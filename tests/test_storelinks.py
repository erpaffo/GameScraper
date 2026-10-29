"""Link di download degli store online: lettura di gioco/aggiornamento/DLC con provider e parti,
risoluzione degli accorciatori pubblicitari e API che apre i link scelti. Nessuna rete.

Esegui: PYTHONUTF8=1 python tests/test_storelinks.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from pozzo import config  # noqa: E402
from pozzo import storelinks as SL  # noqa: E402
from pozzo.app.server import create_app  # noqa: E402
from pozzo.cache import Cache  # noqa: E402
from pozzo.library.build import build  # noqa: E402

URL_NX = "https://example-store.test/gioco-switch-nsp-update-dlc/"
URL_ZP = "https://example-store2.test/gioco-switch-nsp/"

# come una pagina nxbrew reale: intestazione per file e un paragrafo per provider
HTML_NX = """<html><body><article><div class="entry">
  <p><strong>Download Links</strong></p>
  <p><strong>Base Game NSP (996 MB)</strong></p>
  <p><strong>DataNodes</strong><br><a href="https://ouo.io/aaa">Download</a></p>
  <p><strong>1Fichier</strong><br><a href="https://ouo.io/bbb">Download</a></p>
  <p><strong>Base Game NSP Part 2 (1 GB)</strong></p>
  <p><strong>DataNodes</strong><br><a href="https://datanodes.to/part2">Download</a></p>
  <p><strong>Update 1.0.7 (v458752)</strong></p>
  <p><strong>DataNodes</strong><br><a href="https://ouo.io/ccc">Download</a></p>
  <p><strong>DLC Pack (3 DLCs)</strong></p>
  <p><strong>DataNodes</strong><br><a href="https://ouo.io/ddd">Download</a></p>
  <p>Please report any broken/dead/missing links in the comment section.</p>
</div></article></body></html>"""

# come una pagina ziperto reale: intestazione e provider nella stessa riga
HTML_ZP = """<html><body><div class="entry-content">
  <p><span><strong>Download <span>(Base Game)</span> Links: <span>NSP</span> (168MB)</strong></span><br>
     <strong><a href="https://ouo.io/eee">MegaUp</a> | <a href="https://datanodes.to/base">Datanodes</a></strong></p>
  <p><span><strong>Download (EUR) Links: NSP</strong></span><br>
     <strong><a href="https://ouo.io/fff">MegaUp</a></strong></p>
</div></body></html>"""


def test_parse_nxbrew():
    d = SL.parse_page(HTML_NX, URL_NX)
    links = d["links"]
    assert [x["kind"] for x in links] == ["game", "game", "game", "update", "dlc"], links
    assert [x["provider"] for x in links] == ["DataNodes", "1Fichier", "DataNodes", "DataNodes", "DataNodes"], links
    assert links[2]["part"] == 2 and links[0]["part"] == 0, links
    assert links[0]["short"] and links[0]["host"] == "ouo.io", links[0]
    assert not links[2]["short"] and links[2]["host"] == "datanodes.to", links[2]
    assert all(x["label"] for x in links)
    print("ok  nxbrew: gioco (anche a parti), aggiornamento e DLC con provider e accorciatori riconosciuti")


def test_parse_ziperto():
    d = SL.parse_page(HTML_ZP, URL_ZP)
    links = d["links"]
    assert [(x["kind"], x["provider"]) for x in links] == [
        ("game", "MegaUp"), ("game", "Datanodes"), ("game", "MegaUp")], links
    assert "———" not in links[0]["label"] and links[0]["label"].startswith("Download"), links[0]["label"]
    assert links[0]["url"] == "https://ouo.io/eee" and links[1]["url"] == "https://datanodes.to/base"
    print("ok  ziperto: provider nella stessa riga, versioni in altre lingue come righe distinte")


def test_risoluzione(tmp_path):
    """Un accorciatore viene risolto una volta e resta in cache; senza un browser a disposizione resta com'e'."""
    cfg = config.load(_make_cfg(tmp_path))
    c = Cache(cfg.cache)
    try:
        seen = []

        def fake_browser(url):
            seen.append(url)
            return "https://datanodes.to/vera" if url.endswith("aaa") else None

        SL.set_resolver(fake_browser)
        real_get = SL.httpx.get
        SL.httpx.get = lambda *a, **k: (_ for _ in ()).throw(SL.httpx.HTTPError("bloccato"))
        try:
            assert SL.resolve(c, "https://ouo.io/aaa") == "https://datanodes.to/vera"
            assert SL.resolve(c, "https://ouo.io/aaa") == "https://datanodes.to/vera" and len(seen) == 1, "risultato in cache"
            assert SL.resolve(c, "https://ouo.io/bbb") == "https://ouo.io/bbb", "senza link vero resta l'accorciatore"
            assert SL.resolve(c, "https://datanodes.to/x") == "https://datanodes.to/x", "i link diretti non si toccano"
        finally:
            SL.httpx.get = real_get
    finally:
        SL.set_resolver(None)
        c.close()
    print("ok  accorciatori: link vero tramite il browser, risultato in cache, fallback al link originale")


def test_api(tmp_path):
    """La scheda del gioco riceve tutti i link; «Scarica» ne apre uno per tipo, già risolti."""
    cfg = config.load(_make_cfg(tmp_path))
    page = "https://example-store.test/elenco"
    cfg.catalogs = {"switch": [page]}
    c = Cache(cfg.cache)
    c.kv_set(f"catalog:type_b:{page}", {"url": page, "index_url": page, "pid": "switch", "time": time.time(),
                                        "entries": [{"name": "Gioco Uno Switch NSP", "size": None,
                                                     "url": URL_NX, "magnet": None, "group": "G"}]})
    c.close()
    build(cfg, log=lambda m: None)
    opened = []
    real_fetch = SL.fetch
    SL.fetch = lambda url, timeout=20.0: SL.parse_page(HTML_NX, url)
    from pozzo.library import launcher as L
    old_open = L.open_uri
    L.open_uri = lambda u: opened.append(u)
    SL.set_resolver(lambda url: "https://datanodes.to/" + url.rsplit("/", 1)[-1])
    try:
        app = create_app(cfg)
        with TestClient(app) as cl:
            gid = cl.get("/api/library").json()["games"]["rows"][0][0]
            d = cl.get(f"/api/game/{gid}/links").json()
            assert len(d["links"]) == 5 and d["kind_order"][:3] == ["game", "update", "dlc"], d
            assert {x["kind"] for x in d["links"]} == {"game", "update", "dlc"}, d
            urls = [next(x["url"] for x in d["links"] if x["kind"] == k) for k in ("game", "update", "dlc")]
            r = cl.post(f"/api/game/{gid}/download", json={"links": urls})
            assert r.status_code == 200, r.text
            assert len(r.json()["opened"]) == 3 and all(x["shortened"] for x in r.json()["opened"]), r.json()
            assert all(u.startswith("https://datanodes.to/") for u in opened), opened
    finally:
        SL.fetch = real_fetch
        L.open_uri = old_open
        SL.set_resolver(None)
    print("ok  API: elenco completo nella scheda e «Scarica» apre un link per tipo, saltando la pubblicità")


def _make_cfg(tmp: Path) -> Path:
    (tmp / "roms").mkdir(exist_ok=True)
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                     encoding="utf-8")
    return tmp / "config.toml"


if __name__ == "__main__":
    test_parse_nxbrew()
    test_parse_ziperto()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_risoluzione(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_api(Path(t))
    print("TUTTI I TEST OK")
