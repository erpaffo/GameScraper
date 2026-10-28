"""Test delle difese del server desktop e della scrittura delle impostazioni.

Copre: richieste da altri siti (CSRF, WebSocket) e DNS rebinding bloccati da LocalGuard, icone dei siti
servite in sandbox (un SVG con script non deve girare nell'app), settings.json mai svuotato da una
lettura fallita, scritture concorrenti senza perdite.

Esegui: python tests/test_security.py
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, WebSocket  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from pozzo import config  # noqa: E402
from pozzo.app.server import LocalGuard, create_app  # noqa: E402


def test_local_guard():
    inner = FastAPI()

    @inner.get("/api/x")
    def get_x():
        return {"ok": True}

    @inner.post("/api/x")
    def post_x():
        return {"ok": True}

    @inner.websocket("/ws")
    async def ws(w: WebSocket):
        await w.accept()
        await w.send_text("ciao")
        await w.close()

    c = TestClient(LocalGuard(inner, 8765), base_url="http://127.0.0.1:8765")
    assert c.get("/api/x").status_code == 200
    assert c.post("/api/x").status_code == 200  # senza Origin (pozzo-cli, curl)
    assert c.post("/api/x", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200
    assert c.post("/api/x", headers={"Origin": "http://localhost:8765"}).status_code == 200
    assert c.post("/api/x", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/x", headers={"Origin": "null"}).status_code == 403  # iframe sandbox / file://
    # DNS rebinding: il browser manda l'Host del dominio dell'attaccante
    assert c.get("/api/x", headers={"Host": "evil.example:8765"}).status_code == 403
    with c.websocket_connect("/ws", headers={"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}) as w:
        assert w.receive_text() == "ciao"
    try:
        with c.websocket_connect("/ws", headers={"Host": "127.0.0.1:8765", "Origin": "https://evil.example"}) as w:
            w.receive_text()
        raise AssertionError("WebSocket da un altro sito accettato")
    except WebSocketDisconnect as e:
        assert e.code == 4403
    print("ok  LocalGuard: altri siti e DNS rebinding respinti, l'app e la CLI passano")


def test_icon_sandbox():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        cfg = config.Config(cache=tmp, output=tmp / "out")
        with TestClient(create_app(cfg)) as c:
            r = c.get("/icons/platform/windows")
            assert r.status_code == 200 and "svg" in r.headers["content-type"]
            assert r.headers.get("content-security-policy") == "sandbox", r.headers
    print("ok  icone servite in sandbox: uno script in un SVG scaricato non gira")


def test_settings_never_wiped():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        cfg = config.Config(cache=tmp)
        cfg.config_path = tmp / "config.toml"
        p = config.settings_path(cfg)
        config.save_settings(cfg, emulators={"retroarch": "C:/RA/retroarch.exe"})

        # file bloccato (OneDrive, antivirus): il salvataggio fallisce invece di riscrivere da zero
        before = p.read_text(encoding="utf-8")
        real = Path.read_text

        def locked(self, *a, **kw):
            if self == p:
                raise PermissionError("bloccato")
            return real(self, *a, **kw)
        with mock.patch.object(Path, "read_text", locked):
            try:
                config.save_settings(cfg, app={"theme": "x"})
                raise AssertionError("salvataggio riuscito su un file illeggibile")
            except PermissionError:
                pass
        assert p.read_text(encoding="utf-8") == before

        # JSON rotto: copia tenuta da parte, poi si riparte
        p.write_text("{rotto", encoding="utf-8")
        config.save_settings(cfg, app={"theme": "x"})
        assert p.with_suffix(".rotto.json").read_text(encoding="utf-8") == "{rotto"
        assert json.loads(p.read_text(encoding="utf-8")) == {"app": {"theme": "x"}}

        # scritture da piu' thread insieme: nessuna voce persa
        ts = [threading.Thread(target=config.save_settings, args=(cfg,), kwargs={"plugins": {f"p{i}": {"v": i}}})
              for i in range(20)]
        for th in ts:
            th.start()
        for th in ts:
            th.join()
        assert len(json.loads(p.read_text(encoding="utf-8"))["plugins"]) == 20
    print("ok  settings.json: mai svuotato da una lettura fallita, nessuna perdita con piu' thread")


if __name__ == "__main__":
    test_local_guard()
    test_icon_sandbox()
    test_settings_never_wiped()
    print("TUTTI I TEST OK")
