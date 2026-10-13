"""Rete asincrona: client HTTP/2 condiviso, rate limiter, retry con backoff, download media.

Nell'originale ogni thread faceva una richiesta alla volta e attendeva 1,2 s anche prima di
scaricare ogni immagine. Qui le chiamate API sono limitate solo quanto richiesto dal servizio,
mentre i download dei media procedono in parallelo con un limite di concorrenza per host.
"""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

import httpx

UA = "GameScraper/1.0 (+https://github.com/; based on Skyscraper by Lars Muldjord)"


class RateLimiter:
    """Al massimo `rate` richieste al secondo (spaziatura uniforme) + max `concurrency` in volo.

    Se collegato a una Policy e a un Ledger (vedi policy.py e guard()):
    - prima di ogni richiesta controlla il budget giornaliero/mensile della fonte (Stop se esaurito)
    - conta ogni richiesta nel registro persistente
    - dopo N errori consecutivi (429, 5xx, timeout) mette in pausa la fonte; dopo 3 pause di fila la ferma
    """

    def __init__(self, rate: float, concurrency: int = 1):
        self.interval = 1.0 / rate if rate > 0 else 0
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self._lock = asyncio.Lock()
        self._next = 0.0
        self.policy = None
        self.ledger = None
        self.counts = True
        self.fails = 0
        self.pauses = 0
        self.paused_until = 0.0
        self.log = None

    def guard(self, policy, ledger, counts: bool = True, log=None):
        """Collega le regole della fonte. Il ritmo non supera mai quello della policy."""
        self.policy, self.ledger, self.counts, self.log = policy, ledger, counts, log
        if policy and policy.rate > 0 and counts:
            self.interval = max(self.interval, 1.0 / policy.rate)
        return self

    def report(self, ok: bool):
        """Esito di una richiesta, per l'interruttore automatico in caso di errori ripetuti."""
        if ok:
            self.fails = 0
            self.pauses = 0
            return
        self.fails += 1
        p = self.policy
        if p and self.fails >= p.errors:
            self.fails = 0
            self.pauses += 1
            if self.pauses >= 3:
                raise Stop(f"{p.source}: troppi errori di fila, fonte sospesa per questa sessione")
            self.paused_until = time.monotonic() + p.cooldown
            if self.log:
                self.log(f"{p.source}: errori ripetuti, pausa di {int(p.cooldown)} s per non sovraccaricare il sito")

    async def __aenter__(self):
        if self.paused_until > time.monotonic():
            await asyncio.sleep(self.paused_until - time.monotonic())
        if self.ledger and self.policy and self.counts:
            day_left, month_left = self.ledger.remaining(self.policy)
            if (day_left is not None and day_left <= 0) or (month_left is not None and month_left <= 0):
                which = "giornaliero" if day_left is not None and day_left <= 0 else "mensile"
                raise Stop(f"{self.policy.source}: raggiunto il limite {which} impostato (vedi Impostazioni > Fonti)")
        await self.sem.acquire()
        if self.interval:
            async with self._lock:
                now = time.monotonic()
                wait = self._next - now
                self._next = max(now, self._next) + self.interval
            if wait > 0:
                await asyncio.sleep(wait)
        if self.ledger and self.policy and self.counts:
            self.ledger.spend(self.policy.source)
        return self

    async def __aexit__(self, *exc):
        self.sem.release()

    def slow_down(self, factor: float = 1.5, max_interval: float = 10):
        self.interval = min(max(self.interval, 0.1) * factor, max_interval)


class Stop(Exception):
    """La sorgente non puo' piu' rispondere (quota esaurita, blacklist, credenziali errate)."""


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(30, connect=15),
        limits=httpx.Limits(max_connections=128, max_keepalive_connections=64),
        headers={"User-Agent": UA},
        follow_redirects=True,
    )


# ---------------------------------------------------------------- cache delle risposte

# parametri da non usare come chiave (credenziali) e da non salvare mai
SECRET_PARAMS = {"key", "apikey", "api_key", "devid", "devpassword", "ssid", "sspassword", "client_secret",
                 "client_id", "access_token", "password"}


class HttpCache:
    """Risposte gia' ottenute, salvate in cache.db: la stessa domanda non viene rifatta al sito
    (utile per ripetizioni, "completa i dati mancanti", crash a meta' lavoro)."""

    def __init__(self, db_path, enabled: bool = True):
        import sqlite3
        self.enabled = enabled
        self.db = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS http_cache(key TEXT PRIMARY KEY, status INT, body BLOB, "
                        "ctype TEXT, ts REAL)")
        self.db.commit()
        self.hits = 0

    @staticmethod
    def key(method: str, url: str, params=None, content=None, data=None) -> str:
        import hashlib
        import json as _json
        clean = {k: v for k, v in sorted((params or {}).items()) if k.lower() not in SECRET_PARAMS}
        raw = _json.dumps([method.upper(), url, clean, content if isinstance(content, str) else
                           (content.decode("utf-8", "replace") if isinstance(content, bytes) else None),
                           {k: v for k, v in sorted((data or {}).items()) if k.lower() not in SECRET_PARAMS}],
                          sort_keys=True, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()

    def get(self, key: str, ttl: float):
        if not self.enabled:
            return None
        r = self.db.execute("SELECT status, body, ctype, ts FROM http_cache WHERE key=?", (key,)).fetchone()
        if not r or time.time() - r[3] > ttl:
            return None
        self.hits += 1
        return httpx.Response(r[0], content=r[1], headers={"content-type": r[2] or "application/json",
                                                           "x-gamescraper-cache": "hit"})

    def put(self, key: str, r: httpx.Response):
        if not self.enabled or r.status_code not in (200, 404):
            return
        self.db.execute("INSERT OR REPLACE INTO http_cache VALUES(?,?,?,?,?)",
                        (key, r.status_code, r.content, r.headers.get("content-type", ""), time.time()))
        self.db.commit()

    def close(self):
        self.db.close()


HTTP_CACHE: HttpCache | None = None  # impostata dalla pipeline durante uno scraping


async def request(client: httpx.AsyncClient, method: str, url: str, *, limiter: RateLimiter | None = None,
                  retries: int = 4, ok404: bool = True, cache_ttl: float = 0, **kw) -> httpx.Response | None:
    """Richiesta con retry su errori di rete / 429 / 5xx. Ritorna None su 404.

    cache_ttl > 0: la risposta viene riusata (senza contattare il sito) per quei secondi."""
    ckey = None
    if cache_ttl and HTTP_CACHE is not None:
        ckey = HttpCache.key(method, url, kw.get("params"), kw.get("content"), kw.get("data"))
        hit = HTTP_CACHE.get(ckey, cache_ttl)
        if hit is not None:
            return None if hit.status_code == 404 and ok404 else hit
    delay = 2.0
    r = None
    for attempt in range(retries):
        try:
            if limiter:
                async with limiter:
                    r = await client.request(method, url, **kw)
            else:
                r = await client.request(method, url, **kw)
        except (httpx.TransportError, httpx.TimeoutException):
            if limiter:
                limiter.report(False)
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay + random.random())
            delay *= 2
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if limiter:
                if r.status_code == 429:
                    limiter.slow_down()
                limiter.report(False)
            ra = r.headers.get("Retry-After")
            await asyncio.sleep(min(float(ra), 600) if ra and ra.isdigit() else delay + random.random())
            delay *= 2
            continue
        if limiter:
            limiter.report(True)
        if ckey:
            HTTP_CACHE.put(ckey, r)
        if r.status_code == 404 and ok404:
            return None
        return r
    return r


# ---------------------------------------------------------------- media

MAGIC = [
    (b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"), (b"RIFF", "webp"),
    (b"\x1aE\xdf\xa3", "webm"), (b"BM", "bmp"),
]
MIN_SIZE = 256


def sniff(data: bytes) -> str | None:
    for magic, ext in MAGIC:
        if data.startswith(magic):
            return ext
    if data[4:8] == b"ftyp":
        return "mp4"
    return None


async def download_media(client, url: str, dest_noext: Path, *, limiter: RateLimiter | None = None,
                         headers: dict | None = None) -> Path | None:
    """Scarica un file media, verifica che sia davvero un'immagine/video e lo salva con l'estensione giusta."""
    try:
        r = await request(client, "GET", url, limiter=limiter, headers=headers, retries=3)
    except (httpx.HTTPError, OSError):
        return None
    if r is None or r.status_code != 200 or len(r.content) < MIN_SIZE:
        return None
    ext = sniff(r.content[:16])
    if not ext:
        return None
    dest = dest_noext.with_suffix("." + ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    await asyncio.to_thread(tmp.write_bytes, r.content)
    await asyncio.to_thread(tmp.replace, dest)
    return dest
