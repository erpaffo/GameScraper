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

    def run(self, cfg, after_build, after_scan=None):
        """Da eseguire in un thread. `after_build(stats)` viene chiamato a libreria ricostruita.
        `after_scan(stats)`, se dato, a scansione finita: i file trovati compaiono subito, i metadati arrivano dopo."""
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
            if after_scan and not self.keys:
                cache.commit()
                self.phase = "Aggiornamento libreria"
                after_scan(build(cfg, log=self.say))

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
class StoreRefreshJob:
    """Coda di aggiornamento degli store: fino a `workers` in parallelo, gli altri aspettano.

    Ogni click su ⟳ (o su «Aggiorna tutti») aggiunge elenchi alla coda: si avviano subito i primi
    `workers` che non puntano allo stesso sito, gli altri restano in attesa e partono appena un
    posto si libera. Gli elenchi letti restano in cache.db, quindi la libreria si ricostruisce da lì
    anche senza rete e l'aggiornamento può girare in sottofondo.
    """

    status: str = "idle"  # idle | running | done | error
    phase: str = ""
    log: list[str] = field(default_factory=list)
    targets: list[dict] = field(default_factory=list)  # una riga per store: in coda, in corso o finito
    workers: int = 4
    games: int = 0
    started: float = 0
    finished: float = 0
    stop: bool = False
    only: list[str] | None = None  # id/url degli elenchi scelti: None = tutti
    queue: list[str] = field(default_factory=list)  # id in attesa di partire
    active: int = 0
    rebuilding: bool = False
    managing: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    cache_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    idle_evt: threading.Event = field(default_factory=threading.Event, repr=False)

    def say(self, msg):
        from .. import applog
        text = _plain(msg)
        self.log.append(text)
        del self.log[:-300]
        applog.write("store", text)

    # ---------------------------------------------------------------- stato per l'interfaccia
    def _row(self, tid: str) -> dict | None:
        for t in self.targets:
            if t["id"] == tid:
                return t
        return None

    def state(self) -> dict:
        from .. import applog
        now = time.time()
        with self.lock:
            rows = []
            for t in self.targets:
                elapsed = (t.get("finished") or now) - (t.get("started") or now)
                page, pages = t.get("page") or 0, t.get("pages") or 0
                rate = (elapsed / page) if page and elapsed > 0 else None  # secondi per pagina
                eta = (pages - page) * rate if rate and pages > page else None
                rows.append({**t, "elapsed": round(elapsed, 1),
                             "rate": round(rate, 2) if rate else None,
                             "eta": round(eta) if eta else None,
                             "position": (self.queue.index(t["id"]) + 1) if t["id"] in self.queue else None})
            done = sum(1 for t in self.targets if t["status"] in ("done", "error", "skip"))
            queued = sum(1 for t in self.targets if t["status"] == "pending")
            running = sum(1 for t in self.targets if t["status"] == "running")
            phase = self.phase
            if self.stop and self.status == "running":
                phase = ("Interruzione: aggiornamento della libreria…" if self.rebuilding
                         else "Interruzione in corso…")
            return {"status": self.status, "phase": phase, "log": self.log[-40:], "targets": rows,
                    "done": done, "total": len(self.targets), "games": self.games,
                    "parallel": self.workers, "queued": queued, "running": running,
                    # durante la ricostruzione finale non c'è più niente da interrompere
                    "stoppable": self.status == "running" and not self.rebuilding,
                    "log_file": str(applog.current()) if applog.enabled() else None,
                    "started": self.started, "finished": self.finished, "stopping": self.stop,
                    "tasks": [{"name": t["label"], "total": 1,
                               "done": 1 if t["status"] in ("done", "error", "skip") else 0}
                              for t in self.targets]}

    # ---------------------------------------------------------------- coda
    def add(self, cfg, items: list[dict], after_build=None) -> int:
        """Mette in coda gli store indicati (quelli già in attesa o in corso vengono saltati)."""
        from ..storerefresh import settings as schedule
        added = 0
        with self.lock:
            self.workers = schedule(cfg)["workers"]
            busy = {t["id"] for t in self.targets if t["status"] in ("pending", "running")}
            for it in items:
                if it["id"] in busy:
                    continue
                row = dict(it, status="pending", games=0, seen=0, pages=0, page=0, type=None, error=None,
                           log=[], started=None, finished=None)
                self.targets = [t for t in self.targets if t["id"] != it["id"]] + [row]
                self.queue.append(it["id"])
                busy.add(it["id"])
                added += 1
            if added:
                self.status = "running"
                self.stop = False
                self.started = self.started or time.time()
                self.finished = 0
                self.idle_evt.clear()
                self.phase = f"{len(self.queue)} in coda · {self.workers} in parallelo"
                self.say(self.phase)
            manage = added > 0 and not self.managing
            if manage:
                self.managing = True
        if manage:
            threading.Thread(target=self._manage, args=(cfg, after_build), daemon=True).start()
        return added

    def run(self, cfg, after_build):
        """Aggiorna subito tutti gli store (o quelli in `only`) e attende la fine."""
        from ..storerefresh import targets as collect
        plan = collect(cfg)
        if self.only:
            wanted = {str(x) for x in self.only}
            plan = [t for t in plan if t["id"] in wanted or t["url"] in wanted]
        if not plan:
            self.status, self.phase = "done", "Nessuno store configurato"
            self.finished = time.time()
            self.idle_evt.set()
            return
        if self.add(cfg, plan, after_build):
            self.idle_evt.wait()

    def stop_now(self):
        """Interrompe: la lettura in corso finisce la pagina, la coda viene scartata."""
        from .. import applog
        with self.lock:
            self.stop = True
            for tid in list(self.queue):
                row = self._row(tid)
                if row is not None:
                    row["status"], row["finished"] = "skip", time.time()
            self.queue.clear()
        applog.write("piano", "interruzione richiesta")

    # ---------------------------------------------------------------- esecuzione
    def _pick(self) -> dict | None:
        """Prossima riga da avviare (lock già preso): mai due elenchi dello stesso sito insieme."""
        from ..storerefresh import host as SR_host
        if self.stop:
            return None
        busy_hosts = {SR_host(t["url"]) for t in self.targets if t["status"] == "running"}
        for tid in list(self.queue):
            row = self._row(tid)
            if row is None:
                self.queue.remove(tid)
                continue
            if SR_host(row["url"]) in busy_hosts:
                continue
            self.queue.remove(tid)
            row["status"], row["started"] = "running", time.time()
            return row
        return None

    def _ensure(self, cfg, after_build):
        with self.lock:
            while self.active < self.workers:
                row = self._pick()
                if row is None:
                    break
                self.active += 1
                threading.Thread(target=self._worker, args=(cfg, after_build, row), daemon=True).start()

    def _manage(self, cfg, after_build):
        """Tiene i worker al limite e, a coda vuota, ricostruisce la libreria una volta sola."""
        try:
            while True:
                self._ensure(cfg, after_build)
                with self.lock:
                    if self.stop and self.queue:  # interruzione: la coda non parte più
                        for tid in list(self.queue):
                            row = self._row(tid)
                            if row is not None:
                                row["status"], row["finished"] = "skip", time.time()
                        self.queue.clear()
                    if self.active or self.queue:
                        work = True
                    elif self.status == "running":
                        self.rebuilding = True
                        work = False
                    else:  # niente da fare e niente in corso: si esce
                        self.managing = False
                        return
                if not work:
                    self._finish(cfg, after_build)
                    with self.lock:
                        if self.active or self.queue:  # sono arrivati nuovi store mentre si costruiva
                            self.status = "running"
                            continue
                        self.managing = False
                        return
                time.sleep(0.25)
        except Exception as e:  # pragma: no cover - difesa
            with self.lock:
                self.managing = False
                self.status, self.phase = "error", f"Errore: {type(e).__name__}: {e}"
            self.say(self.phase)
            self.idle_evt.set()

    def _worker(self, cfg, after_build, row):
        try:
            self._run_one(cfg, row)
        finally:
            with self.lock:
                self.active -= 1

    def _run_one(self, cfg, t: dict):
        from .. import catalog, platforms as P
        from ..net import make_client
        lock = self.lock

        def log_of(msg):
            text = _plain(msg)
            with lock:
                t["log"].append(text)
                del t["log"][:-40]
            from .. import applog
            applog.write("store", f"{t['label']}: {text}")

        def progress_of(page, pages, games=0):
            with lock:
                t["page"], t["pages"] = page, pages
                t["seen"] = (t.get("seen") or 0) + int(games or 0)

        if self.stop:
            with lock:
                t["status"], t["finished"] = "skip", time.time()
            return
        # la connessione si apre una alla volta e nel thread che la usa: sqlite non le condivide
        # fra thread e due PRAGMA journal_mode=WAL simultanee darebbero «database is locked»
        with self.cache_lock:
            cache = Cache(cfg.cache)
        try:
            async def go():
                async with make_client() as client:
                    return await catalog.fetch(cfg, cache, client, P.PLATFORMS[t["platform"]], t["url"],
                                               log=log_of, progress=progress_of, should_stop=lambda: self.stop)
            entries = asyncio.run(go())
            if self.stop:  # interrotto a metà elenco: le pagine lette restano salvate
                with lock:
                    t.update(status="skip", finished=time.time())
                    t["log"].append("interrotto: salvate le pagine già lette")
                self.say(f"{t['label']}: interrotto alla pagina {t['page']}/{t['pages']}")
                return
            try:
                info = catalog.cached_info(cache, t["platform"], t["url"])
                pages, kind = max(info["total_pages"] or 0, t["pages"] or 0) or 1, info["type"]
            except Exception:
                pages, kind = t["pages"] or 1, None
            with lock:
                t.update(status="done", games=len(entries), pages=pages, type=kind, finished=time.time())
                self.games += len(entries)
            self.say(f"{t['label']}: {len(entries):,} giochi ({t['page']} pagine)")
        except Exception as e:  # un sito giù non ferma gli altri
            with lock:
                t.update(status="error", error=f"{type(e).__name__}: {e}", finished=time.time())
                t["log"].append(f"{type(e).__name__}: {e}")
            self.say(f"{t['label']}: {type(e).__name__}: {e}")
        finally:
            cache.close()

    def _finish(self, cfg, after_build):
        from ..library.build import build
        try:
            self.phase = "Aggiornamento della libreria"
            if after_build is not None:
                after_build(build(cfg, log=self.say))
            with self.lock:
                ok = sum(1 for t in self.targets if t["status"] == "done")
                errors = sum(1 for t in self.targets if t["status"] == "error")
                extra = f", {errors} non leggibili" if errors else ""
                self.phase = (f"Interrotto: {self.games:,} giochi da {ok} store{extra}" if self.stop
                              else f"Completato: {self.games:,} giochi da {ok} store{extra}")
                self.status, self.rebuilding = "done", False
                self.finished = time.time()
                self.idle_evt.set()
        except Exception as e:
            with self.lock:
                self.status, self.phase, self.rebuilding = "error", f"Errore: {type(e).__name__}: {e}", False
                self.idle_evt.set()
            self.say(self.phase)


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
