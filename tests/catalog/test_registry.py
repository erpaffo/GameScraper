"""Test del registro dei parser: "filelist" resta il default e si comporta come prima. Nessuna rete.

Esegui: PYTHONUTF8=1 python tests/catalog/test_registry.py
"""

import asyncio
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pozzo import catalog  # noqa: E402
from pozzo.cache import Cache  # noqa: E402

# Indice Apache classico, il formato storico di "filelist"
HTML_APACHE = """<html><body><h1>Index of /nds</h1><pre>
<a href="../">../</a>
<a href="Gioco%20Uno.zip">Gioco Uno.zip</a>            360.25 KB
<a href="Gioco%20Due.7z">Gioco Due.7z</a>             1.2 MiB
</pre></body></html>
"""

URL = "https://example-c.test/nds/"


def fake_get_page(html):
    calls = []

    async def _get(cfg, cache, client, pid, url, log=print):
        calls.append(url)
        return html

    return _get, calls


def test_registry():
    assert set(catalog.PARSERS) == {"filelist", "type_a", "type_b", "type_c", "type_d"}, catalog.PARSERS
    print("ok  registro: filelist, type_a, type_b, type_c, type_d")


def test_default_type_is_filelist():
    assert catalog.parser_type(SimpleNamespace(catalog_options={}), "nds") == "filelist"
    assert catalog.parser_type(SimpleNamespace(catalog_options={"nds": {"type": "TYPE_B"}}), "nds") == "type_b"
    assert catalog.parser_type(SimpleNamespace(catalog_options={"nds": {"type": "type_a"}}), "nds", {"type": "filelist"}) == "filelist"
    assert catalog.parser_type(None, "nds") == "filelist"
    print("ok  registro: default filelist e scelta del tipo")


def test_parse_listing_unchanged():
    entries = catalog.parse_listing(HTML_APACHE, URL, {"nds", "zip"})
    assert len(entries) == 2, entries
    assert entries[0] == {"name": "Gioco Uno.zip", "size": 368896, "url": "https://example-c.test/nds/Gioco%20Uno.zip",
                          "magnet": None}, entries[0]
    assert entries[1]["name"] == "Gioco Due.7z" and entries[1]["size"] == 1258291, entries[1]
    print("ok  filelist: parse_listing invariato")


def test_fetch_filelist_key(tmp_path: Path):
    cache = Cache(tmp_path)
    cfg = SimpleNamespace(catalog_options={}, limits={})
    platform = SimpleNamespace(id="nds", name="NDS", exts=("nds", "zip"))
    fake, calls = fake_get_page(HTML_APACHE)
    old_get = catalog._get_page
    catalog._get_page = fake
    try:
        entries = asyncio.run(catalog.fetch(cfg, cache, None, platform, URL))
    finally:
        catalog._get_page = old_get
    key = f"catalog:nds:{hashlib.sha1(URL.encode()).hexdigest()[:12]}"
    assert calls == [URL] and len(entries) == 2, (calls, entries)
    assert cache.kv_get(key)["entries"] == entries, "chiave di cache storica invariata"
    assert [e["name"] for e in catalog.cached_entries(cache, "nds", URL)] == ["Gioco Uno.zip", "Gioco Due.7z"]
    cache.close()
    print("ok  filelist: scaricamento e chiave di cache invariati")


def test_switch_type_prunes_cache(tmp_path: Path):
    """Cambiando "type" le voci del tipo precedente non devono restare nella libreria."""
    cache = Cache(tmp_path)
    platform = SimpleNamespace(id="nds", name="NDS", exts=("nds", "zip"))
    index_a = ('<ul class="lcp_catlist"><li><a href="https://example-c.test/a/">Gioco A</a></li></ul>'
               '<ul class="lcp_paginator"></ul>')
    index_b = '<div class="PLACEHOLDER_CSS_B-az-list"><div class="PLACEHOLDER_CSS_B-game"><a href="/b/">Gioco B</a></div></div>'

    def fetch_with(kind, html):
        fake, _ = fake_get_page(html)
        old_get = catalog._get_page
        catalog._get_page = fake
        try:
            return asyncio.run(catalog.fetch(SimpleNamespace(catalog_options={"nds": {"type": kind}}, limits={}),
                                             cache, None, platform, URL))
        finally:
            catalog._get_page = old_get

    fetch_with("type_a", index_a)
    assert [e["name"] for e in catalog.cached_entries(cache, "nds", URL)] == ["Gioco A"]
    fetch_with("type_b", index_b)
    assert [e["name"] for e in catalog.cached_entries(cache, "nds", URL)] == ["Gioco B"]
    assert not [k for (k,) in cache.db.execute("SELECT k FROM kv WHERE k LIKE 'catalog:type_a:%'")]
    fetch_with("filelist", HTML_APACHE)
    assert [e["name"] for e in catalog.cached_entries(cache, "nds", URL)] == ["Gioco Uno.zip", "Gioco Due.7z"]
    assert not [k for (k,) in cache.db.execute("SELECT k FROM kv WHERE k LIKE 'catalog:type_%'")]
    cache.close()
    print("ok  cambio tipo: la cache del tipo precedente viene rimossa")


def test_unknown_type(tmp_path: Path):
    cache = Cache(tmp_path)
    cfg = SimpleNamespace(catalog_options={"nds": {"type": "type_zzz"}}, limits={})
    platform = SimpleNamespace(id="nds", name="NDS", exts=("nds",))
    try:
        asyncio.run(catalog.fetch(cfg, cache, None, platform, URL))
    except ValueError as e:
        assert "type_zzz" in str(e), e
    else:
        raise AssertionError("tipo sconosciuto accettato")
    cache.close()
    print("ok  registro: tipo sconosciuto rifiutato")


if __name__ == "__main__":
    test_registry()
    test_default_type_is_filelist()
    test_parse_listing_unchanged()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_fetch_filelist_key(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_switch_type_prunes_cache(Path(t))
        test_unknown_type(Path(t))
    print("TUTTI I TEST OK")

