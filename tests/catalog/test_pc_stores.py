"""Test degli store PC non ufficiali (repack / download diretto). Nessuna rete.

Esegui: PYTHONUTF8=1 python tests/catalog/test_pc_stores.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pozzo import config  # noqa: E402
from pozzo.cache import Cache  # noqa: E402
from pozzo.library import build as B  # noqa: E402

# --------------------------------------------------------------- client HTTP usa-e-chiudi
_LIVE: list = []


def client(cfg):
    """TestClient registrato: a fine test si chiudono i database (su Windows un file aperto resta bloccato)."""
    from fastapi.testclient import TestClient

    from pozzo.app.server import create_app

    api = TestClient(create_app(cfg))
    _LIVE.append(api)
    return api


def teardown_function(_=None):
    """Eseguito da pytest dopo ogni test: spegnimento dell'app e chiusura delle connessioni."""
    while _LIVE:
        api = _LIVE.pop()
        for handler in getattr(api.app.router, "on_shutdown", []):
            try:
                handler()
            except Exception:
                pass
        try:
            api.close()
        except Exception:
            pass

URL = "https://repack.example.test/giochi/"
URL2 = "https://direct.example.test/pc/"


def make_env(tmp: Path):
    (tmp / "roms").mkdir()
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                     encoding="utf-8")
    return config.load(tmp / "config.toml")


def test_config_pc_stores(tmp_path: Path):
    cfg = make_env(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"pc_stores": [
        {"url": URL, "kind": "Repack"},
        {"url": URL2, "kind": "direct"},
        {"url": "ftp://scartato.test/", "kind": "direct"},
        {"url": "https://strano.test/", "kind": "boh"},
        "non-un-oggetto"]}), encoding="utf-8")
    config.apply_settings(cfg)
    assert cfg.pc_stores == [{"url": URL, "kind": "repack"},
                             {"url": URL2, "kind": "direct"},
                             {"url": "https://strano.test/", "kind": "direct"}], cfg.pc_stores
    print("ok  config: repack/direct letti, URL non validi e categorie sconosciute sistemati")


def test_build_pc_entries(tmp_path: Path):
    cfg = make_env(tmp_path)
    cfg.pc_stores = [{"url": URL, "kind": "repack"}, {"url": URL2, "kind": "direct"}]
    cache = Cache(cfg.cache)
    cache.kv_set("catalog:type_b:" + URL,
                 {"url": URL, "index_url": URL, "pid": "windows", "time": time.time(),
                  "entries": [{"name": "Same Game", "size": 20 * 1024 ** 3, "url": "https://repack.example.test/same/",
                               "magnet": None, "group": "S"}]})
    cache.kv_set("catalog:type_c:" + URL2,
                 {"url": URL2, "index_url": URL2, "pid": "windows", "time": time.time(),
                  "entries": [{"name": "Same Game", "size": None, "url": "https://direct.example.test/same/",
                               "magnet": None}]})
    entries, _pages = B._catalog_entries(cfg, cache, {})
    assert len(entries) == 1, entries
    e = entries[0]
    assert e["pid"] == "windows" and e["kind"] == "repack", (e["pid"], e["kind"])
    assert [s["kind"] for s in e["sources"]] == ["repack", "direct"], e["sources"]
    assert [s["site"] for s in e["sources"]] == ["repack.example.test", "direct.example.test"]
    cache.close()
    print("ok  libreria: stesso gioco PC da repack e download diretto in una voce con le due fonti")

    stats = B.build(cfg, log=lambda m: None)
    assert stats["online"] == 1, stats
    import sqlite3
    db = sqlite3.connect(cfg.cache / "library.db")
    platform, store, launch = db.execute("SELECT platform, store, launch FROM games WHERE title='Same Game'").fetchone()
    db.close()
    info = json.loads(launch)
    assert platform == "windows" and store == "online", (platform, store)
    assert info["kind"] == "repack" and [s["kind"] for s in info["sources"]] == ["repack", "direct"], info
    print("ok  build: gioco PC con categoria e due fonti salvate in launch")


def test_api_pc_stores(tmp_path: Path):
    from fastapi.testclient import TestClient
    from pozzo.app.server import create_app
    cfg = make_env(tmp_path)
    api = client(cfg)
    d = api.get("/api/pc-stores").json()
    assert d["stores"] == [] and d["job"]["status"] == "idle", d
    d = api.post("/api/pc-stores", json={"stores": [{"url": URL, "kind": "repack"}], "refresh": False}).json()
    assert len(d["stores"]) == 1 and d["stores"][0]["kind"] == "repack", d
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["pc_stores"]
    assert saved == [{"url": URL, "kind": "repack"}], saved
    assert cfg.pc_stores == [{"url": URL, "kind": "repack"}]
    d = api.post("/api/pc-stores", json={"stores": [{"url": "javascript:alert(1)", "kind": "direct"}],
                                         "refresh": False}).json()
    assert d["stores"] == [], d
    print("ok  API: elenco vuoto, aggiunta salvata in settings.json, indirizzi non http scartati")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_config_pc_stores(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_build_pc_entries(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_api_pc_stores(Path(t))
    print("TUTTI I TEST OK")
