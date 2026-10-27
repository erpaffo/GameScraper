"""Test dei migliori giochi: punteggio affidabile, Top per console, classifica globale, abbinamento IGDB. Nessuna rete.

Esegui: python tests/test_topgames.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamescraper import platforms as P
from gamescraper import topgames as T
from gamescraper.cache import Cache


def g(i, title, pid, rating=None, votes=0, critics=None, sources=1, store=None, primary=1):
    return {"id": i, "key": f"{pid}/k{i}", "title": title, "platform": pid, "year": 2000, "is_primary": primary,
            "store": store, "rating": rating, "rating_count": votes, "rating_critics": critics,
            "ratings": json.dumps([{"source": f"s{n}", "name": f"S{n}"} for n in range(sources)])}


def test_score():
    assert T.score(g(1, "A", "gc", rating=0.95, votes=5)) is None  # 1 fonte, pochi voti: non affidabile
    assert T.score(g(1, "A", "gc", rating=0.9, votes=60)) is not None  # tanti voti
    assert T.score(g(1, "A", "gc", rating=0.9, votes=5, sources=2)) is not None  # due fonti
    assert T.score(g(1, "A", "gc", critics=0.9)) == 0.9  # solo critica
    both = T.score(g(1, "A", "gc", rating=0.8, votes=1000, critics=0.9))
    assert 0.84 < both < 0.86, both  # media fra critica e giocatori
    few, many = T.score(g(1, "A", "gc", rating=0.9, votes=2, sources=2)), T.score(g(1, "A", "gc", rating=0.9, votes=500))
    assert few < many  # pochi voti contano meno
    print("ok  punteggio: soglia di affidabilita', critica + giocatori, pochi voti penalizzati")


def test_local_and_global():
    games = [
        g(1, "Resident Evil 4", "gc", critics=0.96, store=None),
        g(2, "Resident Evil 4", "ps2", critics=0.98, store="online"),
        g(3, "Resident Evil 4", "wii", critics=0.91, store="online"),
        g(4, "Shenmue", "dreamcast", critics=0.89, store="online"),
        g(5, "Brutto", "gc", rating=0.3, votes=200),
        g(6, "Poco votato", "gc", rating=0.99, votes=3),
        g(7, "Resident Evil 4", "gc", critics=0.96, primary=0),  # versione regionale: non conta
    ]
    loc = T.local(games, "gc")
    assert [e["id"] for e in loc] == [1, 5], loc  # "Poco votato" escluso, secondaria esclusa
    glob = T.global_(games)
    assert [e["title"] for e in glob] == ["Resident Evil 4", "Shenmue", "Brutto"], glob
    re4 = glob[0]
    assert re4["id"] == 1 and re4["owned"], re4  # versione posseduta, anche se la PS2 ha un voto piu' alto
    assert re4["also"] == ["ps2", "wii"] and re4["best"] == 0.98, re4
    assert len(T.global_([g(i, f"G{i}", "gc", critics=0.5 + i / 10000) for i in range(1, 1500)])) == 1000
    print("ok  classifiche: Top per console e globale (stesso gioco = una voce, prima la posseduta, max 1000)")


def test_match_and_netgame():
    rows = [{"key": "dreamcast/net:aa", "title": "Soul Calibur"}, {"key": "dreamcast/net:bb", "title": "Jet Grind Radio"}]
    hits = [{"name": "SoulCalibur"}, {"name": "Jet Set Radio", "alternative_names": [{"name": "Jet Grind Radio"}]},
            {"name": "Non in libreria"}]
    m = T.match(hits, rows)
    assert [r["key"] for r, _ in m] == ["dreamcast/net:bb"], m  # "SoulCalibur" != "Soul Calibur" dopo normalize
    rows[0]["title"] = "SoulCalibur"
    assert len(T.match(hits, rows)) == 2
    ng = T.NetGame("dreamcast/net:aa", "SoulCalibur: Edition", P.PLATFORMS["dreamcast"])
    assert ng.key == "dreamcast/net:aa" and ng.search_name == "SoulCalibur: Edition"
    print("ok  abbinamento IGDB per titolo e nomi alternativi; NetGame tiene la chiave della libreria")


def test_save_and_fresh():
    class R:
        fields = {"rating": 0.9, "rating_count": 100, "genres": ["Fighting"]}
        media = {"cover": "https://img/x.jpg"}
    with tempfile.TemporaryDirectory() as d:
        c = Cache(Path(d))
        try:
            plat = P.PLATFORMS["dreamcast"]
            net, own = T.NetGame("dreamcast/net:aa", "X", plat), T.NetGame("dreamcast/123:4", "Y", plat)
            assert T.save(c, net, "igdb", R()) and T.save(c, own, "igdb", R())
            assert not T.save(c, T.NetGame("dreamcast/net:cc", "Z", plat), "igdb", None)
            res = c.all_resources(["dreamcast/net:aa", "dreamcast/123:4"])
            assert res["dreamcast/net:aa"]["igdb"]["url:cover"] == "https://img/x.jpg"
            assert "url:cover" not in res["dreamcast/123:4"]["igdb"]  # i posseduti hanno gia' le loro immagini
            assert T.fresh(c, ["dreamcast/net:aa", "dreamcast/net:cc", "dreamcast/net:zz"], "igdb") == {
                "dreamcast/net:aa", "dreamcast/net:cc"}  # anche "non trovato" non si richiede
            assert T.fresh(c, ["dreamcast/net:aa"], "igdb", max_age=-1) == set()
        finally:
            c.close()
    print("ok  voti salvati come lo scraping normale, ripresa senza richieste ripetute")


def test_build_uses_online():
    from gamescraper.library.build import _apply_online
    from gamescraper.library.ratings import combine
    with tempfile.TemporaryDirectory() as d:
        c = Cache(Path(d))
        try:
            key = "dreamcast/net:aa"
            for f, v in {"rating": 0.9, "rating_count": 300, "rating_critics": 0.93, "description": "Picchiaduro",
                         "url:cover": "https://img/c.jpg", "release_date": "1999-08-05"}.items():
                c.put(key, "igdb", f, v)
            e = {"key": key, "description": None, "developer": None, "publisher": None, "release_date": None,
                 "players": None, "genres": [], "year": None, "media": {},
                 "ratings": combine({"launchbox": {"rating": 0.8, "rating_count": 40}})}
            _apply_online(c, [e])
            assert {r["source"] for r in e["ratings"]["ratings"]} == {"launchbox", "igdb"}, e["ratings"]
            assert e["ratings"]["rating_critics"] == 0.93
            assert e["description"] == "Picchiaduro" and e["year"] == 1999
            assert e["media"]["cover"] == "url:https://img/c.jpg"
        finally:
            c.close()
    print("ok  libreria: le voci da scaricare usano i voti online (LaunchBox + IGDB)")


if __name__ == "__main__":
    test_score()
    test_local_and_global()
    test_match_and_netgame()
    test_save_and_fresh()
    test_build_uses_online()
    print("TUTTI I TEST OK")
