from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..net import RateLimiter, Stop  # noqa: F401  (re-export per le sorgenti)
from ..scanner import Game


class Skipped(Exception):
    """Gioco non interrogato perche' la sorgente si e' fermata (quota): verra' ripreso al prossimo giro."""


@dataclass
class Result:
    fields: dict = field(default_factory=dict)  # title, description, developer, ...
    media: dict = field(default_factory=dict)  # tipo -> url
    media_headers: dict | None = None


@dataclass
class Context:
    cfg: object
    cache: object
    client: object
    log: object  # callable(str)


class Source:
    name = "base"
    provides: set[str] = set()
    concurrency = 4

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.cfg = ctx.cfg
        self.cache = ctx.cache
        self.client = ctx.client
        self.log = ctx.log
        self.media_limiter = RateLimiter(0, 16)
        self.stopped = False
        self.skip: set[str] = set()  # chiavi di giochi da non mandare a questa sorgente
        self.batch = 0  # giochi per blocco nella pipeline a catena (0 = automatico)
        self._sem: asyncio.Semaphore | None = None

    async def setup(self, platforms: set) -> bool:
        """Prepara la sorgente (login, download database...). False = sorgente disattivata."""
        return True

    def supports(self, platform) -> bool:
        return True

    async def lookup(self, game: Game) -> Result | None:
        raise NotImplementedError

    async def lookup_many(self, games: list[Game]) -> AsyncIterator[tuple[Game, Result | None | Exception]]:
        """Default: `lookup` concorrente. Le sorgenti con API batch (IGDB, ArcadeDB) lo sovrascrivono."""
        if self._sem is None:  # condiviso tra tutti i blocchi: la concorrenza resta quella della sorgente
            self._sem = asyncio.Semaphore(self.concurrency)

        async def one(g: Game):
            async with self._sem:
                if self.stopped:
                    return g, Skipped()
                try:
                    return g, await self.lookup(g)
                except Stop as e:
                    self.stopped = True
                    return g, e
                except Exception as e:  # errore di rete o risposta inattesa: si ritentera' al prossimo giro
                    return g, e

        tasks = [asyncio.create_task(one(g)) for g in games]
        try:
            for fut in asyncio.as_completed(tasks):
                item = await fut
                yield item
                if isinstance(item[1], Stop):
                    break
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        pass


def pick_region(items: list, regions: list[str], key_region="region", key_text="text"):
    """Sceglie da una lista di {region, text} il testo nella regione preferita."""
    if not items:
        return None
    by = {}
    for it in items:
        if isinstance(it, dict) and it.get(key_text):
            by.setdefault(it.get(key_region) or "", it[key_text])
    for r in [*regions, "wor", "ss", "us", "eu", "jp", ""]:
        if r in by:
            return by[r]
    return next(iter(by.values()), None)
