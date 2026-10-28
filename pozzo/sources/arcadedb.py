"""ArcadeDB (adb.arcadeitalia.net) per piattaforme arcade - query per nome MAME, in BATCH.

L'originale faceva 1 richiesta per gioco con 1 thread. L'API accetta piu' nomi separati da ';'
quindi qui si chiedono 25 giochi per richiesta (1 richiesta/s, come richiesto da ArcadeDB).
"""

from __future__ import annotations

import asyncio

from ..net import RateLimiter, request
from .base import Result, Source

API = "https://adb.arcadeitalia.net/service_scraper.php"
BATCH = 25


class ArcadeDB(Source):
    name = "arcadedb"
    provides = {"title", "description", "developer", "publisher", "genres", "release_date", "players",
                "media:cover", "media:screenshot", "media:title", "media:marquee", "media:video"}

    def __init__(self, ctx):
        super().__init__(ctx)
        self.limiter = RateLimiter(1.0, 1)
        self.media_limiter = RateLimiter(0, 4)
        self.batch = 100

    def supports(self, platform) -> bool:
        return platform.arcade

    async def _query(self, names: list[str]) -> dict[str, dict]:
        r = await request(self.client, "GET", API, limiter=self.limiter,
                          params={"ajax": "query_mame", "game_name": ";".join(names), "use_parent": 1})
        if r is None or r.status_code != 200:
            raise RuntimeError(f"ArcadeDB HTTP {r.status_code if r else 404}")
        return {g["game_name"]: g for g in r.json().get("result", []) if g.get("game_name")}

    async def lookup_many(self, games):
        batches = [games[i:i + BATCH] for i in range(0, len(games), BATCH)]

        async def run(batch):
            try:
                return batch, await self._query([g.stem.lower() for g in batch])
            except Exception as e:
                return batch, e

        tasks = [asyncio.create_task(run(b)) for b in batches]
        try:
            for fut in asyncio.as_completed(tasks):
                batch, res = await fut
                for g in batch:
                    yield g, (res if isinstance(res, Exception) else self._parse(res.get(g.stem.lower())))
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _parse(self, g: dict | None) -> Result | None:
        if not g or not g.get("title"):
            return None
        f = {
            "title": g["title"], "description": g.get("history"), "developer": g.get("manufacturer"),
            "publisher": g.get("manufacturer"), "release_date": g.get("year"),
            "players": str(g["players"]) if g.get("players") else None,
            "genres": [x.strip() for x in (g.get("genre") or "").split("/") if x.strip()],
        }
        if g.get("youtube_video_id"):
            f["video_url"] = "https://www.youtube.com/watch?v=" + g["youtube_video_id"]
        media = {"cover": g.get("url_image_flyer"), "screenshot": g.get("url_image_ingame"),
                 "title": g.get("url_image_title"), "marquee": g.get("url_image_marquee"),
                 "video": g.get("url_video_shortplay_hd") or g.get("url_video_shortplay")}
        return Result(fields=f, media={k: v for k, v in media.items() if v})
