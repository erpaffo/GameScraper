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

from pozzo import config as C  # noqa: E402
from pozzo import wishlist as W  # noqa: E402
from pozzo.library.userdata import UserData  # noqa: E402

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


def test_itad_game_without_appid_gets_keys_and_detail(tmp: Path):
    """Gioco aggiunto dalla ricerca ITAD (nessun codice Steam): si trova il codice, GG.deals da' la key, il dettaglio e' salvato."""
    def handler(req: httpx.Request):
        h, path = req.url.host, req.url.path
        if h == "store.steampowered.com" and path.endswith("/storesearch/"):
            return httpx.Response(200, json={"items": [{"id": 1, "name": "Altro gioco"}, {"id": 292030, "name": "The Witcher 3: Wild Hunt"}]})
        if h == "store.steampowered.com":
            return httpx.Response(200, json={"292030": {"success": True, "data": {"price_overview": {
                "final": 999, "initial": 2999, "discount_percent": 67, "currency": "EUR"}}}})
        if h == "api.gg.deals":
            assert req.url.params["ids"] == "292030"
            return httpx.Response(200, json=FIXTURE)
        if path.endswith("/games/prices/v3"):
            return httpx.Response(200, json=[{"id": "itad-1", "historyLow": {"all": {"amount": 4.99}}, "deals": [
                {"shop": {"name": "Steam"}, "price": {"amount": 9.99, "currency": "EUR"}, "regular": {"amount": 29.99}, "cut": 67, "url": "https://s"},
                {"shop": {"name": "GOG"}, "price": {"amount": 12.0, "currency": "EUR"}, "regular": {"amount": 29.99}, "cut": 60, "url": "https://g"}]}])
        if path.endswith("/games/history/v2"):
            assert req.url.params["shops"] == "61"
            return httpx.Response(200, json=[
                {"timestamp": "2026-01-02T10:00:00+01:00", "shop": {"id": 61}, "deal": {"price": {"amount": 29.99}}},
                {"timestamp": "2026-03-01T10:00:00+01:00", "shop": {"id": 61}, "deal": {"price": {"amount": 9.99}}},
                {"timestamp": "2026-02-01T10:00:00+01:00", "shop": {"id": 16}, "deal": {"price": {"amount": 1.0}}}])
        raise AssertionError(req.url)

    real_client = W.httpx.Client
    W.httpx.Client = lambda **k: real_client(transport=httpx.MockTransport(handler), **k)
    try:
        u = UserData(tmp / "userdata2.db")
        u.wish_add("itad:itad-1", "The Witcher 3: Wild Hunt", "", "itad-1", cover="https://img/c.jpg")
        cfg = C.Config(cache=tmp)
        cfg.app = {"wishlist": {"gg_key": "k", "itad_key": "i", "country": "IT"}}
        W.check(cfg, u, log=print)
        w = u.wishlist()[0]
        assert w["steam_appid"] == "292030" and w["cover"] == "https://img/c.jpg", w
        assert w["key_price"] == 8.49 and w["key_low"] == 5.12, w
        d = json.loads(w["aks"])
        assert d["steam"]["price"] == 9.99 and d["steam"]["regular"] == 29.99 and d["steam"]["cut"] == 67, d
        assert [o["shop"] for o in d["offers"]] == ["Steam", "GOG"] and d["low"] == 4.99, d
        with real_client(transport=httpx.MockTransport(handler)) as c:
            assert W.itad_history(c, "i", "itad-1", "IT") == [["2026-01-02", 29.99], ["2026-03-01", 9.99]]  # solo Steam, in ordine
        u.db.close()
    finally:
        W.httpx.Client = real_client
    print("ok  gioco ITAD senza codice Steam: codice trovato, key, dettaglio e storico")


if __name__ == "__main__":
    test_gg_prices_parsing_and_regions()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_gg_error_does_not_block_official(Path(t))
        test_itad_game_without_appid_gets_keys_and_detail(Path(t))
    print("TUTTI I TEST OK")
