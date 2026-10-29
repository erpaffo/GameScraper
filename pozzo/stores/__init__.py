"""Aggiornamento di tutti gli store PC: elenco giochi (installati e posseduti), immagini, istantanea in cache.

Ogni store salva la propria lista in cache (kv "store:<id>"): se in un secondo momento un login
scade o un client viene disinstallato, i giochi gia' noti restano visibili.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .. import matching
from ..matching import normalize
from ..net import RateLimiter, download_media, make_client, request
from .amazon import Amazon
from .base import StoreGame
from .battlenet import BattleNet
from .epic import Epic
from .gog import Gog
from .manual import Manual
from .minecraft import Minecraft
from .ports import Ports
from .riot import Riot
from .steam import Steam, images as steam_images
from .xbox import Xbox

PROVIDERS = [Steam, Epic, Gog, Amazon, BattleNet, Riot, Xbox, Minecraft, Ports, Manual]
BY_ID = {p.id: p for p in PROVIDERS}
STORE_ORDER = [p.id for p in PROVIDERS]


def enabled(cfg, pid: str) -> bool:
    return (getattr(cfg, "stores", {}) or {}).get(pid, {}).get("enabled", True)


def load_snapshots(cache) -> list[dict]:
    out = []
    for p in PROVIDERS:
        out += cache.kv_get(f"store:{p.id}") or []
    return out


# ---------------------------------------------------------------- immagini

async def _find_art(cfg, cache, client, title: str, limiter, skip_steam: str = "") -> dict:
    """Immagini per un gioco PC senza copertina: Steam (titolo esatto), poi IGDB. Risultato in cache."""
    key = "art:" + normalize(title) + (":nosteam" if skip_steam else "")
    hit = cache.kv_get(key, max_age=90 * 86400)
    if hit:  # trovato: valido 90 giorni
        return hit
    if hit is not None and cache.kv_get(key, max_age=7 * 86400) is not None:
        return hit  # non trovato di recente: si riprova dopo una settimana
    art: dict = {}
    try:
        r = await request(client, "GET", "https://store.steampowered.com/api/storesearch/",
                          params={"term": title, "cc": "IT", "l": "italian"}, limiter=limiter)
        if r is not None and r.status_code == 200:
            hit, _ = matching.best(title, r.json().get("items", []), key=lambda it: it.get("name", ""), min_score=90)
            if hit and str(hit["id"]) != skip_steam:
                art = steam_images(str(hit["id"]))
    except Exception:
        pass
    if not art and cfg.igdb.get("client_id"):
        try:
            from ..sources.base import Context
            from ..sources.igdb import IMG, Igdb
            ig = Igdb(Context(cfg, cache, client, lambda *a: None))
            if await ig.setup(set()):
                q = title.replace('"', '\\"')
                res = await ig._post("games", f'search "{q}"; fields name,cover.image_id,artworks.image_id,'
                                              f'screenshots.image_id; where platforms = (6); limit 10;')
                g, _ = matching.best(title, [x for x in res if x.get("cover")], key=lambda x: x.get("name", ""),
                                     min_score=85)
                if g:
                    art = {"cover": IMG.format(size="t_cover_big_2x", id=g["cover"]["image_id"])}
                    wide = (g.get("artworks") or g.get("screenshots") or [None])[0]
                    if wide:
                        art["hero"] = IMG.format(size="t_1080p", id=wide["image_id"])
        except Exception:
            pass
    cache.kv_set(key, art)
    return art


async def _download_images(cfg, cache, client, games: list[dict], log, progress=None):
    media_dir = Path(cache.media_dir) / "windows"
    lim = RateLimiter(0, 16)
    search_lim = RateLimiter(3, 3)

    # 1) copertine mancanti
    need = [g for g in games if not g["images"].get("cover")]
    if need:
        arts = await asyncio.gather(*(_find_art(cfg, cache, client, g["title"], search_lim) for g in need))
        for g, art in zip(need, arts):
            for k, v in art.items():
                g["images"].setdefault(k, v)

    # 2) download (solo quelli non gia' in cache)
    jobs = []
    for g in games:
        g.setdefault("media", {})
        for kind in ("cover", "hero", "logo", "header", "icon"):
            url = g["images"].get(kind)
            if not url or g["media"].get(kind):
                continue
            safe = f"{g['store']}_{g['id']}".replace(":", "_").replace("/", "_").replace("\\", "_")
            existing = list((media_dir / kind).glob(safe + ".*")) if (media_dir / kind).exists() else []
            if existing:
                g["media"][kind] = existing[0].relative_to(cache.folder).as_posix()
                continue
            jobs.append((g, kind, url, media_dir / kind / safe))
    task = progress.add_task("Immagini giochi PC", total=len(jobs)) if progress and jobs else None

    async def one(g, kind, url, dest):
        p = await download_media(client, url, dest, limiter=lim)
        if p:
            g["media"][kind] = p.relative_to(cache.folder).as_posix()
        if task is not None:
            progress.advance(task)

    await asyncio.gather(*(one(*j) for j in jobs))

    # 3) copertina non scaricabile (es. giochi Steam recenti, con immagini in percorsi diversi):
    #    si riprova su IGDB, e su Steam per i giochi degli altri store
    still = [g for g in games if not g["media"].get("cover")]
    if still:
        arts = await asyncio.gather(*(_find_art(cfg, cache, client, g["title"], search_lim,
                                                skip_steam=g["id"] if g["store"] == "steam" else "")
                                      for g in still))
        retry = []
        for g, art in zip(still, arts):
            safe = f"{g['store']}_{g['id']}".replace(":", "_").replace("/", "_").replace("\\", "_")
            for kind, url in art.items():
                if not g["media"].get(kind) or kind == "cover":
                    retry.append((g, kind, url, media_dir / kind / safe))
        await asyncio.gather(*(one(*j) for j in retry))
    # Steam: alcuni giochi vecchi non hanno la copertina verticale -> si usa la testata
    for g in games:
        if not g["media"].get("cover") and g["media"].get("header"):
            g["media"]["cover_wide"] = g["media"]["header"]


async def refresh(cfg, cache, log=print, only: list[str] | None = None, progress=None, images: bool = True) -> dict:
    """Aggiorna gli store (tutti o solo `only`) e salva le istantanee in cache.
    images=False salta la ricerca e il download delle copertine (aggiunta rapida di giochi a mano)."""
    summary = {}
    async with make_client() as client:
        for P in PROVIDERS:
            if only and P.id not in only:
                continue
            if not enabled(cfg, P.id):
                continue
            prov = P(cfg, cache, client, log)
            try:
                if not prov.detected():
                    if cache.kv_get(f"store:{P.id}"):  # store disinstallato: niente giochi fantasma dall'istantanea vecchia
                        cache.kv_set(f"store:{P.id}", [])
                    continue
                games = [g.to_dict() for g in await prov.fetch()]
            except Exception as e:
                log(f"{P.name}: errore: {type(e).__name__}: {e}")
                continue
            # conserva le immagini gia' scaricate
            old = {g["id"]: g for g in cache.kv_get(f"store:{P.id}") or []}
            for g in games:
                if g["id"] in old:
                    g["media"] = old[g["id"]].get("media", {})
            for g in games:
                g.setdefault("media", {})
            if images:
                await _download_images(cfg, cache, client, games, log, progress)
            cache.kv_set(f"store:{P.id}", games)
            summary[P.id] = {"games": len(games), "installed": sum(1 for g in games if g["installed"])}
    cache.kv_set("stores:refreshed", time.time())
    return summary


def provider(pid: str, cfg, cache, client, log=print):
    return BY_ID[pid](cfg, cache, client, log)
