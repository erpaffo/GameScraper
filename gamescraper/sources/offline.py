"""Fonti basate sui database scaricati in blocco: nessuna richiesta per gioco.

launchbox  valutazioni della community (con numero di voti), descrizioni, generi, ESRB, video e
           immagini (box, screenshot, title, loghi/wheel, fanart, box 3D, marquee)
tgdb       copia completa di TheGamesDB: descrizioni, giocatori, ESRB, copertine
"""

from __future__ import annotations

import re
from pathlib import Path

from ..bulk import LaunchBoxDB, TgdbDB
from ..bulk.launchbox import ALIASES, PLATFORMS as LB_PLATFORMS
from ..net import RateLimiter
from ..scanner import Game, clean_title
from .base import Result, Source


def _titles(g: Game) -> list[str]:
    out = []
    for t in (clean_title(g.canonical) if g.canonical else "", g.data.get("title"), g.search_name):
        if t and t not in out:
            out.append(str(t))
    return out


def _year(g: Game) -> int | None:
    m = re.match(r"^(\d{4})", str(g.data.get("release_date") or ""))
    return int(m.group(1)) if m else None


class LaunchBox(Source):
    name = "launchbox"
    provides = {"title", "description", "developer", "publisher", "genres", "release_date", "players", "rating",
                "media:cover", "media:screenshot", "media:title", "media:wheel", "media:fanart", "media:box3d",
                "media:marquee"}
    concurrency = 64
    offline = True

    def __init__(self, ctx):
        super().__init__(ctx)
        self.db = LaunchBoxDB(self.cfg.cache)
        self.media_limiter = RateLimiter(4, 4)  # CDN di LaunchBox: ritmo moderato (policy launchbox_images)
        self.batch = 256
        self.pids = set(LB_PLATFORMS.values()) | set(ALIASES)

    async def setup(self, platforms) -> bool:
        return await self.db.ensure(self.client, self.log)

    def supports(self, platform) -> bool:
        return platform.id in self.pids

    async def lookup(self, game: Game) -> Result | None:
        files = [game.stem] + ([Path(game.inner_name).stem] if game.inner_name else [])
        r = self.db.find(game.platform.id, _titles(game), files=files, year=_year(game))
        if not r:
            return None
        f = {"title": r["name"], "description": r["overview"], "developer": r["developer"], "publisher": r["publisher"],
             "genres": [x.strip() for x in (r["genres"] or "").split(";") if x.strip()],
             "release_date": r["release"], "players": r["players"], "ages": r["esrb"], "video_url": r["video"],
             "launchbox_id": r["id"]}
        if r["rating"] is not None and r["rating_count"]:
            f["rating"] = round(r["rating"] / 5, 3)
            f["rating_count"] = r["rating_count"]
        res = Result(fields={k: v for k, v in f.items() if v not in (None, "", [])})
        res.media = self.db.images(r["id"], list(self.cfg.regions))
        return res

    async def close(self):
        self.db.close()


class TgdbOffline(Source):
    name = "tgdb"
    provides = {"title", "description", "developer", "publisher", "genres", "release_date", "players", "media:cover"}
    concurrency = 64
    offline = True

    def __init__(self, ctx):
        super().__init__(ctx)
        self.db = TgdbDB(self.cfg.cache)
        self.media_limiter = RateLimiter(4, 4)
        self.batch = 256
        self.names: dict[str, dict] = {}

    async def setup(self, platforms) -> bool:
        if not await self.db.ensure(self.client, self.log):
            return False
        # nomi di generi/sviluppatori/editori: tabelle gia' scaricate dalle API (se disponibili)
        for kind in ("genres", "developers", "publishers"):
            t = self.cache.kv_get(f"tgdb_{kind}") or {}
            self.names[kind] = {str(k): (v.get("name") if isinstance(v, dict) else v) for k, v in t.items()}
        return True

    def supports(self, platform) -> bool:
        return True

    def _names(self, kind: str, raw: str) -> list[str]:
        ids = re.findall(r"\d+", raw or "")
        return [self.names[kind][i] for i in ids if self.names.get(kind, {}).get(i)]

    async def lookup(self, game: Game) -> Result | None:
        r = self.db.find(game.platform.id, _titles(game))
        if not r:
            return None
        f = {"title": r["title"], "description": r["overview"], "release_date": r["release"], "players": r["players"],
             "ages": r["esrb"], "video_url": f"https://www.youtube.com/watch?v={r['youtube']}" if r["youtube"] else None,
             "developer": ", ".join(self._names("developers", r["developers"])),
             "publisher": ", ".join(self._names("publishers", r["publishers"])),
             "genres": self._names("genres", r["genres"])}
        res = Result(fields={k: v for k, v in f.items() if v not in (None, "", [])})
        if r["cover"]:
            res.media = {"cover": r["cover"]}
        return res

    async def close(self):
        self.db.close()
