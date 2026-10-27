"""Scraping avviato dall'interfaccia: gira in un thread, espone avanzamento e log, si puo' interrompere.

Riusa esattamente la pipeline della riga di comando (scan -> identificazione per nome -> hash
-> fonti a catena), poi ricostruisce la libreria. Le fonti interrogano solo i giochi non ancora
cercati, quindi "Cerca nuovi giochi" su una libreria gia' scrapata richiede pochi secondi.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass, field

from ..cache import Cache
from ..pipeline import scrape
from ..scanner import find_platform_dirs, list_entries, scan


@dataclass
class _Task:
    description: str
    total: float | None = 0
    completed: float = 0
    visible: bool = True


class ProgressSink:
    """Compatibile con le parti di rich.progress.Progress usate da pipeline e scanner."""

    def __init__(self):
        self.tasks: list[_Task] = []
        self.lock = threading.Lock()

    def add_task(self, description, total=0, visible=True, **_):
        with self.lock:
            self.tasks.append(_Task(description.strip(), total, 0, visible))
            return len(self.tasks) - 1

    def advance(self, task_id, advance=1):
        with self.lock:
            self.tasks[task_id].completed += advance

    def update(self, task_id, total=None, visible=None, completed=None, **_):
        with self.lock:
            t = self.tasks[task_id]
            if total is not None:
                t.total = total
            if visible is not None:
                t.visible = visible
            if completed is not None:
                t.completed = completed

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [{"name": t.description, "total": t.total or 0, "done": t.completed}
                    for t in self.tasks if t.visible]


def _plain(s) -> str:
    return re.sub(r"\[/?[a-z ]*\]", "", str(s))


@dataclass
class ScrapeJob:
    platforms: list[str] | None = None
    refresh: bool = False
    retry_missing: bool = False
    keys: list[str] | None = None  # solo questi giochi (pulsante nel dettaglio)
    title: str = ""
    status: str = "idle"  # idle | running | done | cancelled | error
    phase: str = ""
    log: list[str] = field(default_factory=list)
    progress: ProgressSink = field(default_factory=ProgressSink)
    started: float = 0
    finished: float = 0
    _loop: asyncio.AbstractEventLoop | None = None
    _task: asyncio.Task | None = None
    _cancel: bool = False

    def say(self, msg):
        self.log.append(_plain(msg))
        del self.log[:-300]

    def cancel(self):
        self._cancel = True
        if self._loop and self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)

    def state(self) -> dict:
        return {"status": self.status, "phase": self.phase, "log": self.log[-60:], "tasks": self.progress.snapshot(),
                "started": self.started, "finished": self.finished, "platforms": self.platforms, "refresh": self.refresh,
                "retry_missing": self.retry_missing, "keys": self.keys, "title": self.title}

    def run(self, cfg, after_build):
        """Da eseguire in un thread. `after_build(stats)` viene chiamato a libreria ricostruita."""
        from ..library.build import build
        self.status, self.started = "running", time.time()
        cache = None
        try:
            cache = Cache(cfg.cache)
            self.phase = "Scansione cartelle"
            targets = find_platform_dirs(cfg)
            if self.platforms:
                targets = [t for t in targets if t[0].id in self.platforms]
            games = scan(cfg, cache, targets, self.progress)
            if self.keys:
                wanted = set(self.keys)
                for g in games:  # le chiavi dipendono dagli hash: servono quelli gia' in cache
                    if g.needs_hash:
                        g.needs_hash = False
                games = [g for g in games if g.key in wanted]
            self.say(f"{len(games):,} file in {len({g.platform.id for g in games})} console")
            if self._cancel:
                raise asyncio.CancelledError

            self.phase = "Scraping"

            async def main():
                self._loop = asyncio.get_running_loop()
                self._task = asyncio.current_task()
                await scrape(cfg, cache, games, cfg.sources, self.refresh, self.progress, self.say,
                             retry_missing=self.retry_missing)

            asyncio.run(main())
            cache.close()
            cache = None
            if self._cancel:
                raise asyncio.CancelledError
            self.phase = "Aggiornamento libreria"
            stats = build(cfg, log=self.say)
            after_build(stats)
            self.status = "done"
            self.phase = "Completato"
        except (asyncio.CancelledError, KeyboardInterrupt):
            self.status = "cancelled"
            self.phase = "Interrotto: i progressi sono salvati, rilancia per riprendere"
            self.say(self.phase)
        except Exception as e:
            self.status = "error"
            self.phase = f"Errore: {type(e).__name__}: {e}"
            self.say(self.phase)
        finally:
            if cache:
                cache.close()
            self.finished = time.time()


@dataclass
class StoreJob:
    """Aggiornamento degli store PC (elenco giochi + immagini) e poi della libreria."""
    only: list[str] | None = None
    status: str = "idle"
    phase: str = ""
    log: list[str] = field(default_factory=list)
    progress: ProgressSink = field(default_factory=ProgressSink)
    started: float = 0
    finished: float = 0
    summary: dict = field(default_factory=dict)

    def say(self, msg):
        self.log.append(_plain(msg))
        del self.log[:-300]

    def state(self) -> dict:
        return {"status": self.status, "phase": self.phase, "log": self.log[-40:], "tasks": self.progress.snapshot(),
                "summary": self.summary, "started": self.started, "finished": self.finished}

    def run(self, cfg, after_build):
        from .. import stores
        from ..library.build import build
        self.status, self.started = "running", time.time()
        cache = None
        try:
            cache = Cache(cfg.cache)
            self.phase = "Lettura degli store"
            self.summary = asyncio.run(stores.refresh(cfg, cache, log=self.say, only=self.only, progress=self.progress))
            cache.close()
            cache = None
            self.phase = "Aggiornamento libreria"
            after_build(build(cfg, log=self.say))
            self.status, self.phase = "done", "Completato"
        except Exception as e:
            self.status, self.phase = "error", f"Errore: {type(e).__name__}: {e}"
            self.say(self.phase)
        finally:
            if cache:
                cache.close()
            self.finished = time.time()


@dataclass
class StoreMetaJob(StoreJob):
    """Ricerca dei metadati dei giochi degli store nei database offline, poi aggiornamento della libreria."""
    only_missing: bool = False
    stop: bool = False

    def run(self, cfg, after_build):
        from ..library.build import build
        from ..stores import meta
        self.status, self.started = "running", time.time()
        cache = None
        try:
            cache = Cache(cfg.cache)
            self.phase = "Ricerca nei database offline"
            self.summary = meta.search(cfg, cache, log=self.say, progress=self.progress,
                                       only_missing=self.only_missing, cancelled=lambda: self.stop)
            cache.close()
            cache = None
            self.phase = "Aggiornamento libreria"
            after_build(build(cfg, log=self.say))
            self.status = "done"
            self.phase = f"Completato: {self.summary.get('found', 0):,} trovati su {self.summary.get('games', 0):,}"
        except Exception as e:
            self.status, self.phase = "error", f"Errore: {type(e).__name__}: {e}"
            self.say(self.phase)
        finally:
            if cache:
                cache.close()
            self.finished = time.time()


@dataclass
class NetMetaJob(StoreJob):
    """Copertine e descrizioni delle voci degli store online (GameTDB offline + pagine dei siti)."""
    limit: int = 0  # 0 = tutte le voci
    search_ziperto: bool = True
    stop: bool = False

    def run(self, cfg, after_build):
        from ..library.build import build
        from .. import gametdb, storemeta
        self.status, self.started = "running", time.time()
        self.summary = {}  # aggiornato in corso d'opera: l'interfaccia mostra l'avanzamento
        cache = None
        try:
            cache = Cache(cfg.cache)
            self.phase = "Database GameTDB"
            tdb = gametdb.GameTDB(cfg.cache, log=self.say)
            if not tdb.ensure():
                self.say("GameTDB non disponibile: uso solo le pagine dei siti")
                tdb = None
            self.phase = "Copertine e descrizioni"
            asyncio.run(storemeta.fill(
                cfg, cache, tdb, log=self.say, limit=self.limit, stats=self.summary,
                cancelled=lambda: self.stop, search_site="ziperto.com" if self.search_ziperto else None))
            cache.close()
            cache = None
            self.phase = "Aggiornamento libreria"
            after_build(build(cfg, log=self.say))
            self.status = "done"
            done = self.summary.get("complete", 0)
            total = self.summary.get("games", 0)
            self.phase = f"Completato: {done:,} giochi con copertina e descrizione su {total:,}"
            if self.summary.get("stopped"):
                self.phase += " (interrotto)"
        except Exception as e:
            self.status, self.phase = "error", f"Errore: {type(e).__name__}: {e}"
            self.say(self.phase)
        finally:
            if cache:
                cache.close()
            self.finished = time.time()


@dataclass
class PcStoreJob(StoreJob):
    """Lettura degli elenchi degli store PC non ufficiali (repack / download diretto)."""

    def run(self, cfg, after_build):
        from .. import catalog, platforms as P
        from ..library.build import build
        from ..net import make_client
        self.status, self.started = "running", time.time()
        self.summary = {"stores": len(getattr(cfg, "pc_stores", []) or []), "games": 0}
        cache = None
        try:
            cache = Cache(cfg.cache)
            self.phase = "Lettura degli elenchi"
            plat = P.PLATFORMS["windows"]
            stores = list(getattr(cfg, "pc_stores", []) or [])

            async def go():
                async with make_client() as client:
                    for it in stores:
                        try:
                            entries = await catalog.fetch(cfg, cache, client, plat, it["url"], log=self.say)
                            self.summary["games"] += len(entries)
                            self.summary["done"] = self.summary.get("done", 0) + 1
                        except Exception as e:  # un sito giu' non ferma gli altri
                            self.say(f"{it['url']}: {type(e).__name__}: {e}")

            asyncio.run(go())
            cache.close()
            cache = None
            self.phase = "Aggiornamento libreria"
            after_build(build(cfg, log=self.say))
            self.status = "done"
            self.phase = f"Completato: {self.summary['games']:,} giochi da {len(stores)} elenchi"
        except Exception as e:
            self.status, self.phase = "error", f"Errore: {type(e).__name__}: {e}"
            self.say(self.phase)
        finally:
            if cache:
                cache.close()
            self.finished = time.time()


@dataclass
class HltbJob(StoreJob):
    """Durate dei giochi da HowLongToBeat, poi ricostruzione della libreria."""
    only_missing: bool = True
    stop: bool = False

    def run(self, cfg, after_build):
        from .. import hltb
        from ..library.build import build
        self.status, self.started = "running", time.time()
        cache = None
        try:
            cache = Cache(cfg.cache)
            self.phase = "Ricerca su HowLongToBeat"
            self.summary = hltb.run(cfg, cache, log=self.say, progress=self.progress, only_missing=self.only_missing,
                                    cancelled=lambda: self.stop)
            cache.close()
            cache = None
            self.phase = "Aggiornamento libreria"
            after_build(build(cfg, log=self.say))
            self.status = "done"
            self.phase = f"Completato: {self.summary.get('found', 0):,} durate trovate su {self.summary.get('games', 0):,}"
        except Exception as e:
            self.status, self.phase = "error", f"Errore: {type(e).__name__}: {e}"
            self.say(self.phase)
        finally:
            if cache:
                cache.close()
            self.finished = time.time()


@dataclass
class TopGamesJob(StoreJob):
    """Voti online (IGDB, RAWG) per i candidati alla classifica dei migliori giochi, poi ricostruzione della libreria."""
    pids: list = field(default_factory=list)  # vuoto = tutte le console
    stop: bool = False

    def run(self, cfg, after_build):
        from .. import topgames
        from ..library.build import build
        self.status, self.started = "running", time.time()
        self.summary = {}  # aggiornato in corso d'opera
        cache = None
        try:
            cache = Cache(cfg.cache)
            self.phase = "Voti online dei candidati"
            topgames.run(cfg, cache, self.pids or None, log=self.say, stats=self.summary, cancelled=lambda: self.stop)
            cache.close()
            cache = None
            self.phase = "Aggiornamento libreria"
            after_build(build(cfg, log=self.say))
            self.status = "done"
            s = self.summary
            self.phase = (f"Completato: {s.get('done', 0)} console, {s.get('candidates', 0):,} candidati, "
                          f"{s.get('found', 0):,} voti trovati" + (" (interrotto)" if s.get("stopped") else ""))
        except Exception as e:
            self.status, self.phase = "error", f"Errore: {type(e).__name__}: {e}"
            self.say(self.phase)
        finally:
            if cache:
                cache.close()
            self.finished = time.time()


@dataclass
class CollArtJob(StoreJob):
    """Icone e copertine delle collezioni da Wikipedia."""
    collections: list = field(default_factory=list)
    only_missing: bool = True
    stop: bool = False

    def run(self, cfg, after_build):
        from .. import collart
        self.status, self.started = "running", time.time()
        cache = None
        try:
            cache = Cache(cfg.cache)
            self.phase = "Ricerca su Wikipedia"
            self.summary = collart.run(cfg, cache, self.collections, log=self.say, progress=self.progress,
                                       only_missing=self.only_missing, cancelled=lambda: self.stop)
            after_build(self.summary)
            self.status = "done"
            self.phase = f"Completato: {self.summary.get('found', 0):,} collezioni con immagini da Wikipedia"
        except Exception as e:
            self.status, self.phase = "error", f"Errore: {type(e).__name__}: {e}"
            self.say(self.phase)
        finally:
            if cache:
                cache.close()
            self.finished = time.time()


def preview(cfg, library_files: set[str], incomplete: dict[str, tuple[int, int]] | None = None) -> dict:
    """Console trovate sul disco, con numero di giochi e quanti non sono ancora in libreria,
    piu' le cartelle che non sono state riconosciute come console."""
    targets = find_platform_dirs(cfg)
    rows = []
    for p, folder in targets:
        entries = list_entries(p, folder)
        # i [BIOS] non entrano mai in libreria (a meno di show_bios): non sono "nuovi", nessuno scraping li farebbe sparire
        skip_bios = not getattr(cfg, "collections", {}).get("show_bios")
        new = sum(1 for path, _ in entries if str(path) not in library_files
                  and not (skip_bios and path.name.startswith("[BIOS]")))
        nodata, partial = (incomplete or {}).get(p.id, (0, 0))
        label = str(folder.relative_to(cfg.roms)) if folder.is_relative_to(cfg.roms) else str(folder)  # cartella aggiuntiva
        rows.append({"platform": p.id, "name": p.name, "folder": label,
                     "games": len(entries), "new": new, "nodata": nodata, "partial": partial})
    # cartelle in radice che non sono console e non contengono console riconosciute
    found_paths = [f for _, f in targets]
    unknown = []
    try:
        for d in sorted(x for x in cfg.roms.iterdir() if x.is_dir() and not x.name.startswith(".")):
            if d in found_paths or any(d in f.parents for f in found_paths):
                continue
            unknown.append(d.name)
    except OSError:
        pass
    from ..config import rom_roots
    extra = [{"path": str(r), "online": r.exists()} for r in rom_roots(cfg)[1:]]
    return {"root": str(cfg.roms), "platforms": rows, "unknown": unknown, "extra": extra}
