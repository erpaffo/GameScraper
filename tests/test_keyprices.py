"""Prezzi delle key da allkeyshop (lista desideri): lettura delle pagine vere (fixture del 2026-10-27) e ritmo
delle richieste. Nessuna richiesta online.

Esegui: python tests/test_keyprices.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from gamescraper import keyprices as K  # noqa: E402
from gamescraper.library.userdata import UserData  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
SEARCH, PRODUCT, HISTORY = ((FIX / f).read_text(encoding="utf-8") for f in ("aks_search.json", "aks_product.html", "aks_history.json"))
HK = "https://www.allkeyshop.com/blog/buy-hollow-knight-cd-key-compare-prices/"


def test_parse():
    res = K.parse_search(SEARCH)
    assert len(res) == 21 and res[0]["title"] == "Hollow Knight Silksong", res[:2]
    # "Hollow Knight": non Silksong (primo risultato), non le console, non la pagina degli account Steam
    assert K.pick(res, "Hollow Knight")["url"] == HK
    assert K.pick(res, "Hollow Knight: Silksong")["url"].endswith("buy-hollow-knight-silksong-cd-key-compare-prices/")
    assert K.pick(res, "Celeste") is None

    p = K.parse_product(PRODUCT)
    assert p["product_id"] == "16998" and len(p["offers"]) == 70
    s = K.summary(HK, p, K.parse_history(HISTORY))
    # la key piu' economica non e' un account (quelli costano meno ma non sono key) ne' uno store ufficiale
    assert s["key"] == {"shop": "GAMIVO", "price": 6.01, "original": 7.7, "coupon": "SUPERDEAL", "official": False,
                        "account": False, "region": "STEAM GLOBAL", "edition": "Standard", "platform": "steam"}, s["key"]
    assert s["aks_steam"] == 14.79
    assert s["key_low"] == 1.86 and s["key_low_date"] == "2026-03-20"
    assert s["steam_low"] == 7.39 and s["steam_low_date"], s
    print("ok  ricerca, offerte (key, account, store ufficiali) e minimi storici di Steam e delle key")


class FakeSleep:
    def __init__(self):
        self.waits = []

    def __call__(self, s):
        self.waits.append(s)


def handler(calls, status=200):
    def h(req: httpx.Request):
        u = str(req.url)
        calls.append(u)
        if status != 200 and "allkeyshop" in u:
            return httpx.Response(status)
        if "admin-ajax.php" in u:
            return httpx.Response(200, text=SEARCH)
        if "price_history_api" in u:
            assert "normalised_name=16998" in u
            return httpx.Response(200, text=HISTORY)
        if u.startswith(HK) or "hollow-knight" in u:
            return httpx.Response(200, text=PRODUCT)
        if "appdetails" in u:
            return httpx.Response(200, json={"367520": {"success": True, "data": {"price_overview": {
                "currency": "EUR", "initial": 1479, "final": 739, "discount_percent": 50}}}})
        return httpx.Response(404)
    return h


def test_refresh():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        user = UserData(Path(t) / "u.db")
        user.wish_add("steam:367520", "Hollow Knight", "367520")
        user.wish_add("t:celeste", "Celeste")
        calls, sleep = [], FakeSleep()
        thr = K.Throttle(gap=60, jitter=0, sleep=sleep)
        c = httpx.Client(transport=httpx.MockTransport(handler(calls)))
        assert K.refresh(user, throttle=thr, client=c, log=lambda m: None) == 2
        aks = [u for u in calls if "allkeyshop" in u]
        assert len(aks) == 4, aks  # Hollow Knight: ricerca + pagina + storico; Celeste: solo la ricerca (non trovato)
        assert len(sleep.waits) == 3 and all(59 < w <= 60 for w in sleep.waits), sleep.waits  # 60 s fra le richieste
        w = {x["id"]: x for x in user.wishlist()}
        hk = json.loads(w["steam:367520"]["aks"])
        assert hk["steam"] == {"price": 7.39, "regular": 14.79, "cut": 50, "currency": "EUR",
                               "url": "https://store.steampowered.com/app/367520"}
        assert w["steam:367520"]["key_price"] == 6.01 and w["steam:367520"]["key_url"] == HK
        assert json.loads(w["t:celeste"]["aks"]) == {"missing": True}

        # il giorno stesso non si richiede piu' nulla
        calls.clear()
        assert K.refresh(user, throttle=thr, client=c) == 0 and not calls

        # il giorno dopo: solo la pagina del gioco (storico gia' scaricato questo mese); un prezzo piu' basso del
        # minimo salvato diventa il nuovo minimo
        hk = json.loads({x["id"]: x for x in user.wishlist()}["steam:367520"]["aks"])
        user.wish_update("steam:367520", aks_checked=None, aks=json.dumps({**hk, "key_low": 9.0, "steam_low": 20.0}))
        K.refresh(user, throttle=thr, client=c)
        assert [u for u in calls if "allkeyshop" in u] == [HK], calls
        hk = json.loads({x["id"]: x for x in user.wishlist()}["steam:367520"]["aks"])
        today = __import__("time").strftime("%Y-%m-%d")
        assert (hk["key_low"], hk["key_low_date"], hk["steam_low"], hk["steam_low_date"]) == (6.01, today, 7.39, today), hk

        # indirizzo incollato dall'utente: niente ricerca, ma pagina e storico
        calls.clear()
        user.wish_update("steam:367520", aks_checked=None, aks=json.dumps({"url": HK}))
        K.refresh(user, throttle=thr, client=c)
        aks = [u for u in calls if "allkeyshop" in u]
        assert len(aks) == 2 and aks[0] == HK and "price_history_api" in aks[1], aks

        # il sito rifiuta: ci si ferma subito e non si riprova fino alla fine della pausa
        user.wish_update("steam:367520", aks_checked=None)
        user.wish_update("t:celeste", aks_checked=None)
        calls.clear()
        block = {"until": 0.0}
        c429 = httpx.Client(transport=httpx.MockTransport(handler(calls, status=429)))
        n = K.refresh(user, throttle=thr, client=c429, log=lambda m: None,
                      block_until=lambda: block["until"], set_block=lambda v: block.update(until=v))
        assert n == 0 and len(calls) == 1 and block["until"] > 0, calls
        assert K.refresh(user, throttle=thr, client=c429, block_until=lambda: block["until"]) == 0 and len(calls) == 1
        assert json.loads({x["id"]: x for x in user.wishlist()}["steam:367520"]["aks"])["product_id"] == "16998"  # dati vecchi intatti
        user.db.close()
    print("ok  una richiesta al minuto, una volta al giorno per gioco, stop immediato se il sito rifiuta")


def test_api():
    """Aggiunta -> parte il giro di allkeyshop; dettaglio con offerte e coda; indirizzo incollato dall'utente."""
    from fastapi.testclient import TestClient
    from gamescraper import config, wishlist as W
    from gamescraper.app.server import create_app
    runs = []
    real_refresh, real_check = K.refresh, W.check
    K.refresh = lambda user, *a, **kw: runs.append(1) or 0
    W.check = lambda *a, **kw: {"checked": 0, "alerts": []}
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            cfg = config.Config(cache=Path(t))
            cfg.config_path = Path(t) / "config.toml"
            with TestClient(create_app(cfg)) as c:
                d = c.post("/api/wishlist/add", json={"title": "Hollow Knight", "steam_appid": "367520"}).json()
                assert d["aks"]["queued"] == 1 and d["items"][0]["aks_status"] is None and "aks" not in d["items"][0]
                p = c.get("/api/wishlist/prices/steam:367520").json()
                assert p["queue"] == 0 and p["wait"] > 0 and p["aks"] == {}, p
                user = UserData(Path(t) / "userdata.db")
                s = K.summary(HK, K.parse_product(PRODUCT), K.parse_history(HISTORY))
                user.wish_update("steam:367520", aks=json.dumps(s), aks_checked=__import__("time").time())
                p = c.get("/api/wishlist/prices/steam:367520").json()
                assert p["queue"] is None and p["aks"]["key"]["price"] == 6.01 and len(p["aks"]["offers"]) == 70
                assert c.get("/api/wishlist").json()["items"][0]["aks_status"] == "ok"
                assert c.post("/api/wishlist/prices/steam:367520", json={"url": "https://evil.example/x"}).status_code == 400
                p = c.post("/api/wishlist/prices/steam:367520", json={"url": HK}).json()
                assert p["aks"] == {"url": HK} and p["queue"] == 0
                assert c.get("/api/wishlist/prices/nope").status_code == 404
                user.db.close()
            for _ in range(50):  # i giri partono in thread
                if len(runs) >= 2:
                    break
                __import__("time").sleep(0.05)
            assert len(runs) >= 2, runs
    finally:
        K.refresh, W.check = real_refresh, real_check
    print("ok  API: aggiunta avvia la ricerca, dettaglio con offerte e coda, indirizzo allkeyshop a mano")


if __name__ == "__main__":
    test_parse()
    test_refresh()
    test_api()
    print("TUTTI I TEST OK")
