"""Test del passaggio catalogo online -> libreria: titoli puliti e metadati di copertina/descrizione.

Esegui: PYTHONUTF8=1 python tests/catalog/test_build_store.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pozzo import config, storemeta  # noqa: E402
from pozzo.cache import Cache  # noqa: E402
from pozzo.library import build as B  # noqa: E402

URL = "https://example-b.test/all"


def test_store_title():
    assert B._store_title("Zumba World - The Marble Adventure Switch NSP") == "Zumba World - The Marble Adventure"
    assert B._store_title("Gioco XCI") == "Gioco"
    assert B._store_title("Gioco - NSP (eShop)") == "Gioco"
    assert B._store_title("Gioco v1.2.0 Switch NSP") == "Gioco"
    # «Switch» come parola vera del titolo resta
    assert B._store_title("1-2-Switch") == "1-2-Switch"
    assert B._store_title("Nintendo Switch Sports") == "Nintendo Switch Sports"
    # superpsx: la console in fondo impediva di trovare il gioco in LaunchBox
    assert B._store_title("MLB The Show 21 PS4") == "MLB The Show 21"
    assert B._store_title("Silent Hill 2 PS2-PS4 Ported") == "Silent Hill 2"
    assert B._store_title("Battlefield 6 PS5 FPKG") == "Battlefield 6"
    assert B._store_title("PS4 Pro Test") == "PS4 Pro Test"  # in testa al titolo resta
    print("ok  titoli online: «Switch NSP (eShop)» e «PS4/PS5» in fondo rimossi, titoli veri intatti")


def test_catalog_entries_merge(tmp_path):
    (tmp_path / "roms").mkdir()
    (tmp_path / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                          encoding="utf-8")
    cfg = config.load(tmp_path / "config.toml")
    cfg.catalogs = {"switch": [URL]}
    cfg.catalog_options = {"switch": {"type": "type_b"}}
    cache = Cache(cfg.cache)
    cache.kv_set(f"catalog:type_b:{URL}",
                 {"url": URL, "index_url": URL, "pid": "switch", "time": time.time(),
                  "entries": [{"name": "Gioco Uno Switch NSP", "size": None, "url": "https://example-b.test/uno/",
                               "magnet": None, "group": "G"},
                              {"name": "Gioco Due XCI", "size": None, "url": "https://example-b.test/due/",
                               "magnet": None, "group": "G"}]})
    # dati letti dalla pagina (o da GameTDB) del primo gioco
    storemeta.put(cache, "https://example-b.test/uno/", {
        "cover": "https://art.gametdb.com/switch/coverHQ/US/AAAAA.jpg",
        "covers": ["https://art.gametdb.com/switch/coverHQ/US/AAAAA.jpg",
                   "https://art.gametdb.com/switch/coverHQ/EN/AAAAA.jpg"],
        "screenshots": ["https://example-b.test/uno-1.jpg"],
        "description": "Descrizione completa del gioco uno, presa dalla pagina dello store.",
        "publisher": "Editore Uno", "players": 2, "genres": ["Action"],
        "release_date": "2024-03-15", "size": 1024 ** 3}, "page")

    entries = {e["name"]: e for e in B._catalog_entries(cfg, cache, {})[0]}
    uno, due = entries["Gioco Uno Switch NSP"], entries["Gioco Due XCI"]
    assert uno["title"] == "Gioco Uno" and due["title"] == "Gioco Due", (uno["title"], due["title"])
    assert uno["description"].startswith("Descrizione completa"), uno["description"]
    assert uno["media"]["cover"].startswith("url:https://art.gametdb.com/switch/coverHQ/US/AAAAA.jpg|"), uno["media"]
    assert uno["media"]["screenshot"] == "url:https://example-b.test/uno-1.jpg", uno["media"]
    assert uno["players"] == "2" and uno["genres"] == ["Action"] and uno["publisher"] == "Editore Uno"
    assert uno["year"] == 2024 and uno["release_date"] == "2024-03-15" and uno["size"] == 1024 ** 3
    # il gioco senza dati resta senza (li riempie il pulsante «Copertine e descrizioni»)
    assert due["description"] is None and due["media"] == {} and due["size"] is None, due
    cache.close()
    print("ok  libreria: descrizione, copertina (con alternative), screenshot e metadati uniti alle voci online")


def test_build_includes_online(tmp_path):
    (tmp_path / "roms").mkdir()
    (tmp_path / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                          encoding="utf-8")
    cfg = config.load(tmp_path / "config.toml")
    cfg.catalogs = {"switch": [URL]}
    cache = Cache(cfg.cache)
    cache.kv_set(f"catalog:type_b:{URL}",
                 {"url": URL, "index_url": URL, "pid": "switch", "time": time.time(),
                  "entries": [{"name": "Gioco Uno Switch NSP", "size": None, "url": "https://example-b.test/uno/",
                               "magnet": None, "group": "G"}]})
    storemeta.put(cache, "https://example-b.test/uno/", {"cover": "https://x.test/c.jpg",
                                                         "description": "Descrizione di prova."}, "page")
    cache.close()
    stats = B.build(cfg, log=lambda m: None)
    assert stats["online"] == 1, stats
    import sqlite3
    db = sqlite3.connect(cfg.cache / "library.db")
    row = db.execute("SELECT title, description, media FROM games WHERE store='online'").fetchone()
    db.close()
    assert row[0] == "Gioco Uno" and row[1] == "Descrizione di prova." and "x.test/c.jpg" in row[2], row
    print("ok  build completa: la voce online arriva in library.db con titolo pulito, descrizione e copertina")


def test_same_game_two_sources(tmp_path):
    """Stesso gioco su due siti (dopo la pulizia del titolo): una voce sola, due fonti da scaricare."""
    (tmp_path / "roms").mkdir()
    (tmp_path / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                          encoding="utf-8")
    cfg = config.load(tmp_path / "config.toml")
    a, b = "https://aaa.test/index", "https://bbb.test/nintendo-switch-nsp/"
    cfg.catalogs = {"switch": [a, b]}
    cache = Cache(cfg.cache)
    cache.kv_set(f"catalog:type_b:{a}",
                 {"url": a, "index_url": a, "pid": "switch", "time": time.time(),
                  "entries": [{"name": "Same Game Switch NSP", "size": 1024, "url": "https://aaa.test/same/",
                               "magnet": "magnet:?xt=urn:btih:AAA", "group": "S"}]})
    cache.kv_set(f"catalog:type_c:{b}",
                 {"url": b, "index_url": b, "pid": "switch", "time": time.time(),
                  "entries": [{"name": "Same Game Switch NSP (eShop)", "size": None, "url": "https://bbb.test/same/",
                               "magnet": None},
                              {"name": "Altro Gioco Switch NSP", "size": None, "url": "https://bbb.test/altro/",
                               "magnet": None}]})
    entries, _pages = B._catalog_entries(cfg, cache, {})
    assert len(entries) == 2, [e["title"] for e in entries]
    same = next(e for e in entries if e["title"] == "Same Game")
    assert [s["site"] for s in same["sources"]] == ["aaa.test", "bbb.test"], same["sources"]
    assert same["sources"][0]["magnet"] == "magnet:?xt=urn:btih:AAA"
    assert same["size"] == 1024  # resta il dato della fonte principale
    only = next(e for e in entries if e["title"] == "Altro Gioco")
    assert len(only["sources"]) == 1, only["sources"]
    cache.close()
    print("ok  stessa partita su due siti: una voce con due fonti, la principale resta la prima")

    stats = B.build(cfg, log=lambda m: None)
    assert stats["online"] == 2, stats
    import json as _json
    import sqlite3
    db = sqlite3.connect(cfg.cache / "library.db")
    launch = _json.loads(db.execute("SELECT launch FROM games WHERE title='Same Game'").fetchone()[0])
    db.close()
    assert [s["url"] for s in launch["sources"]] == ["https://aaa.test/same/", "https://bbb.test/same/"], launch
    print("ok  libreria: i due link sono salvati nella voce (launch.sources)")


def test_owned_game_keeps_store_page(tmp_path):
    """Un gioco già scaricato non compare tra gli online, ma conserva il link alla pagina dello store."""
    from types import SimpleNamespace
    (tmp_path / "roms").mkdir()
    (tmp_path / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                          encoding="utf-8")
    cfg = config.load(tmp_path / "config.toml")
    cfg.catalogs = {"switch": [URL]}
    cache = Cache(cfg.cache)
    cache.kv_set(f"catalog:type_c:{URL}",
                 {"url": URL, "index_url": URL, "pid": "switch", "time": time.time(),
                  "entries": [{"name": "Zelda Test Switch NSP", "size": None,
                               "url": "https://example-z.test/zelda-test/", "magnet": None}]})
    owned_game = SimpleNamespace(key="switch/abc123", platform=SimpleNamespace(id="switch"), stem="Zelda Test",
                                 inner_name="", data={}, search_name="Zelda Test")
    entries, pages = B._catalog_entries(cfg, cache, {"switch/abc123": owned_game})
    assert entries == [], entries  # già posseduto: niente doppione tra gli online
    assert pages["switch/abc123"][0]["url"] == "https://example-z.test/zelda-test/", pages
    assert pages["switch/abc123"][0]["site"] == "example-z.test"
    cache.close()
    print("ok  gioco posseduto: nessun doppione e link allo store conservato per la sua scheda")


def test_library_has_store_pages(tmp_path):
    cfg = make_env(tmp_path) if "make_env" in globals() else None
    if cfg is None:
        (tmp_path / "roms").mkdir()
        (tmp_path / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                              encoding="utf-8")
        cfg = config.load(tmp_path / "config.toml")
    B.build(cfg, log=lambda m: None)
    import sqlite3
    db = sqlite3.connect(cfg.cache / "library.db")
    cols = [r[1] for r in db.execute("PRAGMA table_info(games)")]
    db.close()
    assert "store_pages" in cols, cols
    print("ok  libreria: la colonna store_pages esiste")


def test_content_kind():
    from pozzo.storekind import content_kind, switch_kind, update_base
    assert content_kind("Hollow Knight") == "game"
    assert content_kind("Cool Game DLC") == "dlc"
    assert content_kind("Game X - Season Pass") == "dlc"
    assert content_kind("Hades Soundtrack") == "extra"
    assert content_kind("Gioco [Update v1.2.0]") == "update"
    assert content_kind("Patch Quest") == "game"  # un gioco non è un aggiornamento
    assert content_kind("Blood and Wine", "dlc") == "dlc"  # tipo dichiarato dallo store
    assert update_base("Gioco [Update v1.2.0]") == "Gioco"
    assert switch_kind("0100152000022000") == "game" and switch_kind("0100152000022800") == "update" \
        and switch_kind("0100152000023001") == "dlc" and switch_kind("") == ""
    print("ok  tipo del contenuto: gioco, DLC/add-on, extra, aggiornamento (anche dal Title ID di Switch)")


def test_updates_and_dlc(tmp_path):
    """Gli aggiornamenti non diventano riquadri: sono un download «⬆» del gioco base. Le DLC restano voci
    con il loro tipo (colonna content), riconosciute dal nome o, solo in conferma, dal peso."""
    import sqlite3
    (tmp_path / "roms").mkdir()
    (tmp_path / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                          encoding="utf-8")
    cfg = config.load(tmp_path / "config.toml")
    cfg.catalogs = {"switch": [URL]}
    cache = Cache(cfg.cache)
    gb = 1024 ** 3
    cache.kv_set(f"catalog:type_b:{URL}",
                 {"url": URL, "index_url": URL, "pid": "switch", "time": time.time(), "entries": [
                     {"name": "Zelda Testo Switch NSP", "size": 8 * gb, "url": "https://z.test/base/", "magnet": None, "group": "Z"},
                     {"name": "Zelda Testo Update v1.2.0 Switch NSP", "size": gb, "url": "https://z.test/upd/", "magnet": None, "group": "Z"},
                     {"name": "Zelda Testo - Pack Aggiuntivo Switch NSP", "size": gb, "url": "https://z.test/pack/", "magnet": None, "group": "Z"},
                     {"name": "Zelda Testo DLC Switch NSP", "size": 2 * gb, "url": "https://z.test/dlc/", "magnet": None, "group": "Z"},
                 ]})
    entries, _pages = B._catalog_entries(cfg, cache, {})
    cache.close()
    by = {e["title"]: e for e in entries}
    assert set(by) == {"Zelda Testo", "Zelda Testo - Pack Aggiuntivo", "Zelda Testo DLC"}, list(by)
    assert by["Zelda Testo"]["content"] == "game"
    upd = [s for s in by["Zelda Testo"]["sources"] if s.get("update")]
    assert len(upd) == 1 and upd[0]["url"] == "https://z.test/upd/" and upd[0]["version"] == "1.2.0", upd
    assert by["Zelda Testo DLC"]["content"] == "dlc"
    assert by["Zelda Testo - Pack Aggiuntivo"]["content"] == "dlc", "DLC riconosciuta dal peso (meno di un terzo)"
    print("ok  aggiornamento aggregato al gioco base, DLC riconosciute da nome e (in conferma) dal peso")

    B.build(cfg, log=lambda m: None)
    db = sqlite3.connect(cfg.cache / "library.db")
    rows = dict(db.execute("SELECT title, content FROM games WHERE platform='switch' AND store='online'"))
    launch = json.loads(db.execute("SELECT launch FROM games WHERE title='Zelda Testo'").fetchone()[0])
    db.close()
    assert rows == {"Zelda Testo": "game", "Zelda Testo - Pack Aggiuntivo": "dlc", "Zelda Testo DLC": "dlc"}, rows
    assert [s["url"] for s in launch["sources"] if s.get("update")] == ["https://z.test/upd/"], launch
    print("ok  libreria: colonna content salvata, aggiornamento tra le fonti del gioco base")


def test_update_of_owned_game(tmp_path):
    """Un aggiornamento di un gioco già sul disco non crea una voce: finisce tra i link della sua scheda."""
    from types import SimpleNamespace
    (tmp_path / "roms").mkdir()
    (tmp_path / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                          encoding="utf-8")
    cfg = config.load(tmp_path / "config.toml")
    cfg.catalogs = {"switch": [URL]}
    cache = Cache(cfg.cache)
    cache.kv_set(f"catalog:type_c:{URL}",
                 {"url": URL, "index_url": URL, "pid": "switch", "time": time.time(),
                  "entries": [{"name": "Zelda Test Update v2.0 Switch NSP", "size": None,
                               "url": "https://z.test/upd2/", "magnet": None}]})
    owned = SimpleNamespace(key="switch/abc", platform=SimpleNamespace(id="switch"), stem="Zelda Test",
                            inner_name="", data={}, search_name="Zelda Test")
    entries, pages = B._catalog_entries(cfg, cache, {"switch/abc": owned})
    cache.close()
    assert entries == [], entries
    assert [s["url"] for s in pages["switch/abc"]] == ["https://z.test/upd2/"], pages
    assert pages["switch/abc"][0]["update"] is True and pages["switch/abc"][0]["version"] == "2.0"
    print("ok  aggiornamento di un gioco posseduto: link «⬆» nella scheda, nessun riquadro")


def test_steam_kinds():
    """Steam dichiara il tipo (0 gioco, 4 DLC, 6 software): niente euristiche sul nome, 250 appid per richiesta."""
    import asyncio
    from pozzo.stores import steam
    calls = []
    types = {1: 0, 2: 4, 3: 6}

    class Resp:
        status_code = 200

        def __init__(self, items):
            self._items = items

        def json(self):
            return {"response": {"store_items": self._items}}

    async def fake(client, method, url, **kw):
        ids = [i["appid"] for i in json.loads(kw["params"]["input_json"])["ids"]]
        calls.append(len(ids))
        return Resp([{"appid": i, "type": types.get(i, 0)} for i in ids])

    orig, steam.request = steam.request, fake
    try:
        p = steam.Steam.__new__(steam.Steam)
        p.client, p.log = None, (lambda *a: None)
        kinds = asyncio.run(p._kinds({"1", "2", "3"}))
        many = asyncio.run(p._kinds({str(i) for i in range(501)}))
    finally:
        steam.request = orig
    assert kinds == {"1": "game", "2": "dlc", "3": "app"}, kinds
    assert len(many) == 501 and calls == [3, 250, 250, 1], (len(many), calls)
    print("ok  Steam: tipo dichiarato dallo store (gioco/DLC/software), richieste a blocchi di 250")


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    test_store_title()
    test_content_kind()
    test_steam_kinds()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_updates_and_dlc(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_update_of_owned_game(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_catalog_entries_merge(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_build_includes_online(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_same_game_two_sources(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_owned_game_keeps_store_page(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_library_has_store_pages(Path(t))
    print("TUTTI I TEST OK")
