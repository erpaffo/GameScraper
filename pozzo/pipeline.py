"""Orchestrazione dello scraping.

Fasi:
  1. setup di tutte le sorgenti in parallelo (login, download DAT libretro, ...)
  2. identificazione per NOME ESATTO sui DAT No-Intro/Redump dei file non ancora hashati:
     se il nome corrisponde, CRC/MD5/SHA1 arrivano dal DAT e il file non viene letto
  3. hash solo dei file rimasti (strategia adatta a HDD/SSD)
  4. pipeline A CATENA: ogni sorgente e' uno stadio con la sua coda. Un gioco passa allo
     stadio successivo appena il precedente ha finito con lui (media compresi), quindi
     libretro, IGDB, ScreenScraper... lavorano CONTEMPORANEAMENTE su giochi diversi.
     Il tempo totale e' circa quello della sorgente piu' lenta, non la somma.

L'ordine di priorita' resta quello di `sources`: uno stadio vede il gioco solo dopo tutti
quelli precedenti, e lo interroga solo se mancano ancora campi/media che sa fornire.
"""

from __future__ import annotations

import asyncio
import json
import sys
import re
from collections import Counter, defaultdict
from pathlib import Path

from .net import Stop, download_media, make_client
from .scanner import Game, hash_games
from .sources import SOURCES
from .sources.base import Context, Result, Skipped

LIST_FIELDS = {"genres", "franchises"}
END = object()  # fine del flusso in una coda

_PRIORITY: dict[str, list[str]] = {}


def _rank(field: str, source: str) -> int:
    lst = _PRIORITY.get(field) or _PRIORITY.get("*", [])
    return lst.index(source) if source in lst else 99


def apply_field(g: Game, field: str, value, source: str):
    """Imposta un campo se manca, o se la sorgente ha priorita' piu' alta per quel campo
    (es. descrizione: ScreenScraper in italiano prima di IGDB in inglese)."""
    if field == "canonical":
        g.canonical = g.canonical or value
    if value in (None, "", [], {}):
        return
    if g.data.get(field) and _rank(field, source) >= _rank(field, g.origin.get(field, "")):
        return
    g.data[field] = value
    g.origin[field] = source


def set_priority(cfg):
    _PRIORITY.clear()
    _PRIORITY["*"] = list(cfg.sources)
    _PRIORITY.update(cfg.field_priority)


def load_cached(cfg, cache, games: list[Game]):
    """Ricostruisce i dati uniti dalla cache, rispettando l'ordine di priorita' delle sorgenti."""
    set_priority(cfg)
    allres = cache.all_resources([g.key for g in games])
    order = list(cfg.sources) + [s for s in SOURCES if s not in cfg.sources]
    for g in games:
        res = allres.get(g.key, {})
        for src in order:
            for field, value in res.get(src, {}).items():
                if field in LIST_FIELDS:
                    try:
                        value = json.loads(value)
                    except ValueError:
                        pass
                apply_field(g, field, value, src)


class Stage:
    """Uno stadio della catena: consuma giochi da `qin`, li passa a `qout` quando ha finito."""

    def __init__(self, src, qin: asyncio.Queue, qout: asyncio.Queue, done: dict, ctx, progress):
        self.src, self.qin, self.qout, self.done = src, qin, qout, done
        self.cfg, self.cache, self.log = ctx.cfg, ctx.cache, ctx.log
        self.progress = progress
        self.stats = Counter()
        self.task = progress.add_task(src.name, total=0)
        self.mtask = progress.add_task(f"  media {src.name}", total=0, visible=False)
        self.inflight: set[asyncio.Task] = set()
        self.errors_shown = 0
        self.stop_logged = False
        self.batch = src.batch or max(16, getattr(src, "concurrency", 4) * 4)

    def eligible(self, g: Game) -> bool:
        s = self.src
        return (not s.stopped and s.supports(g.platform) and g.key not in self.done and g.key not in s.skip
                and (getattr(s, "always", False) or bool(g.missing(self.cfg.wanted) & s.provides)))

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self.inflight.add(t)
        t.add_done_callback(self.inflight.discard)

    async def _fetch(self, g: Game, mtype: str, url: str, headers) -> None:
        src = self.src
        ident = g.key.split("/", 1)[1].replace(":", "_")
        dest = self.cache.media_dir / g.platform.id / mtype / f"{ident}_{src.name}"
        path = await download_media(src.client, url, dest, limiter=src.media_limiter, headers=headers)
        self.progress.advance(self.mtask)
        if path:
            rel = path.relative_to(self.cache.folder).as_posix()
            self.cache.put(g.key, src.name, f"media:{mtype}", rel)
            apply_field(g, f"media:{mtype}", rel, src.name)
            self.stats["media"] += 1

    async def _finish(self, g: Game, downloads: list):
        """Il gioco passa allo stadio successivo solo quando i suoi media sono scaricati,
        cosi' lo stadio dopo sa esattamente cosa manca ancora."""
        if downloads:
            await asyncio.gather(*downloads, return_exceptions=True)
        self.qout.put_nowait(g)

    def _handle(self, g: Game, res) -> list:
        src = self.src
        if isinstance(res, Skipped):
            return []
        if isinstance(res, Stop):
            src.stopped = True
            if not self.stop_logged:
                self.stop_logged = True
                self.log(f"[bold red]{src.name}: {res} - sorgente fermata, i giochi rimasti verranno "
                         f"ripresi alla prossima esecuzione.[/]")
            return []
        if isinstance(res, Exception):
            self.stats["errori"] += 1
            if self.errors_shown < 5:
                self.log(f"[red]{src.name}: {g.path.name}: {type(res).__name__}: {res}[/]")
                self.errors_shown += 1
            return []
        if res is None or not isinstance(res, Result) or not (res.fields or res.media):
            self.cache.set_lookup(g.key, src.name, "notfound")
            self.stats["non trovati"] += 1
            return []
        for k, v in res.fields.items():
            self.cache.put(g.key, src.name, k, v)
            apply_field(g, k, v, src.name)
        self.cache.set_lookup(g.key, src.name, "found" if res.fields else "nodata")
        self.stats["trovati" if res.fields else "non trovati"] += 1
        downloads = []
        for mtype, url in res.media.items():
            if mtype in self.cfg.media and url and not g.data.get(f"media:{mtype}"):
                t = self.progress.tasks[self.mtask]
                self.progress.update(self.mtask, total=(t.total or 0) + 1, visible=True)
                downloads.append(asyncio.create_task(self._fetch(g, mtype, url, res.media_headers)))
        return downloads

    async def _run_chunk(self, chunk: list[Game]):
        seen: set[int] = set()
        try:
            async for g, res in self.src.lookup_many(chunk):
                seen.add(id(g))
                self.progress.advance(self.task)
                downloads = self._handle(g, res)
                self._spawn(self._finish(g, downloads))
        except Exception as e:
            self.log(f"[red]{self.src.name}: errore inatteso: {type(e).__name__}: {e}[/]")
        finally:
            for g in chunk:  # giochi non restituiti (sorgente fermata o errore): passano oltre
                if id(g) not in seen:
                    self.progress.advance(self.task)
                    self.qout.put_nowait(g)

    async def run(self):
        ended = False
        while not ended:
            g = await self.qin.get()
            if g is END:
                break
            if not self.eligible(g):
                self.qout.put_nowait(g)
                continue
            chunk = [g]
            # raccoglie altri giochi gia' pronti, senza aspettare troppo (batch per IGDB/ArcadeDB)
            while len(chunk) < self.batch:
                try:
                    nxt = self.qin.get_nowait()
                except asyncio.QueueEmpty:
                    try:
                        nxt = await asyncio.wait_for(self.qin.get(), 0.05)
                    except TimeoutError:
                        break
                if nxt is END:
                    ended = True
                    break
                if self.eligible(nxt):
                    chunk.append(nxt)
                else:
                    self.qout.put_nowait(nxt)
            t = self.progress.tasks[self.task]
            self.progress.update(self.task, total=(t.total or 0) + len(chunk))
            self._spawn(self._run_chunk(chunk))
        while self.inflight:
            await asyncio.gather(*list(self.inflight), return_exceptions=True)
        self.qout.put_nowait(END)
        self.cache.commit()
        if self.stats:
            self.log(f"[bold cyan]{self.src.name}[/]: " + ", ".join(f"{k} {v:,}" for k, v in self.stats.items()))
        else:
            self.log(f"{self.src.name}: niente da fare (tutto gia' in cache o completo)")
        await self.src.close()


def _library_versions(cfg) -> tuple[set[str], set[str]]:
    """Dalla libreria del frontend: (chiavi delle versioni principali, chiavi delle altre versioni)."""
    import sqlite3
    path = Path(cfg.cache) / "library.db"
    if not path.exists():
        return set(), set()
    try:
        db = sqlite3.connect(path)
        rows = db.execute("SELECT key, is_primary FROM games").fetchall()
        db.close()
    except sqlite3.Error:
        return set(), set()
    return {k for k, p in rows if p}, {k for k, p in rows if not p}


BAD_DUMP = re.compile(r"\((beta|proto|prototype|demo|sample|kiosk|debug|unl|pirate|hack)[^)]*\)|\[b\]|\[h\]", re.I)


def filename_duplicates(cfg, games: list[Game], primary: set[str] = frozenset()) -> set[str]:
    """Copie in piu' riconosciute dal TITOLO DEL FILE, prima di qualsiasi scraping.

    'Super Mario World (USA).zip', 'Super Mario World (Europe) (Rev 1).zip' e
    'Super Mario World [!].zip' hanno lo stesso titolo pulito sulla stessa console: si interroga
    solo una versione (la principale gia' scelta in libreria, altrimenti quella gia' con dati,
    nella regione preferita, non beta/hack) e le altre ne prendono i metadati."""
    from .matching import normalize
    from .scanner import regions_of
    pref = list(dict.fromkeys([*(getattr(cfg, "regions", None) or [])]))

    def score(g: Game):
        rs = regions_of(g.stem)
        reg = min((pref.index(r) for r in rs if r in pref), default=len(pref))
        return (0 if g.key in primary else 1, 0 if "title" in g.origin else 1,
                1 if BAD_DUMP.search(g.stem) else 0, reg, len(g.stem))

    by: dict[tuple, list[Game]] = defaultdict(list)
    for g in games:
        if g.stem.startswith("[BIOS]"):
            continue
        t = normalize(g.search_name)
        if t:
            by[(g.platform.id, t)].append(g)
    dup: set[str] = set()
    for gs in by.values():
        if len(gs) > 1:
            best = min(gs, key=score)
            dup.update(g.key for g in gs if g is not best)
    return dup


class ScrapeLock:
    """Impedisce due scraping contemporanei (es. app + riga di comando): raddoppierebbero i thread
    verso ScreenScraper oltre il limite dell'account. Il lock del sistema operativo si libera da solo
    se il processo termina in modo anomalo."""

    def __init__(self, folder):
        self.path = Path(folder) / "scrape.lock"
        self.f = None

    def __enter__(self):
        self.f = open(self.path, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            self.f = None
            raise RuntimeError("C'e' gia' uno scraping in corso (nell'app o in un altro terminale). "
                               "Aspetta che finisca: due insieme supererebbero i limiti di ScreenScraper.")
        return self

    def __exit__(self, *exc):
        if self.f:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.f.close()


async def scrape(cfg, cache, games: list[Game], sources: list[str], refresh: bool, progress, log,
                 retry_missing: bool = False):
    """refresh = re-interroga tutto; retry_missing = riprova le fonti che in passato NON avevano
    trovato il gioco (solo per i giochi a cui manca ancora qualcosa che quella fonte sa dare)."""
    with ScrapeLock(cfg.cache):
        await _scrape(cfg, cache, games, sources, refresh, progress, log, retry_missing)


async def _scrape(cfg, cache, games: list[Game], sources: list[str], refresh: bool, progress, log,
                  retry_missing: bool = False):
    from . import net
    from .policy import DEFAULTS, Ledger, policy_for
    ledger = Ledger(cache.folder / "cache.db")
    # cache delle risposte: con "riscarica tutto" si ignorano le risposte salvate (ma si aggiornano)
    net.HTTP_CACHE = net.HttpCache(cache.folder / "cache.db", enabled=True)
    if refresh:
        net.HTTP_CACHE.get = lambda *a, **k: None
    try:
        await _scrape_inner(cfg, cache, games, sources, refresh, progress, log, retry_missing, ledger)
    finally:
        hits = net.HTTP_CACHE.hits if net.HTTP_CACHE else 0
        if hits:
            log(f"Risposte riusate dalla cache (nessuna richiesta ai siti): {hits:,}")
        net.HTTP_CACHE.close()
        net.HTTP_CACHE = None
        ledger.close()


def guard_source(src, cfg, ledger, log):
    """Applica le regole (ritmo, budget, pause su errori) a tutti i limitatori di una fonte."""
    from .net import RateLimiter
    from .policy import DEFAULTS, policy_for
    for attr, lim in vars(src).items():
        if not isinstance(lim, RateLimiter) or lim.policy is not None:
            continue
        if "media" in attr:
            key = {"launchbox": "launchbox_images", "libretro": "libretro_thumbs"}.get(src.name)
            if key:  # ritmo dei download di immagini (non contati nel budget)
                lim.guard(policy_for(cfg, key), ledger, counts=False, log=log)
                lim.interval = max(lim.interval, 1.0 / policy_for(cfg, key).rate)
        elif not getattr(src, "offline", False):
            lim.guard(policy_for(cfg, src.name), ledger, counts=True, log=log)


async def _scrape_inner(cfg, cache, games: list[Game], sources: list[str], refresh: bool, progress, log,
                        retry_missing: bool, ledger):
    async with make_client() as client:
        ctx = Context(cfg, cache, client, log)
        platforms = {g.platform for g in games}

        # 1. setup sorgenti in parallelo
        objs = []
        for name in sources:
            if name not in SOURCES:
                log(f"[red]Sorgente sconosciuta: {name}. Disponibili: {', '.join(SOURCES)}[/]")
            else:
                objs.append(SOURCES[name](ctx))
        oks = await asyncio.gather(*(s.setup(platforms) for s in objs), return_exceptions=True)
        srcs = []
        for s, ok in zip(objs, oks):
            if isinstance(ok, Exception):
                log(f"[red]{s.name}: setup fallito: {type(ok).__name__}: {ok}[/]")
            elif ok:
                guard_source(s, cfg, ledger, log)
                srcs.append(s)

        # 2. identificazione per nome esatto (niente letture su disco)
        pending = [g for g in games if g.needs_hash]
        lib = next((s for s in srcs if s.name == "libretro"), None)
        if pending and lib:
            hits = lib.name_first(pending)
            for g in hits:
                g.needs_hash = False
                cache.put_file(str(g.path), g.size, g.mtime, {k: getattr(g, k) for k in
                               ("dat_crc", "dat_size", "dat_md5", "dat_sha1") if getattr(g, k)})
            cache.commit()
            if hits:
                log(f"Identificati per nome (senza leggere i file): [bold]{len(hits):,}[/] su {len(pending):,} da hashare")

        # 3. hash dei file rimasti (bloccante, ma in questa fase non c'e' altro da fare;
        #    il lavoro vero e' nei processi figli)
        if any(g.needs_hash for g in games):
            hash_games(cfg, cache, games, progress, log)

        # 4. pipeline a catena
        load_cached(cfg, cache, games)
        if not srcs:
            return
        primary, secondary = _library_versions(cfg)
        # duplicati riconosciuti dal nome del file (anche per i giochi mai scrapati)
        fdup = filename_duplicates(cfg, games, primary) - secondary
        if fdup:
            secondary = secondary | fdup
            log(f"Duplicati riconosciuti dal nome del file: {len(fdup):,}")
        # i BIOS non sono giochi: niente richieste alle fonti online (libretro, offline, li riconosce comunque)
        bios = {g.key for g in games if g.stem.startswith("[BIOS]")}
        for s_ in srcs:
            if s_.name != "libretro":
                s_.skip = set(bios)
        skip_dup = cfg.app.get("skip_duplicates", True)
        if secondary and skip_dup:
            # duplicati (altre regioni/revisioni dello stesso gioco): niente scraping, prendono i metadati
            # della versione principale. libretro (offline) resta: serve a riconoscerli come duplicati.
            for s_ in srcs:
                if s_.name != "libretro":
                    s_.skip = s_.skip | secondary
            n = sum(1 for g in games if g.key in secondary)
            if n:
                log(f"Duplicati esclusi dallo scraping: {n:,} (usano i metadati della versione principale)")
        elif secondary and cfg.ss.get("only_primary", True):
            ss = next((s for s in srcs if s.name == "screenscraper"), None)
            if ss:
                ss.skip = ss.skip | secondary
        # prima i giochi senza alcun dato, poi le versioni principali, poi il resto:
        # se la quota finisce a meta', e' stata spesa dove serviva di piu'
        games = sorted(games, key=lambda g: ("title" in g.origin, g.key not in primary))
        keys = [g.key for g in games]
        queues = [asyncio.Queue() for _ in range(len(srcs) + 1)]
        stages = []
        for i, s in enumerate(srcs):
            done = {} if refresh else cache.lookup_status(keys, s.name)
            if retry_missing:  # si tengono solo i "trovati": i "non trovati" vengono riprovati
                done = {k: v for k, v in done.items() if v == "found"}
            stages.append(Stage(s, queues[i], queues[i + 1], done, ctx, progress))
        for g in games:
            queues[0].put_nowait(g)
        queues[0].put_nowait(END)
        await asyncio.gather(*(st.run() for st in stages))



# ---------------------------------------------------------------- stima prima di partire

# richieste per gioco interrogato (IGDB: 10 giochi per richiesta + ricerche singole per chi non trova)
REQ_PER_GAME = {"screenscraper": 1.15, "igdb": 0.35, "rawg": 1.0, "thegamesdb": 1.3, "arcadedb": 0.05, "wikipedia": 2.5}


def plan(cfg, cache, games: list[Game], sources: list[str], refresh: bool = False, retry_missing: bool = False) -> list[dict]:
    """Stima per fonte: giochi da interrogare, richieste, tempo e giorni necessari entro i limiti.
    E' un limite massimo: in cascata le fonti precedenti riempiono dati e le successive lavorano meno."""
    import math

    from .policy import Ledger, policy_for
    load_cached(cfg, cache, games)
    keys = [g.key for g in games]
    wanted = cfg.wanted
    primary, secondary = _library_versions(cfg)
    secondary = secondary | filename_duplicates(cfg, games, primary)
    ledger = Ledger(cache.folder / "cache.db")
    out = []
    try:
        for name in sources:
            cls = SOURCES.get(name)
            if not cls:
                continue
            offline = getattr(cls, "offline", False) or name == "libretro"
            done = {} if refresh else cache.lookup_status(keys, name)
            if retry_missing:
                done = {k: v for k, v in done.items() if v == "found"}
            def ok(g):
                if g.key in done or g.stem.startswith("[BIOS]"):
                    return False
                if name == "screenscraper" and g.platform.ss is None:
                    return False
                if name != "libretro" and g.key in secondary and (cfg.app.get("skip_duplicates", True)
                                                                  or (name == "screenscraper" and cfg.ss.get("only_primary", True))):
                    return False
                if name == "arcadedb" and not g.platform.arcade:
                    return False
                return getattr(cls, "always", False) or bool(g.missing(wanted) & cls.provides)
            n = len({g.key for g in games if ok(g)})
            pol = policy_for(cfg, name)
            req = 0 if offline else math.ceil(n * REQ_PER_GAME.get(name, 1.0))
            day_left, month_left = ledger.remaining(pol) if not offline else (None, None)
            daily = pol.daily or (20000 if name == "screenscraper" else 0)
            out.append({"source": name, "offline": offline, "games": n, "requests": req,
                        "seconds": round(req / pol.rate) if req and pol.rate else 0,
                        "days": (math.ceil(req / daily) if daily and req else (1 if req else 0)),
                        "daily_left": day_left, "month_left": month_left,
                        "note": "quota giornaliera dell'account" if name == "screenscraper" else ""})
    finally:
        ledger.close()
    return out
