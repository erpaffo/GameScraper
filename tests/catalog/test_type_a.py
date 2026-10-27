"""Test del parser "type_a" (elenco paginato) e del relativo scaricamento. Nessuna rete.

Esegui: PYTHONUTF8=1 python tests/catalog/test_type_a.py
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

# HTML fittizio: stessa struttura della pagina indice (lcp_catlist + lcp_paginator)
HTML_INDEX = """
<div id="content">
  <article>
    <h1 class="entry-title">All Games, A-Z</h1>
    <div class="entry-content">
      <ul class="lcp_catlist" id="lcp_instance_0">
        <li><a href="https://example-a.test/game-one/">Game One – Build 123</a></li>
        <li><a href="https://example-a.test/game-two/">Game Two – v1.4</a></li>
      </ul>
      <ul class="lcp_paginator">
        <li class="lcp_currentpage">1</li>
        <li><a href="https://example-a.test/all/?lcp_page0=2#lcp_instance_0" title="2">2</a></li>
        <li><a href="https://example-a.test/all/?lcp_page0=147#lcp_instance_0" title="147">147</a></li>
      </ul>
    </div>
  </article>
</div>
"""

# L'HTML di esempio ha 2 voci: la terza arriva dalla "pagina 2" qui sotto
HTML_PAGE1 = HTML_INDEX
HTML_PAGE2 = """
<div id="content"><ul class="lcp_catlist" id="lcp_instance_0">
  <li><a href="https://example-a.test/game-three/">Game Three</a></li>
  <li><a href="https://example-a.test/game-one/">Game One – Build 123</a></li>
</ul></div>
"""

URL = "https://example-a.test/all/"


def page_fixture(page_html):
    """Fixture di rete: restituisce la funzione che rimpiazza _get_page (nessuna richiesta reale)."""
    calls = []

    async def fake_get_page(cfg, cache, client, pid, url, log=print):
        calls.append(url)
        return page_html(url)

    return fake_get_page, calls


def test_parse_type_a_index():
    items, total_pages = catalog.parse_type_a_index(HTML_INDEX, URL, {"nds", "zip"})
    assert len(items) == 2, items
    assert total_pages == 147, total_pages
    assert items[0] == {"name": "Game One – Build 123", "size": None, "url": "https://example-a.test/game-one/",
                        "magnet": None}, items[0]
    assert items[1]["url"] == "https://example-a.test/game-two/"
    print(f"ok  type_a: {len(items)} voci, {total_pages} pagine")


def test_parse_type_a_index_three_items():
    """Con una terza voce in pagina (es. markup con 3 <li>) il conteggio segue l'HTML."""
    html = HTML_INDEX.replace(
        '<li><a href="https://example-a.test/game-two/">Game Two – v1.4</a></li>',
        '<li><a href="https://example-a.test/game-two/">Game Two – v1.4</a></li>\n'
        '        <li><a href="https://example-a.test/game-three/">Game Three</a></li>')
    items, total_pages = catalog.parse_type_a_index(html, URL, set())
    assert len(items) == 3, items
    assert total_pages == 147, total_pages
    assert [i["name"] for i in items] == ["Game One – Build 123", "Game Two – v1.4", "Game Three"]
    print(f"ok  type_a: {len(items)} voci, {total_pages} pagine (html con 3 voci)")


def test_parse_type_a_without_paginator():
    html = '<ul class="lcp_catlist"><li><a href="/x/">Solo Uno</a></li></ul>'
    items, total_pages = catalog.parse_type_a_index(html, URL, set())
    assert len(items) == 1 and total_pages == 1, (items, total_pages)
    assert items[0]["url"] == "https://example-a.test/x/", items[0]
    print("ok  type_a: senza paginatore -> 1 pagina")


def test_page_url():
    assert catalog.page_url(URL, 1) == URL
    assert catalog.page_url(URL, 2) == "https://example-a.test/all/?lcp_page0=2"
    assert catalog.page_url(URL + "?x=1#frag", 3) == "https://example-a.test/all/?x=1&lcp_page0=3#frag"
    assert catalog.cache_key("type_a", URL, 2) == "catalog:type_a:https://example-a.test/all/?lcp_page0=2"
    print("ok  type_a: URL e chiavi di cache delle pagine")


def test_fetch_type_a(tmp_path: Path):
    cache = Cache(tmp_path)
    cfg = SimpleNamespace(catalog_options={"nds": {"type": "type_a"}}, limits={})
    platform = SimpleNamespace(id="nds", name="NDS", exts=("nds", "zip"))
    pages = {URL: HTML_PAGE1, catalog.page_url(URL, 2): HTML_PAGE2}
    # due pagine totali: la prima dichiara il massimo, poi la seconda viene letta davvero
    pages[URL] = HTML_PAGE1.replace("lcp_page0=147", "lcp_page0=2")
    fake, calls = page_fixture(lambda u: pages[u])
    old_get, old_delay = catalog._get_page, catalog.PAGE_DELAY
    catalog._get_page, catalog.PAGE_DELAY = fake, (0.0, 0.0)
    try:
        entries = asyncio.run(catalog.fetch(cfg, cache, None, platform, URL))
    finally:
        catalog._get_page, catalog.PAGE_DELAY = old_get, old_delay

    assert calls == [URL, catalog.page_url(URL, 2)], calls
    # la voce ripetuta in pagina 2 non viene duplicata
    assert [e["name"] for e in entries] == ["Game One – Build 123", "Game Two – v1.4", "Game Three"], entries
    first = cache.kv_get("catalog:type_a:" + URL)
    second = cache.kv_get("catalog:type_a:https://example-a.test/all/?lcp_page0=2")
    assert first["pid"] == "nds" and first["total_pages"] == 2 and len(first["entries"]) == 2, first
    assert second["page"] == 2 and len(second["entries"]) == 1, second
    assert [e["name"] for e in catalog.cached_entries(cache, "nds", URL)] == [e["name"] for e in entries]
    info = catalog.cached_info(cache, "nds", URL)
    assert info["total_pages"] == 2 and info["time"] > 0, info
    cache.close()
    print(f"ok  type_a: 2 pagine scaricate, {len(entries)} voci in cache con le chiavi per pagina")


def _index_html(names, total):
    """Pagina indice minimale: voci + paginatore con il totale indicato."""
    lis = "".join(f'<li><a href="/{n.replace(" ", "-")}/">{n}</a></li>' for n in names)
    pag = (f'<li><a href="{URL}?lcp_page0={total}#lcp_instance_0" title="{total}">{total}</a></li>'
           if total > 1 else "")
    return f'<ul class="lcp_catlist">{lis}</ul><ul class="lcp_paginator">{pag}</ul>'


def test_fetch_type_a_shrink(tmp_path: Path):
    """Se l'elenco si accorcia (3 -> 1 pagina) le pagine vecchie non restano in cache."""
    cache = Cache(tmp_path)
    cfg = SimpleNamespace(catalog_options={"nds": {"type": "type_a"}}, limits={})
    platform = SimpleNamespace(id="nds", name="NDS", exts=("nds",))
    state = {"total": 3}

    def page_for(u):
        tail = u.rsplit("lcp_page0=", 1)[-1] if "lcp_page0=" in u else "1"
        return _index_html([f"Gioco {tail}"], state["total"])

    fake, calls = page_fixture(page_for)
    old_get, old_delay = catalog._get_page, catalog.PAGE_DELAY
    catalog._get_page, catalog.PAGE_DELAY = fake, (0.0, 0.0)
    try:
        first = asyncio.run(catalog.fetch(cfg, cache, None, platform, URL))
        assert len(first) == 3 and len(calls) == 3, (first, calls)
        state["total"] = 1
        calls.clear()
        second = asyncio.run(catalog.fetch(cfg, cache, None, platform, URL))
    finally:
        catalog._get_page, catalog.PAGE_DELAY = old_get, old_delay
    keys = [k for (k,) in cache.db.execute("SELECT k FROM kv WHERE k LIKE 'catalog:type_a:%'").fetchall()]
    assert [e["name"] for e in second] == ["Gioco 1"], second
    assert keys == ["catalog:type_a:" + URL], keys
    assert [e["name"] for e in catalog.cached_entries(cache, "nds", URL)] == ["Gioco 1"]
    cache.close()
    print("ok  type_a: elenco accorciato, pagine vecchie rimosse dalla cache")


if __name__ == "__main__":
    test_parse_type_a_index()
    test_parse_type_a_index_three_items()
    test_parse_type_a_without_paginator()
    test_page_url()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_fetch_type_a(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_fetch_type_a_shrink(Path(t))
    print("TUTTI I TEST OK")

