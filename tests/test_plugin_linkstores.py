"""Estensione «Link → Store»: caricamento del plugin ed endpoint."""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamescraper import config as C  # noqa: E402
from gamescraper.app import extensions  # noqa: E402

BACKEND = Path(extensions.BUILTIN_PLUGINS) / "linkstores" / "backend.py"


class FakeGS:
    """Sostituto di PluginAPI: raccoglie gli endpoint invece di aggiungerli a FastAPI."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.routes = {}
        self.log = lambda m: None

    def route(self, path, methods=("GET",)):
        def deco(fn):
            self.routes[path.lstrip("/")] = fn
            return fn
        return deco


def _plugin(cfg):
    spec = importlib.util.spec_from_file_location("gs_plugin_linkstores_test", BACKEND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    gs = FakeGS(cfg)
    mod.register(gs)
    return gs


def _cfg(tmp_path: Path) -> C.Config:
    cfg = C.Config(cache=tmp_path / "cache")
    cfg.config_path = tmp_path / "config.toml"
    return cfg


def test_plugin_riconosciuto(tmp_path):
    cfg = _cfg(tmp_path)
    found = {p["id"]: p for p in extensions.list_plugins(cfg)}
    assert "linkstores" in found
    p = found["linkstores"]
    assert p["enabled"] and p["name"] == "Link → Store"
    assert p["frontend"] and p["backend"]


def test_endpoint_state(tmp_path):
    gs = _plugin(_cfg(tmp_path))
    assert set(gs.routes) == {"state", "scan", "add"}
    st = gs.routes["state"]()
    plats = [p["id"] for p in st["platforms"]]
    assert plats[0] == "windows"  # il PC per primo: è la destinazione più frequente
    assert st["platforms"][0]["pc"] is True
    assert "switch" in plats and st["platforms"][plats.index("switch")]["pc"] is False
    assert st["catalogs"] == {} and st["pc_stores"] == []


def test_endpoint_add_store_pc(tmp_path):
    cfg = _cfg(tmp_path)
    gs = _plugin(cfg)
    r = gs.routes["add"]({"url": "https://pc.test/elenco/", "platform": "pc", "kind": "repack"})
    assert r["added"] is True and r["kind"] == "repack"
    assert r["state"]["pc_stores"] == [{"url": "https://pc.test/elenco/", "kind": "repack"}]
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["pc_stores"][0]["url"] == "https://pc.test/elenco/"


def test_endpoint_add_store_console(tmp_path):
    cfg = _cfg(tmp_path)
    gs = _plugin(cfg)
    r = gs.routes["add"]({"url": "https://sw.test/list/", "platform": "switch"})
    assert r["added"] is True and r["platform"] == "switch"
    assert r["state"]["catalogs"] == {"switch": ["https://sw.test/list/"]}

    again = gs.routes["add"]({"url": "https://sw.test/list/", "platform": "switch"})
    assert again["added"] is False
    assert len(again["state"]["catalogs"]["switch"]) == 1


def test_endpoint_add_indirizzo_non_valido(tmp_path):
    from fastapi import HTTPException
    gs = _plugin(_cfg(tmp_path))
    try:
        gs.routes["add"]({"url": "non-un-indirizzo", "platform": "switch"})
    except HTTPException as e:
        assert e.status_code == 400
        return
    raise AssertionError("doveva rispondere 400")
