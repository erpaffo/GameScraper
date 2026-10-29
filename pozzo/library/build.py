"""Costruzione della libreria per il frontend.

Produce `library.db`: un'istantanea compatta e gia' "unita" di tutti i giochi (dati scelti per
priorita' di sorgente, media, file) e delle collezioni automatiche. Il frontend legge solo
questo file all'avvio (pochi ms), senza riscansionare il disco ne' rifare l'unione dei dati.

La ricostruzione gira in background (comando `library` o pulsante "Aggiorna libreria") e
sostituisce il file in modo atomico solo a lavoro finito.

Collezioni automatiche, da tre fonti unite:
  1. libretro: DAT "franchise" (offline, per CRC/nome) per i sistemi a cartuccia
  2. IGDB: franchise/serie; per i giochi gia' scrapati si recuperano in blocchi da 500 per slug
  3. euristica sul titolo: i giochi senza dati di franchise che iniziano col nome di una
     collezione nota ("Pokemon - Versione Smeraldo") vengono aggiunti
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from ..cache import Cache
from ..catalog import CatalogItem, cached_info, entry_stem
from ..matching import normalize
from .. import storemeta, storekind
from ..net import make_client
from ..pipeline import load_cached
from ..scanner import Game, clean_title, find_platform_dirs, scan
from ..sources.base import Context
from .ratings import combine

SCHEMA = """
CREATE TABLE games(id INTEGER PRIMARY KEY, key TEXT UNIQUE, platform TEXT, title TEXT, sort_title TEXT,
                   year INT, release_date TEXT, developer TEXT, publisher TEXT, genres TEXT, players TEXT,
                   rating REAL, description TEXT, region TEXT, found INT, files TEXT, media TEXT,
                   video_url TEXT, canonical TEXT, crc TEXT, origin TEXT, grp TEXT, is_primary INT,
                   store TEXT, installed INT, launch TEXT, install_uri TEXT, page TEXT, store_playtime REAL,
                   install_dir TEXT, rating_count INT, rating_critics REAL, ratings TEXT, size INT, hltb TEXT,
                   store_pages TEXT, content TEXT);
CREATE TABLE collections(slug TEXT PRIMARY KEY, name TEXT);
CREATE INDEX games_grp ON games(grp);
CREATE TABLE collection_games(slug TEXT, key TEXT, PRIMARY KEY(slug, key));
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
"""

# stessi franchise con nomi diversi tra libretro e IGDB
DEFAULT_ALIASES = {"Sonic": "Sonic The Hedgehog", "Mario Bros.": "Mario", "Super Mario": "Mario",
                   "Legend of Zelda": "The Legend of Zelda", "Zelda": "The Legend of Zelda",
                   "Pokemon": "Pokémon", "Final Fantasy series": "Final Fantasy"}
from ..pipeline import BAD_DUMP  # noqa: E402

DEFAULT_EXCLUDE = ["Famicom Mini", "Classic NES Series", "NES Remix", "Virtual Console", "Nintendo Selects",
                   "Player's Choice", "Greatest Hits", "Platinum", "Essentials"]


def slugify(name: str) -> str:
    return normalize(name).replace(" ", "-")


def sort_title(t: str) -> str:
    t = t.strip()
    for art in ("The ", "A ", "An "):
        if t.startswith(art):
            return t[len(art):].lower()
    return t.lower()


# ---------------------------------------------------------------- franchise

async def _enrich_franchises(cfg, cache: Cache, games: list[Game], log):
    """Riempie in cache i franchise mancanti. Ogni gioco viene elaborato una volta sola."""
    keys = [g.key for g in games]
    res = cache.all_resources(keys)
    done_lr = cache.lookup_status(keys, "libretro_extra")
    done_ig = cache.lookup_status(keys, "igdb_franchise2")
    from ..sources.igdb import Igdb, franchises_of
    from ..sources.libretro import Libretro

    async with make_client() as client:
        ctx = Context(cfg, cache, client, log)

        # 1) libretro, offline: franchise + codice prodotto (serve a raggruppare le versioni regionali)
        todo = [g for g in games if g.key not in done_lr and not g.platform.arcade and g.platform.libretro]
        if todo:
            lr = Libretro(ctx)
            await lr.setup({g.platform for g in todo})
            n = ns = 0
            for g in todo:
                idx = lr.indexes.get(g.platform.id)
                if idx:
                    name = g.canonical or idx.identify(g, cfg.regions)
                    m = idx.meta(g, name) if name else {}
                    if m.get("franchise"):
                        cache.put(g.key, "libretro", "franchises", [m["franchise"]])
                        n += 1
                    if m.get("serial"):
                        cache.put(g.key, "libretro", "serial", m["serial"])
                        ns += 1
                cache.set_lookup(g.key, "libretro_extra", "done")
            cache.commit()
            log(f"Da libretro: franchise per {n:,} giochi, codice prodotto per {ns:,}")

        # 2) IGDB per slug, 500 giochi per richiesta
        slugs: dict[str, list[str]] = defaultdict(list)
        for g in games:
            r = res.get(g.key, {}).get("igdb", {})
            if g.key in done_ig or not r.get("igdb_url"):
                continue
            slugs[r["igdb_url"].rstrip("/").rsplit("/", 1)[-1]].append(g.key)
        if slugs:
            ig = Igdb(ctx)
            if await ig.setup(set()):
                names = list(slugs)
                n = 0
                for i in range(0, len(names), 500):
                    chunk = names[i:i + 500]
                    q = ", ".join(f'"{s}"' for s in chunk)
                    try:
                        data = await ig._post("games", f"fields slug,name,franchise.name,franchises.name,collections.name; "
                                                       f"where slug = ({q}); limit 500;")
                    except Exception as e:
                        log(f"[yellow]IGDB franchise: {e}[/]")
                        break
                    for hit in data:
                        fr = franchises_of(hit)
                        for k in slugs.get(hit.get("slug"), []):
                            cache.db.execute("DELETE FROM resources WHERE key=? AND source='igdb' AND field='franchises'", (k,))
                            if fr:
                                cache.put(k, "igdb", "franchises", fr)
                                n += 1
                    for s in chunk:
                        for k in slugs[s]:
                            cache.set_lookup(k, "igdb_franchise2", "done")
                    cache.commit()
                log(f"Franchise da IGDB: {n:,} giochi ({(len(names) + 499) // 500} richieste)")


def _franchise_map(res: dict, by_key: dict[str, Game]) -> dict[str, set[str]]:
    """Franchise per gioco. IGDB ha la precedenza; libretro (che ha qualche errore, es.
    'Eternal Legend' etichettato Pokemon) si accetta solo se il nome compare nel titolo,
    se IGDB concorda, o se IGDB non conosce il gioco."""
    def load(v):
        try:
            return json.loads(v) if v else []
        except ValueError:
            return []
    out: dict[str, set[str]] = defaultdict(set)
    for k, by_src in res.items():
        ig = set(load(by_src.get("igdb", {}).get("franchises")))
        lr = load(by_src.get("libretro", {}).get("franchises"))
        known_to_igdb = bool(by_src.get("igdb", {}).get("igdb_url"))
        g = by_key.get(k)
        title = normalize((g.data.get("title") or g.search_name) if g else "")
        out[k].update(ig)
        for n in lr:
            if not known_to_igdb or slugify(n) in {slugify(x) for x in ig} or normalize(n) in title:
                out[k].add(n)
    return out


def compute_collections(cfg, games: list[Game], franchises: dict[str, set[str]]) -> dict[str, tuple[str, set[str]]]:
    """slug -> (nome, chiavi dei giochi)."""
    ccfg = getattr(cfg, "collections", {}) or {}
    min_games = int(ccfg.get("min_games", 3))
    exclude = {slugify(x) for x in DEFAULT_EXCLUDE + list(ccfg.get("exclude", []))}
    aliases = {slugify(k): v for k, v in {**DEFAULT_ALIASES, **(ccfg.get("aliases") or {})}.items()}

    spellings: dict[str, Counter] = defaultdict(Counter)
    members: dict[str, set[str]] = defaultdict(set)
    for g in games:
        for name in franchises.get(g.key, ()):
            name = aliases.get(slugify(name), name)
            s = slugify(name)
            if not s or s in exclude:
                continue
            spellings[s][name] += 1
            members[s].add(g.key)

    # euristica sul titolo per i giochi senza alcun dato di franchise
    known = sorted((s for s, m in members.items() if len(m) >= min_games and len(s) >= 4),
                   key=len, reverse=True)
    known_words = [(s, s.replace("-", " ")) for s in known]
    for g in games:
        if franchises.get(g.key):
            continue
        t = normalize(g.data.get("title") or g.search_name)
        for s, words in known_words:
            if t == words or t.startswith(words + " "):
                members[s].add(g.key)
                break

    out = {}
    for s, keys in members.items():
        if len(keys) >= min_games:
            out[s] = (spellings[s].most_common(1)[0][0], keys)
    return out


# ---------------------------------------------------------------- versioni regionali

def _regional_groups(cfg, merged: dict[str, Game], res: dict) -> tuple[dict[str, str], set[str]]:
    """Raggruppa le versioni regionali dello stesso gioco (es. Pokemon Rubino / Ruby / Rubin).
    Identita': stesso gioco IGDB sulla stessa console, altrimenti stesso titolo normalizzato.
    Ritorna (chiave -> gruppo, chiavi delle versioni principali)."""
    from ..scanner import REGION_TAGS, regions_of
    lang_regions = [l for l in cfg.languages if l in REGION_TAGS.values()]
    pref = lang_regions + [r for r in cfg.regions if r not in lang_regions]

    def score(g: Game):
        rs = regions_of(g.stem) or (str(g.data.get("region") or "").split(","))
        reg = min((pref.index(r) for r in rs if r in pref), default=len(pref))
        bad = 1 if BAD_DUMP.search(g.stem) else 0
        media = 0 if g.data.get("media:cover") else 1
        return (bad, reg, 0 if "title" in g.origin else 1, media, len(g.stem))

    # union-find: due versioni sono lo stesso gioco se condividono codice prodotto, gioco IGDB o
    # gioco ScreenScraper (anche a catena). Il titolo normalizzato si usa solo se manca tutto il resto.
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for key, g in merged.items():
        r = res.get(key, {})
        ids = []
        core = serial_core(r.get("libretro", {}).get("serial", "")) if g.platform.id in SERIAL_PLATFORMS else ""
        if core:
            ids.append("s:" + core)
        ig = r.get("igdb", {})
        slug = ig.get("igdb_url", "").rstrip("/").rsplit("/", 1)[-1] if ig.get("igdb_url") else ""
        if slug:
            ids.append("i:" + slug)
        if r.get("screenscraper", {}).get("ss_id"):
            ids.append("ss:" + r["screenscraper"]["ss_id"])
        if not ids:
            ids.append("t:" + normalize(g.data.get("title") or g.search_name))
        # stesso titolo del file sulla stessa console = stesso gioco (le copie non vengono scrapate
        # e prendono i metadati della versione principale)
        if not g.stem.startswith("[BIOS]") and normalize(g.search_name):
            ids.append("f:" + normalize(g.search_name))
        for i in ids:
            union("k:" + key, f"{g.platform.id}/{i}")

    groups: dict[str, str] = {}
    members: dict[str, list[str]] = defaultdict(list)
    for key in merged:
        grp = find("k:" + key)
        groups[key] = grp
        members[grp].append(key)
    primary = {min(keys, key=lambda k: score(merged[k])) for keys in members.values()}
    return groups, primary


# solo console con codici Nintendo "XXXr" (r = regione). NON PlayStation (SLES-xxxxx), Sega, ecc.
SERIAL_PLATFORMS = {"gb", "gbc", "gba", "n64", "snes", "nds", "3ds", "gc", "wii", "wiiu", "virtualboy"}
_SERIAL_PREFIX = {"SHVC", "SNSP", "SNSN", "SNS", "DMG", "CGB", "AGB", "NUS", "NTR", "TWL", "CTR", "KTR", "DOL",
                  "RVL", "WUP", "HAC", "USA", "EUR", "JPN", "NOE", "FRA", "ESP", "ITA", "GER", "AUS", "KOR", "CHN",
                  "UKV", "FAH", "HOL", "SCN", "SWE", "DL", "B0", "B1", "B2"}


def serial_core(serial: str) -> str:
    """Codice gioco senza la lettera di regione: 'AGB-AXVJ-JPN' / 'AGB-AXVP-EUR' -> 'AXV'.
    Vale per i codici Nintendo a 4 caratteri (GBA, N64, SNES, NDS, 3DS, GameCube, Wii)."""
    for tok in str(serial or "").upper().split("-"):
        # "AGBJ", "NTRJ"... sono codici segnaposto (homebrew, demo): non identificano un gioco
        if len(tok) == 4 and tok.isalnum() and tok not in _SERIAL_PREFIX and tok[:3] not in _SERIAL_PREFIX                 and tok[-1].isalpha():
            return tok[:3]
    return ""


# ---------------------------------------------------------------- giochi PC dagli store

APP_GENRE = "Applicazione"
# DLC/add-on, colonne sonore e aggiornamenti non si scartano più: il tipo (vedi storekind.content_kind)
# finisce nella colonna `content`, cosi' si possono mostrare, filtrare e nascondere.


def _store_entries(cfg, cache) -> list[dict]:
    """Giochi degli store (istantanee in cache) come voci della piattaforma 'windows'.
    Lo stesso gioco su piu' store (es. Steam + Epic) diventa un gruppo con piu' versioni:
    principale quella installata, poi nell'ordine degli store."""
    from types import SimpleNamespace

    from ..stores import STORE_ORDER, load_snapshots
    entries = []
    for s in load_snapshots(cache):
        title = s.get("title") or s["id"]
        if not s.get("installed") and re.fullmatch(r"[0-9a-fA-F]{16,}", title):
            continue  # DLC senza scheda (titolo = codice interno dello store)
        # giochi aggiunti a mano e Minecraft non si classificano: sono giochi per definizione
        content = "game" if s["store"] in ("manual", "minecraft") else storekind.content_kind(title, s.get("kind") or "")
        rd = str(s.get("release_date") or "")
        entries.append({
            "key": f"windows/{s['store']}:{s['id']}", "title": title, "sort": sort_title(title),
            "year": int(rd[:4]) if re.match(r"^\d{4}", rd) else None, "release_date": rd or None,
            "developer": s.get("developer") or None, "publisher": s.get("publisher") or None,
            "genres": s.get("genres") or [], "description": s.get("description") or None,
            "media": {("cover" if k == "cover_wide" else k): v for k, v in (s.get("media") or {}).items()
                      if k != "cover" or True},
            "files": [s["install_dir"]] if s.get("install_dir") else [],
            "store": s["store"], "installed": int(bool(s.get("installed"))), "launch": s.get("launch") or {},
            "install_uri": s.get("install_uri") or None, "page": s.get("page") or None,
            "store_playtime": s.get("playtime") or 0, "install_dir": s.get("install_dir") or None,
            "size": s.get("size") or None, "kind": s.get("kind") or "game", "content": content,
            "grp": "windows/t:" + normalize(title),
        })
    # metadati SOLO dalle fonti offline (LaunchBox/TheGamesDB), cercati col pulsante "Metadati store":
    # descrizioni/generi/valutazioni forniti online dagli store non si usano (tranne i dati fissi di Minecraft, Battle.net e Riot)
    from .ratings import combine
    for e in entries:
        if e["store"] not in ("minecraft", "battlenet", "riot"):
            e.update(description=None, developer=None, publisher=None, genres=[], release_date=None, year=None)
        m = cache.kv_get("storemeta:" + e["key"]) or {}
        e["players"] = str(m["players"]) if m.get("players") else None
        for k in ("description", "developer", "publisher", "genres", "release_date"):
            if m.get(k) and not e[k]:
                e[k] = m[k]
        if e["release_date"] and not e["year"] and re.match(r"^\d{4}", e["release_date"]):
            e["year"] = int(e["release_date"][:4])
        e["ratings"] = combine({"launchbox": m["rating"]} if m.get("rating") else {})
        if e.pop("kind", "game") == "app":  # le applicazioni si riconoscono dal genere (nessuna colonna in piu')
            e["genres"] = [APP_GENRE]
    order = {sid: i for i, sid in enumerate(STORE_ORDER)}
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        groups[e["grp"]].append(e)
    for members in groups.values():
        best = min(members, key=lambda e: (not e["installed"], order.get(e["store"], 99)))
        for e in members:
            e["primary"] = int(e is best)
            # oggetto minimo per il calcolo delle collezioni (euristica sul titolo)
            e["obj"] = SimpleNamespace(key=e["key"], data={"title": e["title"]}, search_name=e["title"])
    return entries


# ---------------------------------------------------------------- cataloghi online (store per console)

def _platform_exts(pid: str) -> set[str]:
    """Estensioni dei file della console (entry_stem aggiunge gli archivi piu' comuni)."""
    from .. import platforms as P
    return set(getattr(P.PLATFORMS.get(pid), "exts", ()) or ())


# rumore dei siti di ROM in fondo al titolo: "Gioco Switch NSP (eShop)", "Gioco XCI", "Gioco - NSP"
_STORE_NOISE = re.compile(r"\s*(?:[-–|:]\s*)?(?:\bswitch\s+)?\b(?:nsp|xci|eshop|nsz)\b.*$", re.I | re.S)
_STORE_VERSION = re.compile(r"\s+v\d+(?:\.\d+)*$", re.I)
# console in fondo al titolo (superpsx e simili): "Gioco PS4", "Gioco PS2-PS4 Ported", "Gioco PS5 FPKG"
_STORE_CONSOLE = re.compile(r"\s+\(?ps[1-5](?:\s*[-/+&]\s*ps[1-5])*\)?(?:\s+(?:ported|port|fpkg|pkg))*\s*$", re.I)


@lru_cache(maxsize=300_000)  # stesse voci a ogni ricostruzione della libreria
def _store_title(stem: str) -> str:
    """Titolo leggibile di una voce online: via «Switch NSP (eShop)» e la versione finale.

    Il nome originale resta in `name` (serve per peso, deduplica e file); cambia solo il titolo mostrato.
    Non tocca titoli che contengono «Switch» come parola vera (es. «1-2-Switch», «Nintendo Switch Sports»).
    """
    base = _STORE_NOISE.sub("", stem).strip(" -–|:")
    base = _STORE_VERSION.sub("", base).strip()
    base = _STORE_CONSOLE.sub("", base).strip() or base
    return clean_title(base) or base or stem


def _catalog_to_store_game(item: CatalogItem, console_pid: str, meta: dict | None = None) -> dict:
    """Converte una voce di catalogo (catalog.py) nel gioco "da scaricare" della libreria.

    `console_pid` e' la console a cui appartiene lo store (es. "nds"): serve per la chiave,
    il gruppo delle versioni e il filtro per piattaforma. `meta` sono i dati della pagina (storemeta):
    per Switch il Title ID dice se la voce e' il gioco, un aggiornamento o una DLC.
    """
    from ..scanner import clean_title, regions_of

    name = item["name"]
    stem = entry_stem(item, _platform_exts(console_pid))
    title = _store_title(stem)
    content = ""
    if console_pid == "switch" and meta:
        content = storekind.switch_kind(meta.get("title_id") or "")
    content = content or storekind.content_kind(title)
    return {
        "key": f"{console_pid}/net:{hashlib.sha1(item['url'].encode()).hexdigest()[:16]}",
        "pid": console_pid,
        "name": name,
        "title": title,
        "content": content,
        "url": item["url"],
        "magnet": item.get("magnet"),
        "size": item.get("size"),
        "region": ",".join(regions_of(stem)),
        "year": None,
        "release_date": None,
        "developer": None,
        "publisher": None,
        "genres": [],
        "players": None,
        "description": None,
        "media": {},
        "ratings": {},
        "grp": f"{console_pid}/net:" + normalize(title),
    }


def apply_store_meta(e: dict, meta: dict) -> None:
    """Copertina e descrizione lette dalla pagina dello store (o da GameTDB): riempie solo i vuoti.
    La usa anche il server per mostrare i dati appena trovati, senza ricostruire la libreria."""
    if not meta:
        return
    if not e["description"]:
        e["description"] = meta.get("description")
    if not e["developer"]:
        e["developer"] = meta.get("developer")
    if not e["publisher"]:
        e["publisher"] = meta.get("publisher")
    if not e["genres"]:
        e["genres"] = list(meta.get("genres") or [])
    if not e["players"] and meta.get("players"):
        e["players"] = str(meta["players"])
    if not e["release_date"] and meta.get("release_date"):
        e["release_date"] = meta["release_date"]
        e["year"] = int(meta["release_date"][:4]) if meta["release_date"][:4].isdigit() else e["year"]
    if not e["size"] and meta.get("size"):
        e["size"] = meta["size"]
    covers = meta.get("covers") or ([meta["cover"]] if meta.get("cover") else [])
    if covers and "cover" not in e["media"]:
        # piu' indirizzi separati da "|": il server prova il primo che risponde
        e["media"]["cover"] = "url:" + "|".join(covers[:3])
    shots = meta.get("screenshots") or []
    if shots and "screenshot" not in e["media"]:
        e["media"]["screenshot"] = "url:" + shots[0]


def _catalog_entries(cfg, cache, merged: dict) -> tuple[list[dict], dict]:
    """Voci lette dalle pagine degli store online (esclusi i giochi gia' posseduti) e, per i giochi
    posseduti che compaiono negli elenchi, i link alla pagina dello store: (voci, {chiave: fonti}).
    Dati e immagini dal database LaunchBox (offline): le immagini si scaricano solo quando servono."""
    from .. import platforms as P
    from ..bulk import LaunchBoxDB
    from ..scanner import REGION_TAGS, clean_title
    # giochi gia' in libreria: stesso nome file, stesso titolo (anche in un'altra regione/formato,
    # es. "X (Europe).rvz" contro "X (USA).zip") o stesso gioco LaunchBox
    def tkey(t: str, pid: str) -> str:
        """Titolo per il confronto, senza il nome della console in fondo ("Party Megamix Wii")."""
        n = normalize(t)
        for suf in {pid, "wii", "ds", "gc", "psp", "gba", "switch"}:
            if n.endswith(" " + suf):
                return n[: -len(suf) - 1]
        return n

    lb = LaunchBoxDB(cfg.cache)
    lb_ready = lb.ready()  # una volta sola: e' un accesso al disco, per voce costava ~10 s su 150.000 voci
    pc_stores = [x for x in (getattr(cfg, "pc_stores", None) or []) if x.get("url")]
    with_cat = set(getattr(cfg, "catalogs", {}) or {}) | ({"windows"} if pc_stores else set())
    owned: dict[str, set[str]] = defaultdict(set)
    owned_titles: dict[str, set[str]] = defaultdict(set)
    owned_lb: dict[str, set[int]] = defaultdict(set)
    owned_key: dict[tuple[str, str], str] = {}        # (console, nome file) -> chiave del gioco
    owned_title_key: dict[tuple[str, str], str] = {}  # (console, titolo confrontabile) -> chiave
    owned_lb_key: dict[tuple[str, int], str] = {}     # (console, id LaunchBox) -> chiave
    for g in merged.values():
        pid = g.platform.id
        owned[pid].add(g.stem.lower())
        owned_key[(pid, g.stem.lower())] = g.key
        if g.inner_name:
            owned[pid].add(Path(g.inner_name).stem.lower())
            owned_key.setdefault((pid, Path(g.inner_name).stem.lower()), g.key)
        if pid not in with_cat:
            continue
        names = {clean_title(g.stem), str(g.data.get("title") or ""), g.search_name}
        for n in (tkey(x, pid) for x in names):
            if n:
                owned_titles[pid].add(n)
                owned_title_key.setdefault((pid, n), g.key)
        if lb_ready:
            r = lb.find(pid, [t for t in names if t], files=[g.stem], fuzzy=0)
            if r:
                owned_lb[pid].add(r["id"])
                owned_lb_key.setdefault((pid, r["id"]), g.key)
    lang_regions = [l for l in cfg.languages if l in REGION_TAGS.values()]
    pref = lang_regions + [r for r in cfg.regions if r not in lang_regions]
    out, seen = [], set()
    by_title: dict[tuple[str, str], dict] = {}  # (console, titolo normalizzato) -> gioco gia' creato
    owned_pages: dict[str, list[dict]] = defaultdict(list)  # chiave gioco -> pagine dello store
    updates: list[dict] = []  # aggiornamenti: diventano un download del gioco base, non una voce a parte

    def remember_owned(key: str | None, source: dict) -> None:
        """Il gioco e' gia' in libreria: si tiene il link allo store per la sua scheda."""
        if not key:
            return
        if source["url"] not in {s["url"] for s in owned_pages[key]}:
            owned_pages[key].append(source)

    sources_list: list[tuple[str, str, str | None]] = [
        (pid, url, None) for pid, urls in (getattr(cfg, "catalogs", {}) or {}).items()
        if pid in P.PLATFORMS for url in (urls or [])]
    # store PC non ufficiali: giochi per Windows, con la categoria (repack / download diretto)
    sources_list += [("windows", x["url"], x.get("kind")) for x in pc_stores]
    for pid, url, kind in sources_list:
        info = cached_info(cache, pid, url)
        is_file_list = info["type"] == "filelist"
        for it in info["entries"]:
            stem = entry_stem(it, set(P.PLATFORMS[pid].exts))
            source = {"site": (urlparse(it["url"]).hostname or "").removeprefix("www."),
                      "url": it["url"], "magnet": it.get("magnet"), "name": it["name"]}
            if kind:
                source["kind"] = kind  # repack / download diretto (solo store PC)
            # stesso URL ripetuto = doppione; nomi uguali da siti diversi sono fonti diverse
            if stem.lower() in owned[pid] or (pid, it["url"]) in seen or stem.startswith("[BIOS]"):
                if stem.lower() in owned[pid]:  # gia' scaricato: si conserva il link nella sua scheda
                    remember_owned(owned_key.get((pid, stem.lower())), source)
                continue
            seen.add((pid, it["url"]))
            title = _store_title(stem)
            tk = tkey(title, pid)
            meta = {} if is_file_list else storemeta.cached(cache, it["url"])
            content = (storekind.switch_kind(meta.get("title_id")) if pid == "switch" else "") \
                or storekind.content_kind(title)
            if content == "update":
                base = storekind.update_base(title)
                updates.append({"pid": pid, "base": normalize(base), "tk": tkey(base, pid),
                                "source": {**source, "update": True,
                                           "version": storekind.update_version(it["name"] or stem)}})
                continue
            if tk in owned_titles[pid]:
                remember_owned(owned_title_key.get((pid, tk)), source)
                continue
            # stesso gioco da piu' fonti (es. nxbrew + ziperto): una sola voce con piu' link
            key = (pid, normalize(title))
            existing = by_title.get(key)
            if existing is not None:
                if it["url"] not in {s["url"] for s in existing["sources"]}:
                    existing["sources"].append(source)
                if not existing["magnet"] and it.get("magnet"):
                    existing["magnet"] = it["magnet"]
                if not is_file_list:
                    apply_store_meta(existing, meta)
                continue
            e = _catalog_to_store_game(it, pid, meta)
            if kind:
                e["kind"] = kind
            if lb_ready:
                r = lb.find(pid, [title], files=[stem], fuzzy=0)
                if r and r["id"] in owned_lb[pid]:
                    remember_owned(owned_lb_key.get((pid, r["id"])), source)
                    continue  # stesso gioco gia' posseduto (titolo diverso)
                if r:
                    e.update({"description": r["overview"], "developer": r["developer"], "publisher": r["publisher"],
                              "genres": [x.strip() for x in (r["genres"] or "").split(";") if x.strip()],
                              "players": r["players"], "release_date": r["release"],
                              "year": int(r["release"][:4]) if r["release"] and r["release"][:4].isdigit() else None})
                    if r["rating"] is not None and r["rating_count"]:
                        e["ratings"] = combine({"launchbox": {"rating": r["rating"] / 5, "rating_count": r["rating_count"]}})
                    e["grp"] = f"{pid}/net:lb{r['id']}"  # stesso gioco in altre regioni = stesso gruppo
                    imgs = lb.images(r["id"], list(cfg.regions))
                    e["media"] = {k2: "url:" + v for k2, v in imgs.items() if k2 in ("cover", "screenshot", "wheel", "fanart")}
            # copertina e descrizione lette dalla pagina dello store (o da GameTDB): riempiono i vuoti.
            # Solo per le schede di gioco (type_a/type_b/type_c): i file hanno gia' i loro metadati.
            if not is_file_list:
                apply_store_meta(e, meta)
            e["sources"] = [source]
            by_title[key] = e
            out.append(e)
    lb.close()
    _attach_updates(updates, out, owned_title_key, owned_pages)
    _dlc_by_size(out)
    _apply_online(cache, out)
    # una voce principale per gioco (le altre regioni restano tra le "versioni")
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in out:
        groups[e["grp"]].append(e)
    for members in groups.values():
        def rank(e):
            rs = e["region"].split(",") if e["region"] else []
            return (1 if BAD_DUMP.search(e["name"]) else 0, min((pref.index(r) for r in rs if r in pref), default=len(pref)))
        best = min(members, key=rank)
        for e in members:
            e["primary"] = int(e is best)
    return out, owned_pages


def _attach_updates(updates: list[dict], entries: list[dict], owned_title_key: dict,
                    owned_pages: dict) -> int:
    """Gli aggiornamenti non sono giochi: diventano un download in piu' («⬆ Update») del gioco base,
    se questo e' nello stesso elenco o gia' posseduto. Senza gioco base il link non serve e si scarta."""
    by = {(e["pid"], normalize(e["title"])): e for e in entries}
    n = 0
    for u in updates:
        base = by.get((u["pid"], u["base"]))
        if base is not None:
            base["sources"].append(u["source"])
            n += 1
            continue
        key = owned_title_key.get((u["pid"], u["tk"]))
        if key and u["source"]["url"] not in {s["url"] for s in owned_pages[key]}:
            owned_pages[key].append(u["source"])
            n += 1
    return n


def _dlc_by_size(entries: list[dict]) -> int:
    """«Gioco - Qualcosa» che pesa meno di un terzo del gioco omonimo: quasi sempre una DLC senza le
    parole chiave nel nome (nei cataloghi console il tipo non esiste). Regola di conferma, mai da sola:
    serve il gioco base nella stessa lista."""
    base: dict[tuple[str, str], int] = {}
    for e in entries:
        if e["content"] == "game" and e.get("size"):
            base.setdefault((e["pid"], normalize(e["title"])), e["size"])
    n = 0
    for e in entries:
        if e["content"] != "game" or not e.get("size"):
            continue
        for sep in (" - ", " – ", ":"):
            i = e["title"].find(sep)
            if i <= 0:
                continue
            b = base.get((e["pid"], normalize(e["title"][:i])))
            if b and e["size"] < b * storekind.SIZE_RATIO:
                e["content"] = "dlc"
                n += 1
                break
    return n


def _apply_online(cache, entries: list[dict]) -> None:
    """Voti e dati delle fonti online (IGDB, RAWG) per le voci da scaricare, cercati da «Migliori giochi»
    (topgames.py): il voto unisce LaunchBox e le fonti online; gli altri campi riempiono solo i vuoti."""
    res = cache.all_resources([e["key"] for e in entries])
    if not res:
        return

    def val(v):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return v

    for e in entries:
        per = res.get(e["key"])
        if not per:
            continue
        rs = {src: {f: val(v) for f, v in d.items()} for src, d in per.items()}
        lb_r = {"launchbox": {"rating": r["rating"], "rating_count": r["count"]}
                for r in (e["ratings"] or {}).get("ratings", []) if r.get("source") == "launchbox"}
        e["ratings"] = combine({**lb_r, **rs})
        for d in rs.values():
            for f in ("description", "developer", "publisher", "release_date", "players"):
                if not e.get(f) and d.get(f):
                    e[f] = d[f] if f != "players" else str(d[f])
            if not e["genres"] and d.get("genres"):
                e["genres"] = list(d["genres"])
            if not e["year"] and str(d.get("release_date") or "")[:4].isdigit():
                e["year"] = int(str(d["release_date"])[:4])
            for mtype in ("cover", "screenshot"):
                if mtype not in e["media"] and d.get(f"url:{mtype}"):
                    e["media"][mtype] = "url:" + d[f"url:{mtype}"]


def _attach_hltb(db, cache):
    """Durate di HowLongToBeat (in cache) per gruppo: tutte le versioni prendono quella della principale."""
    from ..hltb import cache_key
    upd = []
    for grp, title, pid in db.execute("SELECT grp, title, platform FROM games WHERE is_primary=1").fetchall():
        h = cache.kv_get(cache_key(title or "", pid))
        if h and (h.get("main") or h.get("extra") or h.get("complete")):
            upd.append((json.dumps({k: h.get(k) for k in ("main", "extra", "complete", "url")}), grp))
    db.executemany("UPDATE games SET hltb=? WHERE grp=?", upd)


# ---------------------------------------------------------------- metadati condivisi tra le versioni

SHARED_FIELDS = ("description", "developer", "publisher", "genres", "release_date", "players", "ages", "video_url",
                 "rating")


def _share_metadata(merged: dict, groups: dict, primary: set, res: dict) -> dict:
    """Le versioni dello stesso gioco (regioni, revisioni) usano gli STESSI metadati: quelli della
    versione principale, completati con i dati che solo altre versioni hanno.
    Cosi' le versioni secondarie non vanno scrapate e sono comunque complete. Ritorna i voti combinati."""
    members: dict[str, list[str]] = defaultdict(list)
    for k in merged:
        members[groups[k]].append(k)
    ratings = {k: combine(res.get(k, {})) for k in merged}
    for keys in members.values():
        if len(keys) < 2:
            continue
        order = sorted(keys, key=lambda k: k not in primary)  # prima la principale
        media = {}
        for k in order:
            for f, v in merged[k].data.items():
                if f.startswith("media:") and v and f not in media:
                    media[f] = v
        best_rating = next((ratings[k] for k in order if ratings[k].get("rating") is not None), {})
        # valori del gioco: quelli della versione principale, completati dalle altre versioni
        shared = {f: next((merged[o].data.get(f) for o in order if merged[o].data.get(f)), None) for f in SHARED_FIELDS}
        for k in keys:
            d = merged[k].data
            for f, v in shared.items():
                if v:
                    d[f] = v
            d.update(media)  # stesse immagini per tutte le versioni
            if best_rating:
                ratings[k] = best_rating
    return ratings


# ---------------------------------------------------------------- build

INSERT_GAME = ("INSERT INTO games(key, platform, title, sort_title, year, release_date, developer, publisher,"
               " genres, players, rating, description, region, found, files, media, video_url, canonical,"
               " crc, origin, grp, is_primary, store, installed, launch, install_uri, page, store_playtime,"
               " install_dir, rating_count, rating_critics, ratings, size, store_pages, content) VALUES("
               + ",".join("?" * 35) + ")")


def _pc_row(e: dict) -> tuple:
    rc = e["ratings"]
    return (e["key"], "windows", e["title"], e["sort"], e["year"], e["release_date"], e["developer"],
            e["publisher"], json.dumps(e["genres"], ensure_ascii=False), e.get("players"), rc.get("rating"), e["description"],
            None, 1, json.dumps(e["files"], ensure_ascii=False), json.dumps(e["media"]), None, None, None,
            json.dumps({"title": e["store"]}), e["grp"], e["primary"],
            e["store"], e["installed"], json.dumps(e["launch"]), e["install_uri"], e["page"],
            e["store_playtime"], e["install_dir"], rc.get("rating_count"), rc.get("rating_critics"),
            json.dumps(rc.get("ratings", [])), e["size"], "[]", e["content"])


def update_manual(cfg) -> bool:
    """Aggiorna SOLO i giochi aggiunti a mano nella libreria gia' costruita (una frazione di secondo): niente
    nuova scansione delle ROM. Le collezioni automatiche si aggiornano alla prossima ricostruzione completa.
    False se la libreria non esiste ancora (serve build())."""
    dest = Path(cfg.cache) / "library.db"
    if not dest.exists():
        return False
    cache = Cache(cfg.cache)
    try:
        pc = _store_entries(cfg, cache)
    finally:
        cache.close()
    db = sqlite3.connect(dest)
    try:
        db.execute("DELETE FROM collection_games WHERE key IN (SELECT key FROM games WHERE store='manual')")
        db.execute("DELETE FROM games WHERE store='manual'")
        db.executemany(INSERT_GAME, [_pc_row(e) for e in pc if e["store"] == "manual"])
        # un gioco a mano con lo stesso titolo di uno store puo' diventare (o smettere di essere) la versione principale
        db.executemany("UPDATE games SET is_primary=? WHERE key=?", [(e["primary"], e["key"]) for e in pc])
        # il server e l'interfaccia riconoscono una libreria cambiata dall'orario di costruzione: senza, mostrano la vecchia lista
        db.execute("INSERT OR REPLACE INTO meta VALUES('built_at', ?)", (str(time.time()),))
        db.execute("INSERT OR REPLACE INTO meta VALUES('games', (SELECT COUNT(*) FROM games))")
        db.commit()
    finally:
        db.close()
    return True


def build(cfg, log=print, progress=None) -> dict:
    t0 = time.time()
    cache = Cache(cfg.cache)
    try:
        from ..config import rom_roots
        if any(r.is_dir() for r in rom_roots(cfg)):
            targets = find_platform_dirs(cfg)
        else:
            targets = []
            log("Nessuna cartella ROM impostata: la libreria contiene solo i giochi degli store")
        games = scan(cfg, cache, targets, progress)
        load_cached(cfg, cache, games)
        try:
            asyncio.run(_enrich_franchises(cfg, cache, games, log))
        except Exception as e:  # la libreria si costruisce comunque, senza i franchise nuovi
            log(f"[yellow]Franchise non aggiornati: {type(e).__name__}: {e}[/]")

        keys = [g.key for g in games]
        res = cache.all_resources(keys)
        franchises = _franchise_map(res, {g.key: g for g in games})

        # stesso gioco presente piu' volte (es. cartella HITS + set completo) = una voce con piu' file
        merged: dict[str, Game] = {}
        files: dict[str, list[str]] = defaultdict(list)
        for g in games:
            if g.stem.startswith("[BIOS]") and not getattr(cfg, "collections", {}).get("show_bios"):
                continue
            merged.setdefault(g.key, g)
            files[g.key].append(str(g.path))
        groups, primary = _regional_groups(cfg, merged, res)
        shared_ratings = _share_metadata(merged, groups, primary, res)

        pc = _store_entries(cfg, cache)
        net, owned_pages = _catalog_entries(cfg, cache, merged)
        colls = compute_collections(cfg, list(merged.values()) + [e["obj"] for e in pc], franchises)
        from .sizes import game_size

        dest = Path(cfg.cache) / "library.db"
        tmp = dest.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        db = sqlite3.connect(tmp)
        db.executescript(SCHEMA)
        rows = []
        for key, g in merged.items():
            d = g.data
            title = d.get("title") or g.search_name
            rd = str(d.get("release_date") or "")
            year = int(rd[:4]) if re.match(r"^\d{4}", rd) else None
            media = {k.split(":", 1)[1]: v for k, v in d.items() if k.startswith("media:")}
            try:
                rating = float(d["rating"]) if d.get("rating") not in (None, "") else None
            except (TypeError, ValueError):
                rating = None
            comb = shared_ratings.get(key) or {}
            rating = comb.get("rating", rating)
            rows.append((key, g.platform.id, title, sort_title(title), year, rd or None, d.get("developer"),
                         d.get("publisher"), json.dumps(d.get("genres") or [], ensure_ascii=False),
                         str(d.get("players") or "") or None, rating, d.get("description"), d.get("region"),
                         int("title" in g.origin), json.dumps(files[key], ensure_ascii=False),
                         json.dumps(media), d.get("video_url"), g.canonical or None, g.best_crc or None,
                         json.dumps(g.origin), groups[key], int(key in primary),
                         None, None, None, None, None, None, None,
                         comb.get("rating_count"), comb.get("rating_critics"), json.dumps(comb.get("ratings", [])),
                         game_size(g.path, g.is_dir, cache) or None,
                         json.dumps(owned_pages.get(key, []), ensure_ascii=False) if owned_pages else "[]",
                         "game"))
        rows.extend(_pc_row(e) for e in pc)
        for e in net:  # disponibili online (non posseduti)
            rc = e["ratings"]
            rows.append((e["key"], e["pid"], e["title"], sort_title(e["title"]), e["year"], e["release_date"],
                         e["developer"], e["publisher"], json.dumps(e["genres"], ensure_ascii=False), e["players"],
                         rc.get("rating"), e["description"], e["region"], 1, "[]", json.dumps(e["media"]), None, e["name"],
                         None, "{}", e["grp"], e["primary"], "online", 0,
                         json.dumps({"magnet": e["magnet"], "kind": e.get("kind"),
                                     "sources": e.get("sources") or []}),
                         e["url"], e["url"], 0, None, rc.get("rating_count"), rc.get("rating_critics"),
                         json.dumps(rc.get("ratings", [])), e["size"],
                         json.dumps(e.get("sources") or [], ensure_ascii=False), e["content"]))
        db.executemany(INSERT_GAME, rows)
        _attach_hltb(db, cache)
        db.executemany("INSERT INTO collections VALUES(?,?)", [(s, n) for s, (n, _) in colls.items()])
        db.executemany("INSERT INTO collection_games VALUES(?,?)",
                       [(s, k) for s, (_, ks) in colls.items() for k in ks])
        db.executemany("INSERT INTO meta VALUES(?,?)", [("built_at", str(time.time())), ("games", str(len(rows)))])
        db.commit()
        db.close()
        os.replace(tmp, dest)
        stats = {"games": len(rows), "files": len(games), "collections": len(colls),
                 "groups": len(primary) + sum(e["primary"] for e in pc), "pc": len(pc), "online": len(net),
                 "with_franchise": sum(1 for k in merged if franchises.get(k)), "seconds": round(time.time() - t0, 1)}
        log(f"Libreria: {stats['games']:,} voci ({stats['groups']:,} giochi unici, {stats['files']:,} file), {stats['collections']} collezioni "
            f"automatiche, in {stats['seconds']} s")
        return stats
    finally:
        cache.close()
