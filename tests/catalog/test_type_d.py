"""Test del parser "type_d" (elenco A-Z completo su una pagina, es. ziperto XCI list). Nessuna rete.

Esegui: PYTHONUTF8=1 python tests/catalog/test_type_d.py
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

URL = "https://www.example-z.test/nintendo-switch-xci-list/"

HTML = """<html><body><article class="post">
<div class="entry">
  <div class="az-listing">
    <div class="az-letters-wrap"><div class="az-letters">
      <a href="#letter-A">A</a><a href="#letter-B">B</a></div></div>
    <div class="items-outer"><div class="items-inner">
      <div class="letter-section">
        <h2 class="letter-title">A</h2>
        <ul class="az-columns max-10-columns">
          <li><a href="https://www.example-z.test/abbies-farm-switch/">Abbie's Farm for Kids SWITCH NSP/XCI (eShop)</a></li>
          <li><a href="https://www.example-z.test/aca-neogeo-switch/">ACA NEOGEO FATAL FURY SWITCH NSP/XCI [Update] (eShop)</a></li>
          <li><a href="#">x</a></li>
        </ul>
        <div class="back-to-top"><a href="#top">Back to top</a></div>
      </div>
      <div class="letter-section">
        <h2 class="letter-title">B</h2>
        <ul class="az-columns max-5-columns">
          <li><a href="https://www.example-z.test/bang-bang-bandits-switch/">Bang! Bang! Bandits SWITCH NSP/XCI (eShop)</a></li>
        </ul>
        <div class="back-to-top"><a href="#top">Back to top</a></div>
      </div>
    </div></div>
  </div>
</div></article></body></html>"""


def test_parse_type_d():
    items, total = catalog.parse_type_d_index(HTML, URL)
    assert total == 1, total
    assert [i["name"] for i in items] == ["Abbie's Farm for Kids SWITCH NSP/XCI (eShop)",
                                          "ACA NEOGEO FATAL FURY SWITCH NSP/XCI [Update] (eShop)",
                                          "Bang! Bang! Bandits SWITCH NSP/XCI (eShop)"], items
    assert [i["group"] for i in items] == ["A", "A", "B"], [i["group"] for i in items]
    assert items[0]["url"] == "https://www.example-z.test/abbies-farm-switch/", items[0]
    print(f"ok  type_d: {len(items)} voci con lettera, «Back to top» e ancore scartati")


def test_detect_and_registry():
    assert catalog.detect_kind(HTML) == "type_d"
    assert "type_d" in catalog.PARSERS
    # una pagina con pochi link non viene scambiata per un elenco
    assert catalog.detect_kind('<article><ul><li><a href="/x/">Solo uno</a></li></ul></article>') is None
    print("ok  rilevamento: elenco A-Z su una pagina riconosciuto")


def test_fetch_type_d(tmp_path: Path):
    cache = Cache(tmp_path)
    cfg = SimpleNamespace(catalog_options={}, limits={})
    platform = SimpleNamespace(id="switch", name="Switch", exts=("xci", "nsp"))
    calls = []

    async def fake(c, ch, cl, pid, url, log=print):
        calls.append(url)
        return HTML

    old = catalog._get_page
    catalog._get_page = fake
    try:
        entries = asyncio.run(catalog.fetch(cfg, cache, None, platform, URL))
    finally:
        catalog._get_page = old
    assert calls == [URL], calls  # una sola pagina, nessuna paginazione
    assert len(entries) == 3, entries
    assert cache.kv_get("catalog:type_d:" + URL)["entries"] == entries
    info = catalog.cached_info(cache, "switch", URL)
    assert info["type"] == "type_d" and len(info["entries"]) == 3, info
    cache.close()
    print(f"ok  type_d: {len(entries)} voci salvate con una sola richiesta")


if __name__ == "__main__":
    test_parse_type_d()
    test_detect_and_registry()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_fetch_type_d(Path(t))
    print("TUTTI I TEST OK")
