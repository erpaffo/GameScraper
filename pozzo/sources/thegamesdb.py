"""TheGamesDB API v1.1 - ricerca per nome. Ha una quota MENSILE: usata come ultima risorsa,
solo per i giochi a cui manca ancora qualcosa dopo le altre sorgenti."""

from __future__ import annotations

from .. import matching
from ..net import RateLimiter, Stop, request
from ..platforms import norm
from ..scanner import Game
from .base import Result, Source

API = "https://api.thegamesdb.net/"


class TheGamesDb(Source):
    name = "thegamesdb"
    provides = {"title", "description", "developer", "publisher", "genres", "release_date", "players", "rating",
                "media:cover", "media:screenshot", "media:wheel", "media:fanart"}
    concurrency = 2

    def __init__(self, ctx):
        super().__init__(ctx)
        self.key = self.cfg.tgdb.get("api_key", "")
        self.limiter = RateLimiter(float(self.cfg.tgdb.get("rate", 2)), 2)
        self.media_limiter = RateLimiter(0, 16)
        self.lookups: dict[str, dict[str, str]] = {}
        self.plat_ids: dict[str, int] = {}
        self.remaining = None

    async def _get(self, path: str, params: dict) -> dict | None:
        if self.remaining is not None and self.remaining <= int(self.cfg.tgdb.get("quota_reserve", 20)):
            raise Stop("quota mensile TheGamesDB esaurita")
        r = await request(self.client, "GET", API + path, limiter=self.limiter, params={"apikey": self.key, **params})
        if r is None:
            return None
        if r.status_code in (401, 403):
            raise Stop(f"TheGamesDB: chiave API non valida o quota esaurita ({r.status_code})")
        d = r.json()
        if "remaining_monthly_allowance" in d:
            self.remaining = int(d["remaining_monthly_allowance"])
        return d

    async def _table(self, kind: str) -> dict[str, str]:
        cached = self.cache.kv_get(f"tgdb_{kind}", max_age=30 * 86400)
        if cached is None:
            d = await self._get(f"v1/{kind.capitalize()}", {})
            items = (d or {}).get("data", {}).get(kind, {})
            cached = {str(k): v for k, v in items.items()}
            self.cache.kv_set(f"tgdb_{kind}", cached)
        return cached

    async def setup(self, platforms) -> bool:
        if not self.key:
            self.log("[yellow]TheGamesDB disattivato: serve 'api_key'.[/]")
            return False
        try:
            for kind in ("genres", "developers", "publishers"):
                self.lookups[kind] = {k: v.get("name", "") for k, v in (await self._table(kind)).items()}
            plats = await self._table("platforms")
        except Stop as e:
            self.log(f"[red]{e}[/]")
            return False
        by_name = {}
        for v in plats.values():
            for n in (v.get("name", ""), v.get("alias", "")):
                if n:
                    by_name[norm(n)] = int(v["id"])
        for p in platforms:
            pid = p.tgdb or next((by_name[norm(n)] for n in (p.name, p.id, *p.aliases) if norm(n) in by_name), None)
            if pid:
                self.plat_ids[p.id] = pid
        if self.remaining is not None:
            self.log(f"TheGamesDB: {self.remaining:,} richieste rimaste questo mese")
        return True

    def supports(self, platform) -> bool:
        return platform.id in self.plat_ids

    def _names(self, kind: str, ids) -> list[str]:
        return [self.lookups[kind].get(str(i), "") for i in ids or [] if self.lookups[kind].get(str(i))]

    async def lookup(self, game: Game) -> Result | None:
        d = await self._get("v1.1/Games/ByGameName", {
            "name": game.search_name, "filter[platform]": self.plat_ids[game.platform.id],
            "fields": "players,publishers,genres,overview,rating", "include": "boxart"})
        games = (d or {}).get("data", {}).get("games", [])
        hit, _ = matching.best(game.search_name, games, key=lambda g: g.get("game_title"), min_score=self.cfg.min_match)
        if not hit:
            return None
        gid = str(hit["id"])
        f = {
            "title": hit.get("game_title"), "description": hit.get("overview"),
            "release_date": hit.get("release_date"), "players": str(hit["players"]) if hit.get("players") else None,
            "developer": ", ".join(self._names("developers", hit.get("developers"))),
            "publisher": ", ".join(self._names("publishers", hit.get("publishers"))),
            "genres": self._names("genres", hit.get("genres")),
        }
        res = Result(fields=f)
        inc = (d.get("include") or {}).get("boxart") or {}
        base = (inc.get("base_url") or {}).get("original", "https://cdn.thegamesdb.net/images/original/")
        for img in (inc.get("data") or {}).get(gid, []):
            if img.get("type") == "boxart" and img.get("side") == "front":
                res.media["cover"] = base + img["filename"]
        want = {"screenshot", "wheel", "fanart"} & set(self.cfg.media)
        if want and not {f"media:{w}" for w in want} <= set(k for k, v in game.data.items() if v):
            imgs = await self._get("v1/Games/Images", {"games_id": gid, "filter[type]": "screenshot,clearlogo,fanart"})
            data = (imgs or {}).get("data", {})
            base = (data.get("base_url") or {}).get("original", base)
            for img in (data.get("images") or {}).get(gid, []):
                t = {"screenshot": "screenshot", "clearlogo": "wheel", "fanart": "fanart"}.get(img.get("type"))
                if t and t not in res.media:
                    res.media[t] = base + img["filename"]
        return res
