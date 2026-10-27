"""Test del passaggio catalogo online -> libreria: titoli puliti e metadati di copertina/descrizione.

Esegui: PYTHONUTF8=1 python tests/catalog/test_build_store.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gamescraper import config, storemeta  # noqa: E402
from gamescraper.cache import Cache  # noqa: E402
from gamescraper.library import build as B  # noqa: E402

URL = "https://example-b.test/all"


def test_store_title():
    assert B._store_title("Zumba World - The Marble Adventure Switch NSP") == "Zumba World - The Marble Adventure"
    assert B._store_title("Gioco XCI") == "Gioco"
    assert B._store_title("Gioco - NSP (eShop)") == "Gioco"
    assert B._store_title("Gioco v1.2.0 Switch NSP") == "Gioco"
    # «Switch» come parola vera del titolo resta
    assert B._store_title("1-2-Switch") == "1-2-Switch"
    assert B._store_title("Nintendo Switch Sports") == "Nintendo Switch Sports"
    print("ok  titoli online: «Switch NSP (eShop)» rimosso, titoli veri intatti")


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

    entries = {e["name"]: e for e in B._catalog_entries(cfg, cache, {})}
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
    entries = B._catalog_entries(cfg, cache, {})
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


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    test_store_title()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_catalog_entries_merge(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_build_includes_online(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_same_game_two_sources(Path(t))
    print("TUTTI I TEST OK")
