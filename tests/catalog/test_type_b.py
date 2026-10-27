"""Test del parser "type_b" (elenco A-Z senza paginazione) e del relativo scaricamento. Nessuna rete.

Esegui: PYTHONUTF8=1 python tests/catalog/test_type_b.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gamescraper import catalog  # noqa: E402
from gamescraper.cache import Cache  # noqa: E402

# HTML fittizio: contenitore unico, separatori <h2 id="letter-X">, un div per gioco
HTML_INDEX = """
<div class="PLACEHOLDER_CSS_B-az-list">
  <h2 id="letter-#">#</h2>
  <div class="PLACEHOLDER_CSS_B-game"><a href="https://example-b.test/game-hash/">Game Hash</a></div>
  <h2 id="letter-A">A</h2>
  <div class="PLACEHOLDER_CSS_B-game"><a href="https://example-b.test/game-a/">Game A</a></div>
  <div class="PLACEHOLDER_CSS_B-game"><a href="https://example-b.test/game-a2/">Game A2</a></div>
  <h2 id="letter-B">B</h2>
  <div class="PLACEHOLDER_CSS_B-game"><a href="https://example-b.test/game-b/">Game B</a></div>
</div>
"""

URL = "https://example-b.test/all"


def page_fixture(page_html):
    """Fixture di rete: restituisce la funzione che rimpiazza _get_page (nessuna richiesta reale)."""
    calls = []

    async def fake_get_page(cfg, cache, client, pid, url, log=print):
        calls.append(url)
        return page_html() if callable(page_html) else page_html

    return fake_get_page, calls


def test_parse_type_b():
    items = catalog.parse_type_b(HTML_INDEX, URL, {"nds", "zip"})
    assert len(items) == 4, items
    assert [i["name"] for i in items] == ["Game Hash", "Game A", "Game A2", "Game B"], items
    assert [i["group"] for i in items] == ["#", "A", "A", "B"], [i["group"] for i in items]
    assert items[0]["url"] == "https://example-b.test/game-hash/", items[0]
    assert items[2]["url"] == "https://example-b.test/game-a2/", items[2]
    print(f"ok  type_b: {len(items)} voci, gruppi {[i['group'] for i in items]}")


def test_parse_type_b_custom_css():
    """Le classi CSS arrivano da config["css"], cosi' lo stesso parser vale per altri siti."""
    html = """
    <div class="altro-elenco">
      <h2 id="letter-C">C</h2>
      <div class="altro-gioco"><a href="/c1/">Gioco C1</a></div>
    </div>
    """
    cfg = {"css": {"az_list": "altro-elenco", "game": "altro-gioco"}}
    items = catalog.parse_type_b(html, "https://example-b.test/", set(), cfg)
    assert len(items) == 1 and items[0]["group"] == "C", items
    assert items[0]["url"] == "https://example-b.test/c1/", items[0]
    assert catalog.parse_type_b(html, "https://example-b.test/", set()) == [], "classi di default non presenti"
    print("ok  type_b: classi CSS configurabili")


def test_parse_type_b_ignores_links_outside_items():
    html = """
    <div class="PLACEHOLDER_CSS_B-az-list">
      <a href="https://example-b.test/pagina/">Link fuori da un item</a>
      <div class="PLACEHOLDER_CSS_B-game"><a href="javascript:void(0)">Vuoto</a></div>
      <div class="PLACEHOLDER_CSS_B-game"><a href="#ancora">Ancora</a></div>
      <div class="PLACEHOLDER_CSS_B-game"><a href="/ok/">Gioco Ok</a></div>
    </div>
    """
    items = catalog.parse_type_b(html, URL, set())
    assert [i["name"] for i in items] == ["Gioco Ok"], items
    print("ok  type_b: scarta link non validi e fuori dagli item")


def test_fetch_type_b(tmp_path: Path):
    cache = Cache(tmp_path)
    cfg = SimpleNamespace(catalog_options={"nds": {"type": "type_b"}}, limits={})
    platform = SimpleNamespace(id="nds", name="NDS", exts=("nds", "zip"))
    fake, calls = page_fixture(HTML_INDEX)
    old_get = catalog._get_page
    catalog._get_page = fake
    try:
        entries = asyncio.run(catalog.fetch(cfg, cache, None, platform, URL))
    finally:
        catalog._get_page = old_get

    assert calls == [URL], calls  # nessuna paginazione: una sola richiesta
    assert [e["group"] for e in entries] == ["#", "A", "A", "B"], entries
    cached = cache.kv_get("catalog:type_b:" + URL)
    assert cached["pid"] == "nds" and len(cached["entries"]) == 4, cached
    assert [e["name"] for e in catalog.cached_entries(cache, "nds", URL)] == [e["name"] for e in entries]
    cache.close()
    print(f"ok  type_b: indice unico, {len(entries)} voci in cache")


def test_parse_type_b_other_class_names():
    """Classi diverse dai default (es. nxbrew-az-list / nxbrew-game): si riconoscono per suffisso."""
    html = """
    <div class="nxbrew-az-list">
      <h2 id="letter-#">#</h2>
      <div class="nxbrew-game"><a href="https://example-b.test/hash/">Game Hash</a></div>
      <h2 id="letter-A">A</h2>
      <div class="nxbrew-game"><a href="https://example-b.test/a/">Game A</a></div>
      <div class="nxbrew-game"><a href="https://example-b.test/a2/">Game A2</a></div>
    </div>
    """
    items = catalog.parse_type_b(html, URL, set())  # nessuna configurazione css
    assert [i["name"] for i in items] == ["Game Hash", "Game A", "Game A2"], items
    assert [i["group"] for i in items] == ["#", "A", "A"], items
    print("ok  type_b: classi con nomi diversi riconosciute dal suffisso")


def test_detect_kind():
    assert catalog.detect_kind(HTML_INDEX) == "type_b"
    assert catalog.detect_kind('<ul class="lcp_catlist"><li><a href="/x/">X</a></li></ul>') == "type_a"
    assert catalog.detect_kind('<pre><a href="gioco.zip">gioco.zip</a> 1 MB</pre>') is None
    assert catalog.detect_kind("<html>niente</html>") is None
    print("ok  rilevamento: type_b, type_a, pagina di file, pagina sconosciuta")


def test_fetch_auto_detect(tmp_path: Path):
    """Senza "type" in configurazione, una pagina A-Z viene riconosciuta e letta."""
    cache = Cache(tmp_path)
    cfg = SimpleNamespace(catalog_options={}, limits={})  # nessun tipo indicato
    platform = SimpleNamespace(id="nds", name="NDS", exts=("nds", "zip"))
    fake, calls = page_fixture(HTML_INDEX)
    old_get = catalog._get_page
    catalog._get_page = fake
    try:
        entries = asyncio.run(catalog.fetch(cfg, cache, None, platform, URL))
    finally:
        catalog._get_page = old_get
    assert len(calls) == 1 and len(entries) == 4, (calls, entries)
    assert cache.kv_get("catalog:type_b:" + URL)["entries"] == entries
    print("ok  rilevamento: senza type, pagina A-Z letta come type_b con una sola richiesta")


if __name__ == "__main__":
    test_parse_type_b()
    test_parse_type_b_custom_css()
    test_parse_type_b_other_class_names()
    test_parse_type_b_ignores_links_outside_items()
    test_detect_kind()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_fetch_type_b(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_fetch_auto_detect(Path(t))
    print("TUTTI I TEST OK")

