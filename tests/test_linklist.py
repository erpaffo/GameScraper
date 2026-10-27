"""Estensione «Link → Store»: raggruppamento dei link e aggiunta degli store."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamescraper import config as C  # noqa: E402
from gamescraper.linklist import add_store, platform_id, scan_page  # noqa: E402

PAGE = """
<html><head><title>Elenco di siti</title></head><body>
  <nav><a href="https://menu.test/">Menu</a></nav>
  <h2>Siti PC</h2>
  <div><a href="https://uno.test/">Sito Uno</a></div>
  <h3>Sotto sezione</h3>
  <a href="https://due.test/">Sito Due</a>
  <a href="https://due.test/">Sito Due (doppione)</a>
  <h2>Console</h2>
  <a href="https://tre.test/list/">Elenco console</a>
  <a href="/interno/">Pagina interna</a>
  <a href="#ancora">Ancora</a>
  <footer><a href="https://piede.test/">Piede</a></footer>
</body></html>
"""


def test_raggruppa_per_intestazione():
    d = scan_page(PAGE, "https://elenco.test/")
    assert d["title"] == "Elenco di siti"
    assert [g["name"] for g in d["groups"]] == ["Siti PC", "Sotto sezione", "Console"]
    assert [g["level"] for g in d["groups"]] == [2, 3, 2]
    assert [l["text"] for l in d["groups"][0]["links"]] == ["Sito Uno"]
    assert [l["text"] for l in d["groups"][1]["links"]] == ["Sito Due"]  # doppione tolto
    assert d["total"] == 3


def test_ignora_menu_piede_e_ancore():
    d = scan_page(PAGE, "https://elenco.test/")
    url_link = [l["url"] for g in d["groups"] for l in g["links"]]
    assert "https://menu.test/" not in url_link
    assert "https://piede.test/" not in url_link
    assert not any("#" in u for u in url_link)
    # link interni esclusi: la pagina raccoglie altri siti
    assert "https://elenco.test/interno/" not in url_link


def test_link_interni_se_richiesti():
    d = scan_page(PAGE, "https://elenco.test/", external_only=False)
    nomi = [l["text"] for g in d["groups"] for l in g["links"]]
    assert "Pagina interna" in nomi
    assert d["total"] == 4


def test_link_prima_di_ogni_intestazione():
    d = scan_page('<p><a href="https://solo.test/">Solo</a></p><h2>Dopo</h2>'
                  '<a href="https://dopo.test/">Dopo link</a>', "https://elenco.test/")
    assert d["groups"][0]["name"] == "Senza intestazione"
    assert d["groups"][0]["level"] == 0
    assert [g["name"] for g in d["groups"]] == ["Senza intestazione", "Dopo"]


def test_limite_link():
    html = "".join(f'<a href="https://s{i}.test/">Sito {i}</a>' for i in range(10))
    assert scan_page(html, "https://elenco.test/", limit=4)["total"] == 4


def test_platform_id():
    assert platform_id("pc") == "windows"
    assert platform_id("Windows") == "windows"
    assert platform_id("") == "windows"
    assert platform_id("switch") == "switch"


def _cfg(tmp_path: Path) -> C.Config:
    cfg = C.Config(cache=tmp_path / "cache")
    cfg.config_path = tmp_path / "config.toml"  # settings.json finisce nella cartella temporanea
    return cfg


def _settings(tmp_path: Path) -> dict:
    p = tmp_path / "settings.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def test_store_pc_repack(tmp_path):
    cfg = _cfg(tmp_path)
    r = add_store(cfg, "https://pc.test/elenco/", "pc", "repack")
    assert r["added"] and r["platform"] == "windows" and r["kind"] == "repack"
    assert _settings(tmp_path)["pc_stores"] == [{"url": "https://pc.test/elenco/", "kind": "repack"}]
    assert cfg.pc_stores == [{"url": "https://pc.test/elenco/", "kind": "repack"}]

    r2 = add_store(cfg, "https://pc.test/elenco/", "pc", "direct")
    assert r2["added"] is False
    assert len(_settings(tmp_path)["pc_stores"]) == 1


def test_store_pc_categoria_non_valida(tmp_path):
    cfg = _cfg(tmp_path)
    add_store(cfg, "https://pc.test/x/", "windows", "boh")
    assert _settings(tmp_path)["pc_stores"][0]["kind"] == "direct"


def test_store_console(tmp_path):
    cfg = _cfg(tmp_path)
    assert add_store(cfg, "https://sw.test/list/", "switch")["added"]
    assert add_store(cfg, "https://sw.test/list2/", "switch")["added"]
    assert _settings(tmp_path)["catalogs"] == {"switch": ["https://sw.test/list/", "https://sw.test/list2/"]}
    assert cfg.catalogs["switch"] == ["https://sw.test/list/", "https://sw.test/list2/"]


def test_store_console_mantiene_il_tipo(tmp_path):
    cfg = _cfg(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps(
        {"catalogs": {"switch": {"urls": ["https://sw.test/a/"], "type": "type_b"}}}), encoding="utf-8")
    add_store(cfg, "https://sw.test/b/", "switch")
    entry = _settings(tmp_path)["catalogs"]["switch"]
    assert entry == {"urls": ["https://sw.test/a/", "https://sw.test/b/"], "type": "type_b"}
    assert cfg.catalog_options["switch"] == {"type": "type_b"}


def test_indirizzo_non_valido(tmp_path):
    cfg = _cfg(tmp_path)
    for bad in ("", "ftp://x.test/", "non-un-indirizzo"):
        try:
            add_store(cfg, bad, "switch")
        except ValueError:
            continue
        raise AssertionError(f"doveva rifiutare {bad!r}")


def test_console_sconosciuta(tmp_path):
    cfg = _cfg(tmp_path)
    try:
        add_store(cfg, "https://x.test/", "console-inventata")
    except ValueError:
        return
    raise AssertionError("doveva rifiutare una console sconosciuta")
