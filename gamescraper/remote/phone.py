"""App per il telefono: libreria, avvio dei giochi sul PC, controller sullo schermo con giroscopio.

Sicurezza: l'app desktop resta solo su 127.0.0.1. Il telefono usa un secondo server HTTPS
(0.0.0.0, porta 8766 predefinita) attivato dall'utente, davanti al quale c'e' un "cancello":
  - senza abbinamento si raggiungono solo la pagina /m e l'abbinamento;
  - l'abbinamento usa un codice di 6 cifre (nel QR mostrato dal PC) valido 10 minuti e usabile una
    volta, con al massimo 10 tentativi sbagliati; il telefono riceve un token (cookie HttpOnly) e il
    PC ne conserva solo l'impronta SHA-256; i telefoni si revocano dalle impostazioni;
  - anche abbinati, si raggiungono solo le funzioni per il telefono (libreria, avvio/chiusura,
    schermate, controller): mai impostazioni, file, cartelle o apertura di indirizzi sul PC.

Controller: ogni telefono collegato (fino a 4) diventa un controller Xbox virtuale (ViGEmBus) e, se il
giroscopio e' attivo, uno slot del server DSU (movimento per Cemu, Ryujinx, Dolphin, Azahar...).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import Body, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

PORT = 8766
COOKIE = "gs_phone"
CODE_TTL = 600
MAX_FAILS = 10

# percorsi raggiungibili dal telefono (dopo l'abbinamento)
ALLOWED = [
    ("GET", r"/api/library"), ("GET", r"/api/game/\d+"), ("POST", r"/api/game/\d+/play"),
    ("POST", r"/api/game/\d+/flag"), ("GET", r"/api/game/\d+/shots"),
    ("GET", r"/api/running"), ("POST", r"/api/running/stop"), ("POST", r"/api/running/screenshot"),
    ("GET", r"/img/\d+/[a-z0-9_]+"), ("GET", r"/icons/(platform|store)/[^/]+"), ("GET", r"/coll-img/[^/]+/(icon|cover)"),
    ("GET", r"/shots/\d+"), ("GET", r"/m/api/me"), ("POST", r"/m/api/logout"), ("WS", r"/m/ws"),
]
PUBLIC = [("GET", r"/m/?"), ("GET", r"/m/static/[\w.-]+"), ("GET", r"/m/manifest\.webmanifest"), ("POST", r"/m/api/pair"),
          ("GET", r"/m/api/hello")]
_ALLOWED = [(m, re.compile(p + r"$")) for m, p in ALLOWED]
_PUBLIC = [(m, re.compile(p + r"$")) for m, p in PUBLIC]


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cookie(scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                name, _, val = part.strip().partition("=")
                if name == COOKIE:
                    return val
    return None


class PhoneGate:
    """Middleware ASGI del server per il telefono (il server desktop non lo usa)."""

    def __init__(self, app, hub: "PhoneHub"):
        self.app, self.hub = app, hub

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        method = "WS" if scope["type"] == "websocket" else scope.get("method", "GET")
        if method == "HEAD":
            method = "GET"
        if any(m == method and r.match(path) for m, r in _PUBLIC):
            return await self.app(scope, receive, send)
        dev = self.hub.device_for(_cookie(scope))
        if not dev:
            return await self._deny(scope, receive, send, 401, "Telefono non abbinato")
        if not any(m == method and r.match(path) for m, r in _ALLOWED):
            return await self._deny(scope, receive, send, 403, "Funzione non disponibile dal telefono")
        scope.setdefault("state", {})["phone_device"] = dev["id"]
        return await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope, receive, send, status, msg):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4000 + status})
            return
        body = json.dumps({"detail": msg}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class PhoneHub:
    """Telefoni abbinati, codice di abbinamento, server HTTPS, controller virtuali e server DSU."""

    def __init__(self, cfg, save_settings):
        self.cfg = cfg
        self.save_settings = save_settings
        self.code: str | None = None
        self.code_until = 0.0
        self.fails = 0
        self.server = None
        self.thread = None
        self.error = ""
        self.dsu = None
        self.dsu_error = ""
        self.lock = threading.Lock()
        self.slots: dict[int, dict] = {}  # slot -> {"device", "pad", "motion", "name"}

    # ------------------------------------------------------------ impostazioni
    @property
    def conf(self) -> dict:
        return {"enabled": False, "port": PORT, "devices": [], **(self.cfg.app.get("phone") or {})}

    def _save(self, **kw):
        c = {**self.conf, **kw}
        self.cfg.app["phone"] = c
        self.save_settings(self.cfg, app={"phone": c})

    def devices(self) -> list[dict]:
        return [{k: v for k, v in d.items() if k != "token"} for d in self.conf["devices"]]

    def device_for(self, token: str | None) -> dict | None:
        if not token:
            return None
        h = _sha(token)
        for d in self.conf["devices"]:
            if secrets.compare_digest(d.get("token", ""), h):
                if time.time() - d.get("last", 0) > 300:  # ultimo accesso, aggiornato al massimo ogni 5 minuti
                    self._touch(d["id"])
                return d
        return None

    def _touch(self, did: str):
        devs = self.conf["devices"]
        for d in devs:
            if d["id"] == did:
                d["last"] = time.time()
        self._save(devices=devs)

    def new_code(self) -> str:
        with self.lock:
            self.code = f"{secrets.randbelow(1_000_000):06d}"
            self.code_until = time.time() + CODE_TTL
            self.fails = 0
            return self.code

    def pair(self, code: str, name: str) -> str:
        """Ritorna il token del nuovo telefono; ValueError se il codice e' sbagliato o scaduto."""
        with self.lock:
            if not self.code or time.time() > self.code_until or self.fails >= MAX_FAILS:
                raise ValueError("Codice scaduto: generane uno nuovo dal PC (Impostazioni › Telefono)")
            if not secrets.compare_digest(str(code).strip(), self.code):
                self.fails += 1
                raise ValueError("Codice sbagliato")
            self.code = None  # un codice vale una volta sola
        token = secrets.token_urlsafe(32)
        dev = {"id": secrets.token_hex(6), "name": (name or "Telefono").strip()[:40], "token": _sha(token),
               "created": time.time(), "last": time.time()}
        self._save(devices=[*self.conf["devices"], dev])
        return token

    def revoke(self, did: str):
        self._save(devices=[d for d in self.conf["devices"] if d["id"] != did])
        with self.lock:
            for s, c in list(self.slots.items()):
                if c["device"] == did:
                    c["kick"] = True

    # ------------------------------------------------------------ server HTTPS
    def urls(self) -> list[str]:
        from .certs import lan_ips
        return [f"https://{ip}:{self.conf['port']}/m/" for ip in lan_ips()]

    def start(self, app) -> bool:
        if self.server:
            return True
        import uvicorn

        from .certs import ensure
        try:
            crt, key = ensure(Path(self.cfg.cache).parent / "certs" if self.cfg.config_path is None
                              else Path(self.cfg.config_path).parent / "certs")
        except Exception as e:
            self.error = f"Certificato HTTPS non creato: {e}"
            return False
        config = uvicorn.Config(PhoneGate(app, self), host=self.conf.get("host") or "0.0.0.0", port=self.conf["port"],
                                log_level="warning",
                                ssl_certfile=str(crt), ssl_keyfile=str(key), lifespan="off", ws_ping_interval=10)
        self.server = uvicorn.Server(config)
        self.error = ""

        def run():
            try:
                self.server.run()
            except (OSError, SystemExit) as e:
                self.error = f"Porta {self.conf['port']} non disponibile ({e})"
            finally:
                if self.server and not self.server.started:
                    self.server = None
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server is None or self.server.started:
                break
            time.sleep(0.05)
        if self.server is None or not self.server.started:
            self.error = self.error or f"Server del telefono non avviato (porta {self.conf['port']} occupata?)"
            self.server = None
            return False
        self.start_dsu()
        return True

    def stop(self):
        if self.server:
            self.server.should_exit = True
            self.server = None
        with self.lock:
            for c in self.slots.values():
                c["kick"] = True
        if self.dsu:
            self.dsu.stop()
            self.dsu = None

    def running(self) -> bool:
        return bool(self.server and self.server.started)

    def start_dsu(self):
        from .dsu import DSUServer
        if self.dsu:
            return
        d = DSUServer()
        if d.start():
            self.dsu, self.dsu_error = d, ""
        else:
            self.dsu_error = "Porta 26760 gia' in uso (un altro server DSU, es. DS4Windows?): giroscopio non disponibile"

    # ------------------------------------------------------------ controller
    def open_slot(self, device: str, name: str) -> int | None:
        with self.lock:
            for n in range(4):
                if n not in self.slots:
                    self.slots[n] = {"device": device, "name": name, "pad": None, "motion": False, "kick": False,
                                     "since": time.time()}
                    return n
        return None

    def close_slot(self, n: int):
        with self.lock:
            c = self.slots.pop(n, None)
        if not c:
            return
        if c.get("pad"):
            try:
                c["pad"].close()
            except Exception:
                pass
        if c.get("motion") and self.dsu:
            self.dsu.disconnect(n)

    def pad_input(self, n: int, m: dict) -> str | None:
        """Stato del controller dal telefono; ritorna un errore (una volta) se ViGEm non e' disponibile."""
        c = self.slots.get(n)
        if not c:
            return None
        if c["pad"] is None:
            from .vigem import VirtualPad, ViGEmError
            try:
                c["pad"] = VirtualPad()
            except ViGEmError as e:
                c["pad"] = False
                return str(e)
        if c["pad"]:
            f = lambda k: max(-1.0, min(1.0, float(m.get(k) or 0)))  # noqa: E731
            c["pad"].update(int(m.get("b") or 0), max(0.0, f("lt")), max(0.0, f("rt")), f("lx"), f("ly"), f("rx"), f("ry"))
        return None

    def motion(self, n: int, accel, gyro):
        c = self.slots.get(n)
        if not c or not self.dsu:
            return
        c["motion"] = True
        self.dsu.update_motion(n, accel, gyro)

    def status(self) -> dict:
        from .vigem import available
        ok, err = available()
        return {"enabled": self.conf["enabled"], "running": self.running(), "error": self.error, "port": self.conf["port"],
                "urls": self.urls(), "devices": self.devices(), "vigem": ok, "vigem_error": err,
                "dsu": bool(self.dsu), "dsu_error": self.dsu_error,
                "controllers": [{"slot": n, "name": c["name"], "motion": c["motion"], "pad": bool(c["pad"])}
                                for n, c in sorted(self.slots.items())]}


def register(app, hub: PhoneHub, static_dir: Path):
    """Pagine e API per il telefono, e gestione dal PC (/api/phone, raggiungibile solo in locale)."""
    mdir = static_dir / "m"

    @app.get("/m")
    @app.get("/m/")
    def m_index():
        return FileResponse(mdir / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/m/static/{name}")
    def m_static(name: str):
        p = (mdir / name).resolve()
        if p.parent != mdir.resolve() or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    @app.get("/m/manifest.webmanifest")
    def m_manifest():
        return JSONResponse({"name": "GameScraper", "short_name": "GameScraper", "start_url": "/m/", "display": "standalone",
                             "background_color": "#0f1117", "theme_color": "#0f1117",
                             "icons": [{"src": "/m/static/icon.png", "sizes": "512x512", "type": "image/png"}]},
                            media_type="application/manifest+json")

    @app.get("/m/api/hello")
    def m_hello(request: Request):
        return {"paired": bool(hub.device_for(request.cookies.get(COOKIE))) or request.client.host == "127.0.0.1"}

    @app.post("/m/api/pair")
    def m_pair(body: dict = Body(...)):
        try:
            token = hub.pair(str(body.get("code") or ""), str(body.get("name") or ""))
        except ValueError as e:
            raise HTTPException(403, str(e))
        r = JSONResponse({"ok": True})
        r.set_cookie(COOKIE, token, max_age=3 * 365 * 86400, httponly=True, secure=True, samesite="strict", path="/")
        return r

    @app.get("/m/api/me")
    def m_me(request: Request):
        d = hub.device_for(request.cookies.get(COOKIE))
        return {"device": {k: v for k, v in (d or {}).items() if k != "token"}, "vigem": hub.status()["vigem"], "dsu": bool(hub.dsu)}

    @app.post("/m/api/logout")
    def m_logout(request: Request):
        d = hub.device_for(request.cookies.get(COOKIE))
        if d:
            hub.revoke(d["id"])
        r = Response(status_code=204)
        r.delete_cookie(COOKIE, path="/")
        return r

    @app.websocket("/m/ws")
    async def m_ws(ws: WebSocket):
        import asyncio
        await ws.accept()
        dev = ws.scope.get("state", {}).get("phone_device") or "locale"
        name = next((d["name"] for d in hub.conf["devices"] if d["id"] == dev), "Telefono")
        n = hub.open_slot(dev, name)
        if n is None:
            await ws.send_json({"t": "err", "msg": "Gia' 4 controller collegati"})
            await ws.close()
            return
        await ws.send_json({"t": "ok", "slot": n, "player": n + 1, "dsu": bool(hub.dsu), "dsu_error": hub.dsu_error})
        told = False
        try:
            while True:
                if hub.slots.get(n, {}).get("kick"):
                    await ws.close(code=4001)
                    break
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=2)
                except asyncio.TimeoutError:
                    continue
                m = json.loads(raw)
                t = m.get("t")
                if t == "pad":
                    err = hub.pad_input(n, m)
                    if err and not told:
                        told = True
                        await ws.send_json({"t": "err", "msg": err})
                elif t == "m" and isinstance(m.get("a"), list) and isinstance(m.get("g"), list):
                    hub.motion(n, m["a"][:3], m["g"][:3])
                elif t == "ping":
                    await ws.send_json({"t": "pong", "ts": m.get("ts")})
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            hub.close_slot(n)

    # ------------------------------------------------------------ gestione dal PC
    def _local(request: Request):
        if request.client is None or request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403)

    @app.get("/api/phone")
    def api_phone(request: Request):
        _local(request)
        st = hub.status()
        if hub.running() and (not hub.code or time.time() > hub.code_until):
            hub.new_code()
        st["code"] = hub.code if hub.running() else None
        st["code_until"] = hub.code_until
        st["qr"] = _qr_svg(f"{st['urls'][0]}#code={hub.code}") if hub.running() and st["urls"] else ""
        return st

    @app.post("/api/phone")
    def api_phone_set(request: Request, body: dict = Body(...)):
        _local(request)
        if "port" in body and str(body["port"]).isdigit() and 1024 < int(body["port"]) < 65536:
            hub._save(port=int(body["port"]))
        if "enabled" in body:
            on = bool(body["enabled"])
            hub._save(enabled=on)
            if on:
                hub.start(app)
            else:
                hub.stop()
        return api_phone(request)

    @app.post("/api/phone/code")
    def api_phone_code(request: Request):
        _local(request)
        hub.new_code()
        return api_phone(request)

    @app.delete("/api/phone/devices/{did}")
    def api_phone_revoke(request: Request, did: str):
        _local(request)
        hub.revoke(did)
        return api_phone(request)


def _qr_svg(text: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")
