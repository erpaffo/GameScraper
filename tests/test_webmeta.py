"""Giochi browser: validazione URL (solo http/https, niente host locali/IP privati), parser dei metadati,
limiti di fetch/redirect, salvataggio e avvio come URI.

Esegui: python tests/test_webmeta.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pozzo import webmeta as W  # noqa: E402
from pozzo.library.userdata import UserData  # noqa: E402
from pozzo.stores.manual import Manual  # noqa: E402

HTML = """<html><head><title> Fallback  titolo </title>
<meta property="og:title" content="Chess - Play Online">
<meta property="og:description" content="Gioca  a scacchi">
<meta property="og:image" content="/img/cover.png"></head><body>ignorato</body></html>"""


def refused(url):
    try:
        W.validate_url(url, resolve=False)
    except W.WebMetaError:
        return True
    return False


def test_validate():
    assert W.validate_url("chess.com", resolve=False) == "https://chess.com"
    assert W.validate_url("http://www.chess.com/play?x=1#a", resolve=False) == "http://www.chess.com/play?x=1#a"
    for bad in ["", "ftp://a.com", "file:///etc/passwd", "javascript:alert(1)", "http://localhost/", "http://127.0.0.1/",
                "http://10.0.0.5/", "http://192.168.1.1:8080/", "http://169.254.169.254/", "http://[::1]/",
                "http://nas/", "http://stampante.local/", "http://u:p@chess.com/", "http://a.com/ x", "https://", "http://0.0.0.0/"]:
        assert refused(bad), bad
    with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.1.2.3", 0))]):  # DNS verso rete privata
        try:
            W.validate_url("https://evil.example.com")
            raise AssertionError("host privato accettato")
        except W.WebMetaError:
            pass
    print("ok  validazione URL")


def test_parse():
    m = W.parse_meta(HTML, "https://www.chess.com/home")
    assert m == {"title": "Chess - Play Online", "description": "Gioca a scacchi",
                 "image": "https://www.chess.com/img/cover.png"}, m
    m = W.parse_meta("<title>Solo titolo</title><meta name='description' content='d'>", "https://a.com")
    assert m == {"title": "Solo titolo", "description": "d", "image": ""}
    assert W.parse_meta('<meta property="og:image" content="http://127.0.0.1/x.png">', "https://a.com")["image"] == ""
    assert W.parse_meta('<meta property="og:image" content="javascript:x">', "https://a.com")["image"] == ""
    assert W.parse_meta("", "https://a.com") == {"title": "", "description": "", "image": ""}
    print("ok  parser metadati")


def fetch_with(handler, url="https://example.com/"):
    real = httpx.Client
    with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]), \
            mock.patch("pozzo.webmeta.safe_client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)):
        return W.fetch_meta(url)


def err(handler, url="https://example.com/"):
    try:
        fetch_with(handler, url)
    except W.WebMetaError as e:
        return str(e)
    raise AssertionError("errore atteso")


def test_fetch():
    ok = lambda r: httpx.Response(200, html=HTML)  # noqa: E731
    assert fetch_with(ok)["title"] == "Chess - Play Online"
    assert "404" in err(lambda r: httpx.Response(404))
    assert "HTML" in err(lambda r: httpx.Response(200, json={"a": 1}))
    loop = lambda r: httpx.Response(302, headers={"location": "/b"})  # noqa: E731
    assert "redirect" in err(loop)
    to_local = lambda r: httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})  # noqa: E731
    assert "locale" in err(to_local) or "privato" in err(to_local)

    def boom(r):
        raise httpx.ConnectTimeout("t")
    assert "Tempo scaduto" in err(boom)
    big = lambda r: httpx.Response(200, html="<meta property='og:title' content='T'>" + "x" * 3_000_000)  # noqa: E731
    assert fetch_with(big)["title"] == "T"
    print("ok  fetch: errori, redirect, limiti")


def test_pinned_backend():
    """Connessione solo all'IP verificato: rebinding, host privati e redirect dell'immagine sono rifiutati."""
    import httpcore
    pub, priv = [(2, 1, 6, "", ("93.184.216.34", 0))], [(2, 1, 6, "", ("10.0.0.7", 0))]
    seen = []
    with mock.patch.object(httpcore.SyncBackend, "connect_tcp", lambda self, host, *a, **k: seen.append(host) or "S"), \
            mock.patch("socket.getaddrinfo", return_value=pub):
        assert W._PinnedSync().connect_tcp("example.com", 443) == "S" and seen == ["93.184.216.34"]  # IP verificato
    for infos in (priv, pub + priv):  # anche un solo indirizzo privato tra quelli risolti blocca
        with mock.patch("socket.getaddrinfo", return_value=infos):
            try:
                W._PinnedSync().connect_tcp("rebind.example.com", 80)
                raise AssertionError("IP privato accettato")
            except httpcore.ConnectError:
                pass
    # client reale: IP locale diretto -> rifiutato prima di aprire il socket (sync, async e immagine via download_media)
    with W.safe_client() as c:
        try:
            c.get("http://127.0.0.1:9/x.png")
            raise AssertionError("connessione locale permessa")
        except httpx.ConnectError as e:
            assert "non ammesso" in str(e)

    async def cover():
        from pozzo.net import MIN_SIZE, download_media
        assert MIN_SIZE >= 0
        async with W.safe_async_client(follow_redirects=True) as c:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                return await download_media(c, "http://127.0.0.1:9/cover.png", Path(d) / "x")
    assert asyncio.run(cover()) is None  # copertina su host locale: scartata, niente eccezioni
    # redirect verso host privato: rifiutato anche quando la prima tappa e' pubblica
    to_priv = lambda r: httpx.Response(302, headers={"location": "http://10.0.0.7/a"})  # noqa: E731
    assert "privato" in err(to_priv) or "locale" in err(to_priv)
    print("ok  anti-rebinding: connessione all'IP verificato, copertina e redirect privati rifiutati")


def test_store(tmp: Path):
    ud = UserData(tmp / "userdata.db")
    meta = W.parse_meta(HTML, "https://www.chess.com/")
    a = ud.add_manual("Chess", "https://www.chess.com/", [], "", "browser", meta)
    assert ud.add_manual("bis", "https://WWW.chess.com/", [], "", "browser") == a  # stesso URL: nessun doppione
    ud.add_manual("Tool", "C:/g/tool.exe", [], kind="app")  # flusso .exe invariato
    rows = {m["title"]: m for m in ud.manual_games()}
    assert rows["Chess"]["meta"] == meta and rows["Tool"]["meta"] == {} and rows["Tool"]["kind"] == "app"
    cfg = mock.Mock(cache=str(tmp))
    games = asyncio.run(Manual(cfg, None, None, print).fetch())
    g = next(x for x in games if x.title == "Chess")
    assert g.launch == {"type": "uri", "target": "https://www.chess.com/"} and g.installed
    assert g.description == "Gioca a scacchi" and g.images == {"cover": "https://www.chess.com/img/cover.png"}
    assert next(x for x in games if x.title == "Tool").launch["type"] == "exe"
    print("ok  salvataggio e libreria: browser + exe")


if __name__ == "__main__":
    test_validate()
    test_parse()
    test_fetch()
    test_pinned_backend()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        test_store(Path(d))
    print("test_webmeta: OK")
