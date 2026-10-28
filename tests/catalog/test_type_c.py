"""Test del parser "type_c" (archivio paginato tipo Ziperto). Nessuna rete.

Esegui: PYTHONUTF8=1 python tests/catalog/test_type_c.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pozzo import catalog  # noqa: E402
from pozzo.cache import Cache  # noqa: E402

URL = "https://www.example-z.test/nintendo-switch-nsp/"
PAGE2 = URL + "page/2/"

HTML_INDEX = """<html><head><link rel="next" href="https://www.example-z.test/nintendo-switch-nsp/page/2/"></head>
<body>
  <article><h2 class="post-title"><a href="https://www.example-z.test/beat-them-all-switch/">Beat Them All Switch NSP (eShop)</a></h2></article>
  <article><h2 class="post-title"><a href="/beach-volleyball-challenge-switch/">Beach Volleyball Challenge Switch NSP (eShop)</a></h2></article>
  <div class="nav-links">
    <a class="page-numbers" href="https://www.example-z.test/nintendo-switch-nsp/page/2/">2</a>
    <a class="page-numbers" href="https://www.example-z.test/nintendo-switch-nsp/page/3/">3</a>
    <a class="nextpostslink" rel="next" aria-label="Next Page"
       href="https://www.example-z.test/nintendo-switch-nsp/page/2/">Next</a>
  </div>
</body></html>"""

HTML_PAGE2 = """<html><body>
  <article><h2 class="post-title"><a href="https://www.example-z.test/zelda-switch/">Zelda Switch NSP (eShop)</a></h2></article>
  <article><h2 class="post-title"><a href="https://www.example-z.test/beat-them-all-switch/">Beat Them All Switch NSP (eShop)</a></h2></article>
</body></html>"""


def fake_page(pages):
    calls = []

    async def _get(cfg, cache, client, pid, url, log=print):
        calls.append(url)
        return pages[url]

    return _get, calls


def test_parse_type_c_index():
    items, total = catalog.parse_type_c_index(HTML_INDEX, URL)
    assert len(items) == 2, items
    assert total == 3, total  # massimo dei link /page/N/
    assert items[0] == {"name": "Beat Them All Switch NSP (eShop)", "size": None,
                        "url": "https://www.example-z.test/beat-them-all-switch/", "magnet": None}, items[0]
    assert items[1]["url"] == "https://www.example-z.test/beach-volleyball-challenge-switch/", items[1]
    print(f"ok  type_c: {len(items)} voci, {total} pagine, URL relativi risolti")


def test_page_url_and_next():
    assert catalog.page_url(URL, 2, "type_c") == PAGE2
    assert catalog.page_url(URL, 1, "type_c") == URL
    assert catalog.cache_key("type_c", URL, 2) == "catalog:type_c:" + PAGE2
    assert catalog.next_page_url(HTML_INDEX, URL) == PAGE2
    assert catalog.next_page_url(HTML_PAGE2, PAGE2) is None
    print("ok  type_c: URL /page/N/, chiavi di cache e link «next»")


def test_detect_kind():
    assert catalog.detect_kind(HTML_INDEX) == "type_c"
    assert catalog.detect_kind('<ul class="lcp_catlist"><li><a href="/x/">X</a></li></ul>') == "type_a"
    assert catalog.detect_kind('<pre><a href="gioco.zip">gioco.zip</a> 1 MB</pre>') is None
    print("ok  rilevamento: archivio paginato riconosciuto")


def test_fetch_type_c(tmp_path):
    cache = Cache(tmp_path)
    cfg = SimpleNamespace(catalog_options={}, limits={})  # tipo non indicato: si rileva
    platform = SimpleNamespace(id="switch", name="Switch", exts=("nsp",))
    index = HTML_INDEX.replace('href="https://www.example-z.test/nintendo-switch-nsp/page/3/">3</a>', "")
    fake, calls = fake_page({URL: index, PAGE2: HTML_PAGE2})
    old = catalog._get_page
    catalog._get_page = fake
    try:
        entries = asyncio.run(catalog.fetch(cfg, cache, None, platform, URL))
    finally:
        catalog._get_page = old
    assert calls == [URL, PAGE2], calls
    # la voce ripetuta in pagina 2 non si duplica
    assert [e["name"] for e in entries] == ["Beat Them All Switch NSP (eShop)",
                                            "Beach Volleyball Challenge Switch NSP (eShop)", "Zelda Switch NSP (eShop)"], entries
    assert cache.kv_get("catalog:type_c:" + URL)["page"] == 1
    assert cache.kv_get("catalog:type_c:" + PAGE2)["page"] == 2
    info = catalog.cached_info(cache, "switch", URL)
    assert info["type"] == "type_c" and info["total_pages"] == 2 and len(info["entries"]) == 3, info
    cache.close()
    print(f"ok  type_c: {len(entries)} voci in {len(calls)} pagine, cache per pagina, niente duplicati")


if __name__ == "__main__":
    test_parse_type_c_index()
    test_page_url_and_next()
    test_detect_kind()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_fetch_type_c(Path(t))
    print("TUTTI I TEST OK")
