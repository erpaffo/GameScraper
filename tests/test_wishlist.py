"""Test per la lettura dei prezzi da GG.deals (API v1) e integrazione con la lista desideri. Nessuna rete.

Esegui: python tests/test_wishlist.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from gamescraper import config as C  # noqa: E402
from gamescraper import wishlist as W  # noqa: E402
from gamescraper.library.userdata import UserData  # noqa: E402

HERE = Path(__file__).parent
FIXTURE = json.loads((HERE / "fixtures" / "ggdeals_prices.json").read_text(encoding="utf-8"))


def test_gg_prices_parsing_and_regions():
    assert W._gg_region("IT") == "it"
    assert W._gg_region("UK") == "gb"
    assert W._gg_region("AT") == "eu"  # paese non nell'elenco diretto di GG.deals -> fallback eu

    seen_params = []

    def handler(req: httpx.Request):
        seen_params.append(dict(req.url.params))
        return httpx.Response(200, json=FIXTURE, headers={"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "997"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        # Include un ID duplicato per verificare la deduplicazione
        out = W.gg_prices(c, "FAKE_KEY", ["292030", "202970", "292030", "999999"], "IT", log=lambda *a: None)

    assert len(seen_params) == 1
    assert seen_params[0] == {"ids": "292030,202970,999999", "key": "FAKE_KEY", "region": "it"}, seen_params
    assert set(out) == {"292030", "202970"}, out  # 999999 (null) ignorato
    w3 = out["292030"]
    assert w3["key"] == 8.49 and w3["key_low"] == 5.12 and w3["retail"] == 29.99 and w3["retail_low"] == 2.99, w3
    assert w3["currency"] == "EUR" and w3["url"] == "https://gg.deals/game/the-witcher-3-wild-hunt/"
    bo2 = out["202970"]
    assert bo2["retail"] is None and bo2["key"] == 13.85 and bo2["key_low"] == 9.10, bo2
    print("ok  gg_prices: formato API v1, prezzi stringhe/null, deduplicazione ID e regioni")


def test_gg_error_does_not_block_official(tmp: Path):
    """Un errore di GG.deals (es. success=false o HTTP 429) non blocca il controllo dei prezzi su Steam."""
    def handler(req: httpx.Request):
        if req.url.host == "api.gg.deals":
            return httpx.Response(200, json={"success": False, "data": {"message": "Invalid API key.", "code": 0}})
        return httpx.Response(200, json={"292030": {"success": True, "data": {"price_overview": {
            "final": 999, "initial": 2999, "discount_percent": 67, "currency": "EUR"}}}})

    real_client = W.httpx.Client
    W.httpx.Client = lambda **k: real_client(transport=httpx.MockTransport(handler), **k)
    try:
        u = UserData(tmp / "userdata.db")
        u.wish_add("steam:292030", "The Witcher 3: Wild Hunt", "292030", target=15.0)
        cfg = C.Config(cache=tmp)
        cfg.app = {"wishlist": {"gg_key": "bad_key", "country": "IT"}}
        r = W.check(cfg, u, log=lambda *a: None)
        items = {x["id"]: x for x in u.wishlist()}
        w3 = items["steam:292030"]
        assert w3["price"] == 9.99 and w3["cut"] == 67 and w3["key_price"] is None, w3
        assert r["alerts"] == ["The Witcher 3: Wild Hunt"], r
        u.db.close()
    finally:
        W.httpx.Client = real_client
    print("ok  errore GG.deals gestito senza bloccare i prezzi ufficiali")


if __name__ == "__main__":
    test_gg_prices_parsing_and_regions()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_gg_error_does_not_block_official(Path(t))
    print("TUTTI I TEST OK")
