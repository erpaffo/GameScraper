"""IGDB (Twitch) - ricerca per nome in due passaggi.

NB: l'endpoint /multiquery NON supporta la clausola `search` (risponde sempre lista vuota),
quindi:
  1) passaggio batch: 10 giochi per richiesta /multiquery con filtro sul NOME ESATTO
     (case-insensitive, anche nei nomi alternativi e con " - " <-> ": ") -> copre la
     maggior parte dei giochi identificati da libretro con 1/10 delle richieste
  2) solo per chi non ha trovato corrispondenza: ricerca fuzzy /games con `search`
Limiti IGDB: 4 richieste/s, 8 in volo.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from .. import matching
from ..net import RateLimiter, Stop, request
from ..scanner import Game
from .base import Result, Skipped, Source

API = "https://api.igdb.com/v4/"
IMG = "https://images.igdb.com/igdb/image/upload/{size}/{id}.jpg"
BATCH = 10
FIELDS = ("name,alternative_names.name,summary,first_release_date,genres.name,"
          "involved_companies.company.name,involved_companies.developer,involved_companies.publisher,"
          "total_rating,rating,cover.image_id,screenshots.image_id,artworks.image_id,game_modes.slug,"
          "videos.video_id,url,slug,franchise.name,franchises.name,collections.name,"
          "total_rating_count,aggregated_rating,aggregated_rating_count,rating_count,multiplayer_modes.offlinemax,multiplayer_modes.offlinecoopmax,multiplayer_modes.splitscreen")


def franchises_of(hit: dict) -> list[str]:
    """Franchise IGDB (es. Pokemon, Tomb Raider).

    IGDB nei crossover elenca TUTTI i franchise presenti (Smash Bros. -> Pokemon, F-Zero, Yoshi...):
    si usa il franchise principale se c'e'; con 1-2 franchise si tengono; con di piu' solo quelli
    che compaiono nel titolo, altrimenti la serie ("collections")."""
    from ..matching import normalize
    main = (hit.get("franchise") or {}).get("name")
    names = [f["name"] for f in hit.get("franchises", []) if f.get("name")]
    series = [c["name"] for c in hit.get("collections", []) if c.get("name")]
    if main:
        return [main]
    if len(names) <= 2:
        return names or series[:1]
    title = normalize(hit.get("name") or "")
    in_title = [n for n in names if normalize(n) and normalize(n) in title]
    return in_title or series[:1]


def _esc(s: str) -> str:
    return s.replace("\\", " ").replace('"', '\\"')


def _platform_filter(g: Game) -> str:
    return f"platforms = ({','.join(map(str, g.platform.igdb))})" if g.platform.igdb else ""


class Igdb(Source):
    name = "igdb"
    provides = {"title", "description", "developer", "publisher", "genres", "release_date", "rating", "players",
                "media:cover", "media:screenshot", "media:fanart"}

    def __init__(self, ctx):
        super().__init__(ctx)
        self.client_id = self.cfg.igdb.get("client_id", "")
        self.secret = self.cfg.igdb.get("client_secret", "")
        self.token = None
        self.limiter = RateLimiter(float(self.cfg.igdb.get("rate", 4)), 8)
        self.media_limiter = RateLimiter(0, 24)
        self.search_fallback = bool(self.cfg.igdb.get("search_fallback", True))
        self.batch = 80  # 8 multiquery da 10 per blocco
        self._token_lock = asyncio.Lock()

    def supports(self, platform) -> bool:
        return True  # se l'id piattaforma non e' noto si cerca senza filtro

    async def _get_token(self, force=False):
        async with self._token_lock:
            cached = self.cache.kv_get("igdb_token")
            if cached and not force and cached["exp"] > time.time() + 300:
                self.token = cached["token"]
                return
            r = await request(self.client, "POST", "https://id.twitch.tv/oauth2/token", params={
                "client_id": self.client_id, "client_secret": self.secret, "grant_type": "client_credentials"})
            if r is None or r.status_code != 200:
                raise Stop(f"IGDB: autenticazione Twitch fallita ({r.status_code if r else '404'})")
            d = r.json()
            self.token = d["access_token"]
            self.cache.kv_set("igdb_token", {"token": self.token, "exp": time.time() + d.get("expires_in", 0)})

    async def setup(self, platforms) -> bool:
        if not (self.client_id and self.secret):
            self.log("[yellow]IGDB disattivato: servono client_id e client_secret Twitch.[/]")
            return False
        try:
            await self._get_token()
        except Stop as e:
            self.log(f"[red]{e}[/]")
            return False
        return True

    async def _post(self, endpoint: str, body: str):
        if self.stopped:
            raise Skipped()
        for attempt in range(2):
            r = await request(self.client, "POST", API + endpoint, limiter=self.limiter, content=body,
                              headers={"Client-ID": self.client_id, "Authorization": f"Bearer {self.token}"})
            if r is not None and r.status_code == 401 and attempt == 0:
                await self._get_token(force=True)
                continue
            break
        if r is not None and r.status_code == 429:
            raise RuntimeError("IGDB: troppe richieste (429) anche dopo i tentativi")
        if r is None or r.status_code != 200:
            raise RuntimeError(f"IGDB HTTP {r.status_code if r else 404}: {r.text[:200] if r else ''}")
        return r.json()

    async def _exact_batch(self, games: list[Game]) -> list[tuple[Game, object]]:
        parts = []
        for i, g in enumerate(games):
            n = g.search_name
            variants = {n, n.replace(" - ", ": ")}
            cond = " | ".join(f'name ~ "{_esc(v)}" | alternative_names.name ~ "{_esc(v)}"' for v in variants)
            pf = _platform_filter(g)
            where = f"({cond}) & {pf}" if pf else f"({cond})"
            parts.append(f'query games "{i}" {{ fields {FIELDS}; where {where}; limit 5; }};')
        try:
            data = await self._post("multiquery", "\n".join(parts))
        except Exception as e:
            return [(g, e) for g in games]
        found = [[] for _ in games]
        for q in data:
            found[int(q["name"])] = q.get("result", [])
        return [(g, self._pick(g, res)) for g, res in zip(games, found)]

    async def _search(self, g: Game):
        pf = _platform_filter(g)
        body = f'search "{_esc(g.search_name)}"; fields {FIELDS};{f" where {pf};" if pf else ""} limit 10;'
        try:
            return g, self._pick(g, await self._post("games", body))
        except Exception as e:
            return g, e

    async def lookup_many(self, games):
        batches = []
        by_plat: dict[str, list[Game]] = {}
        for g in games:
            by_plat.setdefault(g.platform.id, []).append(g)
        for lst in by_plat.values():
            batches += [lst[i:i + BATCH] for i in range(0, len(lst), BATCH)]

        exact = [asyncio.create_task(self._exact_batch(b)) for b in batches]
        searches: list[asyncio.Task] = []
        try:
            for fut in asyncio.as_completed(exact):
                for g, res in await fut:
                    if isinstance(res, Stop):
                        self.stopped = True
                    if res is None and self.search_fallback and not self.stopped:
                        searches.append(asyncio.create_task(self._search(g)))
                    else:
                        yield g, res
            for fut in asyncio.as_completed(searches):
                g, res = await fut
                if isinstance(res, Stop):
                    self.stopped = True
                yield g, res
        finally:
            for t in exact + searches:
                t.cancel()
            await asyncio.gather(*exact, *searches, return_exceptions=True)

    def _pick(self, game: Game, results: list) -> Result | None:
        hit, _ = matching.best(game.search_name, results,
                               key=lambda r: [r.get("name"), *(a.get("name") for a in r.get("alternative_names", []))],
                               min_score=self.cfg.min_match)
        if not hit:
            return None
        f = {"title": hit.get("name"), "description": hit.get("summary"), "igdb_url": hit.get("url"),
             "igdb_id": hit.get("id"), "franchises": franchises_of(hit)}
        comps = hit.get("involved_companies", [])
        f["developer"] = ", ".join(c["company"]["name"] for c in comps if c.get("developer") and c.get("company"))
        f["publisher"] = ", ".join(c["company"]["name"] for c in comps if c.get("publisher") and c.get("company"))
        f["genres"] = [g["name"] for g in hit.get("genres", []) if g.get("name")]
        if hit.get("first_release_date"):
            f["release_date"] = datetime.fromtimestamp(hit["first_release_date"], timezone.utc).strftime("%Y-%m-%d")
        rating = hit.get("total_rating") or hit.get("rating")
        if rating:
            f["rating"] = round(rating / 100, 3)
            f["rating_count"] = hit.get("total_rating_count") or hit.get("rating_count") or 0
        if hit.get("aggregated_rating") and hit.get("aggregated_rating_count"):
            f["rating_critics"] = round(hit["aggregated_rating"] / 100, 3)
        modes = {m.get("slug") for m in hit.get("game_modes", [])}
        local = max([m.get("offlinemax") or 0 for m in hit.get("multiplayer_modes", [])]
                    + [m.get("offlinecoopmax") or 0 for m in hit.get("multiplayer_modes", [])] + [0])
        if local >= 2:
            f["players"] = f"1-{local}"  # giocatori in locale, non online
        elif modes == {"single-player"}:
            f["players"] = "1"
        if hit.get("videos"):
            f["video_url"] = "https://www.youtube.com/watch?v=" + hit["videos"][0]["video_id"]
        res = Result(fields=f)
        if hit.get("cover"):
            res.media["cover"] = IMG.format(size="t_cover_big_2x", id=hit["cover"]["image_id"])
        if hit.get("screenshots"):
            res.media["screenshot"] = IMG.format(size="t_screenshot_huge", id=hit["screenshots"][0]["image_id"])
        if hit.get("artworks"):
            res.media["fanart"] = IMG.format(size="t_1080p", id=hit["artworks"][0]["image_id"])
        return res
