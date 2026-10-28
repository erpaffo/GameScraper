"""Migliori giochi: Top 100 per console e classifica globale (Top 100/1000), fra posseduti e scaricabili.

1. Candidati per console: i giochi meglio votati della piattaforma su IGDB (abbinati per titolo alle voci della
   libreria) piu' le voci della console gia' ben votate.
2. Solo per i candidati: voti online IGDB e RAWG (Metacritic), salvati in `resources` come lo scraping normale.
   La libreria li usa anche per i giochi da scaricare (build.py), quindi la scheda mostra il voto vero.
3. Classifica: entrano solo i giochi con voti affidabili; punteggio fra critica e giocatori.
   Nella globale lo stesso gioco su piu' console e' una voce sola (prima la versione posseduta).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

from . import platforms as P
from .matching import normalize
from .scanner import Game

LOCAL, GLOBAL = 100, 1000
MAX_AGE = 30 * 86400  # i voti online letti negli ultimi 30 giorni non si richiedono
IGDB_TOP = 300  # migliori della piattaforma chiesti a IGDB
OWN_TOP = 150  # voci della console gia' ben votate che diventano candidate
PRIOR, CONF = 0.6, 20  # pochi voti dei giocatori: il voto si avvicina a 0.6
MIN_VOTES = 50
ONLINE = ("igdb", "rawg")


class NetGame(Game):
    """Gioco della libreria (posseduto o da scaricare) cercato per titolo: la chiave resta quella della libreria."""

    def __init__(self, key: str, title: str, platform):
        super().__init__(path=Path("_"), platform=platform, size=0, mtime=0)
        self._key, self._title = key, title

    @property
    def key(self) -> str:
        return self._key

    @property
    def search_name(self) -> str:
        return self._title


# ---------------------------------------------------------------- punteggio e classifiche

def _ratings(g: dict) -> list:
    r = g.get("ratings")
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except ValueError:
            r = []
    return r or []


def score(g: dict) -> float | None:
    """Punteggio 0..1, oppure None se i voti non sono affidabili.

    Affidabile = almeno 2 fonti con voto, oppure un voto della critica, oppure almeno 50 voti."""
    rating, critics = g.get("rating"), g.get("rating_critics")
    votes = int(g.get("rating_count") or 0)
    if not (len(_ratings(g)) >= 2 or critics or votes >= MIN_VOTES):
        return None
    user = None if rating is None else (rating * votes + PRIOR * CONF) / (votes + CONF)
    parts = [x for x in (critics or None, user) if x is not None]
    return round(sum(parts) / len(parts), 4) if parts else None


def _entry(g: dict, s: float) -> dict:
    return {"id": g.get("id"), "key": g["key"], "title": g["title"], "platform": g["platform"], "year": g.get("year"),
            "score": s, "critics": g.get("rating_critics"), "rating": g.get("rating"),
            "votes": int(g.get("rating_count") or 0), "sources": [r.get("name") for r in _ratings(g)],
            "owned": g.get("store") != "online"}


def _scored(games: list[dict], pid: str | None = None):
    for g in games:
        if g.get("is_primary") and (pid is None or g["platform"] == pid):
            s = score(g)
            if s is not None:
                yield g, s


def local(games: list[dict], pid: str, limit: int = LOCAL) -> list[dict]:
    """Top della console (le versioni regionali sono gia' una voce sola: solo le principali)."""
    rows = sorted(_scored(games, pid), key=lambda x: (-x[1], x[0]["title"]))
    return [_entry(g, s) for g, s in rows[:limit]]


def global_(games: list[dict], limit: int = GLOBAL) -> list[dict]:
    """Classifica di tutte le console: stesso titolo su piu' console = una voce, con la versione posseduta
    (o la meglio votata) e le altre console in `also`. Ordine per il punteggio migliore fra le versioni."""
    by_title: dict[str, list[tuple[dict, float]]] = {}
    for g, s in _scored(games):
        by_title.setdefault(normalize(g["title"]) or g["key"], []).append((g, s))
    out = []
    for versions in by_title.values():
        g, s = max(versions, key=lambda x: (x[0].get("store") != "online", x[1]))
        e = _entry(g, s)
        e["best"] = max(v[1] for v in versions)
        e["also"] = sorted({v[0]["platform"] for v in versions} - {g["platform"]})
        out.append(e)
    out.sort(key=lambda e: (-e["best"], e["title"]))
    return out[:limit]


# ---------------------------------------------------------------- candidati e voti online

def library_rows(cfg) -> dict[str, list[dict]]:
    """Voci principali della libreria per console (dal library.db gia' costruito)."""
    path = Path(cfg.cache) / "library.db"
    if not path.exists():
        return {}
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        out: dict[str, list[dict]] = {}
        for r in db.execute("SELECT key, platform, title, store, rating, rating_count, rating_critics, ratings "
                            "FROM games WHERE is_primary=1"):
            out.setdefault(r["platform"], []).append(dict(r))
        return out
    finally:
        db.close()


def match(hits: list[dict], rows: list[dict]) -> list[tuple[dict, dict]]:
    """Abbina i giochi IGDB alle voci della libreria per titolo normalizzato (anche i nomi alternativi)."""
    idx: dict[str, dict] = {}
    for r in rows:
        idx.setdefault(normalize(r["title"] or ""), r)
    out, used = [], set()
    for h in hits:
        names = [h.get("name"), *(a.get("name") for a in h.get("alternative_names") or [])]
        for n in names:
            r = idx.get(normalize(n or "")) if n else None
            if r and r["key"] not in used:
                used.add(r["key"])
                out.append((r, h))
                break
    return out


def candidates_by_rating(rows: list[dict], limit: int = OWN_TOP) -> list[dict]:
    rated = [r for r in rows if r.get("rating_critics") or r.get("rating")]
    rated.sort(key=lambda r: -(r.get("rating_critics") or r.get("rating") or 0))
    return rated[:limit]


def fresh(cache, keys: list[str], source: str, max_age: float = MAX_AGE) -> set[str]:
    """Chiavi gia' cercate da `source` di recente (trovate o no): non si richiedono."""
    out: set[str] = set()
    since = time.time() - max_age
    for i in range(0, len(keys), 900):
        chunk = keys[i:i + 900]
        q = f"SELECT key FROM lookups WHERE source=? AND ts>? AND key IN ({','.join('?' * len(chunk))})"
        out.update(k for (k,) in cache.db.execute(q, [source, since, *chunk]))
    return out


def save(cache, g: Game, source: str, res) -> bool:
    """Salva il risultato come lo scraping normale; per le voci da scaricare anche l'indirizzo della copertina."""
    if res is None or not getattr(res, "fields", None):
        cache.set_lookup(g.key, source, "notfound")
        return False
    for k, v in res.fields.items():
        cache.put(g.key, source, k, v)
    if "/net:" in g.key:
        for mtype in ("cover", "screenshot"):
            if res.media.get(mtype):
                cache.put(g.key, source, f"url:{mtype}", res.media[mtype])
    cache.set_lookup(g.key, source, "found")
    return True


async def _igdb_top(src, platform) -> list[dict]:
    from .sources.igdb import FIELDS
    if not platform.igdb:
        return []
    ids = ",".join(map(str, platform.igdb))
    return await src._post("games", f"fields {FIELDS}; where platforms = ({ids}) & total_rating_count >= 10; "
                                    f"sort total_rating desc; limit {IGDB_TOP};")


async def fetch(cfg, cache, pids: list[str] | None = None, log=print, stats: dict | None = None,
                cancelled=lambda: False) -> dict:
    """Voti online per i candidati delle console indicate (tutte se `pids` e' vuoto)."""
    from .net import Stop, make_client
    from .pipeline import guard_source
    from .policy import Ledger
    from .sources.base import Context
    from .sources.igdb import Igdb
    from .sources.rawg import Rawg

    stats = stats if stats is not None else {}
    stats.update(consoles=0, done=0, candidates=0, found=0, asked=0)
    by_pid = library_rows(cfg)
    pids = [p for p in (pids or sorted(by_pid)) if p in by_pid and p in P.PLATFORMS]
    stats["consoles"] = len(pids)
    ledger = Ledger(cache.folder / "cache.db")
    try:
        async with make_client() as client:
            ctx = Context(cfg, cache, client, log)
            srcs = []
            for s in (Igdb(ctx), Rawg(ctx)):
                if await s.setup(set(pids)):
                    guard_source(s, cfg, ledger, log)
                    srcs.append(s)
            if not srcs:
                log("Nessuna fonte online disponibile: servono le chiavi IGDB o RAWG (Impostazioni › Fonti)")
                return stats
            igdb = next((s for s in srcs if s.name == "igdb"), None)
            for pid in pids:
                if cancelled():
                    stats["stopped"] = True
                    break
                plat, rows = P.PLATFORMS[pid], by_pid[pid]
                cands: dict[str, dict] = {}
                if igdb and not igdb.stopped:
                    try:
                        hits = await _igdb_top(igdb, plat)
                    except Exception as e:
                        log(f"{plat.name}: classifica IGDB non letta ({type(e).__name__}: {e})")
                        hits = []
                    for r, h in match(hits, rows):
                        g = NetGame(r["key"], r["title"], plat)
                        if save(cache, g, "igdb", igdb._pick(g, [h])):
                            stats["found"] += 1
                        cands[r["key"]] = r
                for r in candidates_by_rating(rows):
                    cands.setdefault(r["key"], r)
                stats["candidates"] += len(cands)
                for src in srcs:
                    if src.stopped or cancelled():
                        continue
                    done = fresh(cache, list(cands), src.name)
                    todo = [NetGame(k, r["title"], plat) for k, r in cands.items() if k not in done]
                    if not todo:
                        continue
                    errors = 0
                    async for g, res in src.lookup_many(todo):
                        stats["asked"] += 1
                        if isinstance(res, Exception):
                            if isinstance(res, Stop):
                                src.stopped = True
                                log(f"{src.name}: {res}")
                            elif errors < 3:
                                errors += 1
                                log(f"{src.name}: {g.search_name}: {type(res).__name__}: {res}")
                            continue
                        if save(cache, g, src.name, res):
                            stats["found"] += 1
                        if cancelled():
                            break
                cache.commit()
                stats["done"] += 1
                log(f"{plat.name}: {len(cands)} candidati")
            for s in srcs:
                await s.close()
    finally:
        ledger.close()
    return stats


def run(cfg, cache, pids=None, log=print, stats=None, cancelled=lambda: False) -> dict:
    return asyncio.run(fetch(cfg, cache, pids, log=log, stats=stats, cancelled=cancelled))
