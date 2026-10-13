"""Test del gestore dello scraping: budget per fonte, pause su errori, cache delle risposte, voti combinati.

Esegui: python tests/test_policy.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from gamescraper import config, net  # noqa: E402
from gamescraper.library.ratings import combine  # noqa: E402
from gamescraper.net import RateLimiter, Stop, request  # noqa: E402
from gamescraper.policy import Ledger, Policy, policy_for  # noqa: E402


def test_budget(tmp: Path):
    ledger = Ledger(tmp / "a.db")
    lim = RateLimiter(100, 4).guard(Policy("prova", rate=100, daily=5), ledger)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            for _ in range(10):
                await request(c, "GET", "https://x.test/", limiter=lim)
    try:
        asyncio.run(go())
        raise AssertionError("doveva fermarsi al limite giornaliero")
    except Stop as e:
        assert "giornaliero" in str(e)
    assert calls["n"] == 5, calls
    ledger.flush()
    assert ledger.used("prova") == (5, 5)
    # il conteggio resta su disco: una nuova sessione non puo' superarlo
    ledger2 = Ledger(tmp / "a.db")
    assert ledger2.remaining(Policy("prova", daily=5)) == (0, None)
    ledger.close(), ledger2.close()
    print("ok  budget giornaliero rispettato e salvato tra le sessioni")


def test_breaker(tmp: Path):
    ledger = Ledger(tmp / "b.db")
    pol = Policy("lento", rate=1000, errors=2, cooldown=0.3)
    lim = RateLimiter(1000, 1).guard(pol, ledger, log=lambda m: None)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            orig = asyncio.sleep

            async def fast_sleep(s):  # niente attese lunghe nel test (salvo le pause dell'interruttore)
                await orig(min(s, 0.01))
            net.asyncio.sleep = fast_sleep
            try:
                for _ in range(20):
                    await request(c, "GET", "https://x.test/", limiter=lim, retries=2)
            finally:
                net.asyncio.sleep = orig
    t0 = time.time()
    try:
        asyncio.run(go())
        raise AssertionError("doveva sospendere la fonte")
    except Stop as e:
        assert "sospesa" in str(e)
    assert calls["n"] <= 7, calls  # 3 serie da 2 errori, poi stop
    ledger.close()
    print(f"ok  errori ripetuti: pause automatiche e sospensione dopo {calls['n']} tentativi ({time.time() - t0:.1f} s)")


def test_http_cache(tmp: Path):
    net.HTTP_CACHE = net.HttpCache(tmp / "c.db")
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            a = await request(c, "GET", "https://x.test/s", params={"q": "zelda", "key": "SEGRETO1"}, cache_ttl=60)
            b = await request(c, "GET", "https://x.test/s", params={"q": "zelda", "key": "SEGRETO2"}, cache_ttl=60)
            return a, b
    try:
        a, b = asyncio.run(go())
        assert calls["n"] == 1 and b.json() == {"ok": True} and b.headers.get("x-gamescraper-cache") == "hit"
        raw = (tmp / "c.db").read_bytes()
        assert b"SEGRETO" not in raw, "le chiavi API non devono finire nella cache"
    finally:
        net.HTTP_CACHE.close()
        net.HTTP_CACHE = None
    print("ok  cache delle risposte: seconda richiesta non inviata, chiavi API non salvate")


def test_ratings():
    out = combine({"launchbox": {"rating": "0.92", "rating_count": "1800"}, "igdb": {"rating": 0.85, "rating_count": 40,
                   "rating_critics": 0.9}, "screenscraper": {"rating": "0.8"}, "rawg": {"rating_critics": 0.88}})
    assert 0.88 < out["rating"] < 0.92, out
    assert out["rating_count"] == 1840 and out["rating_critics"] == 0.88  # Metacritic (RAWG) prima di IGDB
    few = combine({"igdb": {"rating": 1.0, "rating_count": 1}})
    assert few["rating"] < 0.7, few  # un solo voto conta poco
    print(f"ok  voti combinati pesati sul numero di voti ({out['rating'] * 5:.2f}/5), critica separata")


def test_policy_config(tmp: Path):
    (tmp / "config.toml").write_text('[paths]\nroms="."\n[limits.rawg]\ndaily = 100\n', encoding="utf-8")
    cfg = config.load(tmp / "config.toml")
    p = policy_for(cfg, "rawg")
    assert p.daily == 100 and p.monthly == 19000
    config.save_settings(cfg, limits={"rawg": {"rate": 0.5}})
    assert policy_for(config.load(tmp / "config.toml"), "rawg").rate == 0.5
    print("ok  limiti configurabili da config.toml e dall'app")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        test_budget(tmp)
        test_breaker(tmp)
        test_http_cache(tmp)
        test_ratings()
        test_policy_config(tmp)
    print("TUTTI I TEST OK")
