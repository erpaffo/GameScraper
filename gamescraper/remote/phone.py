"""App per il telefono: libreria, avvio dei giochi sul PC, controller sullo schermo con giroscopio.

Sicurezza: l'app desktop resta solo su 127.0.0.1. Il telefono usa un secondo server HTTPS
(0.0.0.0, porta 8766 predefinita) attivato dall'utente, davanti al quale c'e' un "cancello":
  - senza abbinamento si raggiungono solo la pagina /m e l'abbinamento;
  - l'abbinamento usa un codice di 6 cifre (nel QR mostrato dal PC) valido 10 minuti e usabile una
    volta, con al massimo 10 tentativi sbagliati; il telefono riceve un token (cookie HttpOnly) e il
    PC ne conserva solo l'impronta SHA-256; i telefoni si revocano dalle impostazioni;
  - anche abbinati, si raggiungono solo le funzioni per il telefono (libreria, avvio/chiusura,
    schermate, controller): mai impostazioni, file, cartelle o apertura di indirizzi sul PC;
  - l'ospite a distanza («gioca con un amico») entra con un invito suo, diverso dal codice dei telefoni, e
    ottiene solo il controller: niente libreria, niente mouse, niente scambio dei posti.

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
    # scheda Attivita' del telefono: lavori in corso sul PC e comandi (store, ricerca, ricostruzione, chiudi gioco)
    ("GET", r"/api/mobile/state"), ("POST", r"/api/mobile/action"),
    ("GET", r"/img/\d+/[a-z0-9_]+"), ("GET", r"/icons/(platform|store)/[^/]+"), ("GET", r"/coll-img/[^/]+/(icon|cover)"),
    ("GET", r"/shots/\d+"), ("GET", r"/m/api/me"), ("POST", r"/m/api/logout"), ("WS", r"/m/ws"), ("WS", r"/m/mic"),
    ("WS", r"/m/screen"), ("GET", r"/m/api/pad"), ("POST", r"/m/api/pad"),
]
PLAYER_COLORS = ["#4aa3ff", "#ff5f5f", "#3ddc84", "#f5c518"]  # giocatore 1..4 (come i LED dei controller)
PUBLIC = [("GET", r"/m/?"), ("GET", r"/m/static/[\w.-]+"), ("GET", r"/m/manifest\.webmanifest"), ("POST", r"/m/api/pair"),
          ("GET", r"/m/api/hello"), ("POST", r"/api/sync/pair"), ("GET", r"/m/sw\.js")]
# un altro PC abbinato per la sincronizzazione (kind "pc") raggiunge solo lo scambio dei dati, nient'altro
PC_ALLOWED = [("POST", r"/api/sync/exchange")]
# l'ospite a distanza (kind "guest") ha solo il controller: niente libreria, mouse o scambio dei posti
GUEST_ALLOWED = [("GET", r"/m/api/me"), ("POST", r"/m/api/logout"), ("GET", r"/api/running"), ("WS", r"/m/ws"),
                 ("POST", r"/g/api/pin")]
# pagine che anche un ospite non ancora entrato deve poter aprire
GUEST_PUBLIC = [("GET", r"/g/?"), ("GET", r"/g/api/hello"), ("POST", r"/g/api/join")]
_ALLOWED = [(m, re.compile(p + r"$")) for m, p in ALLOWED]
_PC_ALLOWED = [(m, re.compile(p + r"$")) for m, p in PC_ALLOWED]
_GUEST_ALLOWED = [(m, re.compile(p + r"$")) for m, p in GUEST_ALLOWED]
_GUEST_PUBLIC = [(m, re.compile(p + r"$")) for m, p in GUEST_PUBLIC]
_PUBLIC = [(m, re.compile(p + r"$")) for m, p in PUBLIC]


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# impostazioni del pad che il telefono puo' salvare sul PC: solo queste voci, con valori nei limiti
PAD_LAYOUTS = {"xbox", "ps", "switch", "gc", "arcade", "wii", "wiis", "nunchuk", "buzz", "wheel", "guitar", "gun", "mic", "ds"}
PAD_ENUM = {"gyro": {"off", "dsu", "aim"}, "src": {"auto", "touch", "gamepad"}, "layout": PAD_LAYOUTS}
PAD_NUM = {"sens": (0.2, 3.0), "scale": (0.8, 1.3)}
PAD_BOOL = ("invx", "invy", "vib", "rumble", "autoLayout", "nlabel")
PAD_POS_KEYS = {"l2", "l1", "r1", "r2", "ls", "rs", "dpad", "face", "mid"}


def _clean_pad(body: dict) -> dict:
    """Tiene solo le voci previste, con i valori riportati nei limiti: il telefono e' un confine di fiducia."""
    if len(json.dumps(body)) > 20000:
        raise ValueError("Impostazioni del controller troppo grandi")
    out: dict = {}
    for k, allowed in PAD_ENUM.items():
        if str(body.get(k)) in allowed:
            out[k] = str(body[k])
    for k, (lo, hi) in PAD_NUM.items():
        try:
            out[k] = round(min(hi, max(lo, float(body[k]))), 3)
        except (KeyError, TypeError, ValueError):
            pass
    for k in PAD_BOOL:
        if k in body:
            out[k] = bool(body[k])
    pos = body.get("pos")
    if isinstance(pos, dict):
        clean: dict = {}
        for lay, vals in list(pos.items())[:12]:
            if lay not in PAD_LAYOUTS or not isinstance(vals, dict):
                continue
            row = {}
            for name, c in list(vals.items())[:12]:
                if name not in PAD_POS_KEYS or not isinstance(c, dict):
                    continue
                try:
                    row[name] = {"x": round(min(95, max(0, float(c["x"]))), 2),
                                 "y": round(min(95, max(0, float(c["y"]))), 2),
                                 "k": round(min(1.8, max(0.6, float(c.get("k", 1)))), 3)}
                except (KeyError, TypeError, ValueError):
                    continue
            if row:
                clean[lay] = row
        out["pos"] = clean
    return out


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
        if any(m == method and r.match(path) for m, r in _PUBLIC + _GUEST_PUBLIC):
            return await self.app(scope, receive, send)
        dev = self.hub.device_for(_cookie(scope))
        if not dev:
            return await self._deny(scope, receive, send, 401, "Telefono non abbinato")
        kind = dev.get("kind")
        rules = _PC_ALLOWED if kind == "pc" else _GUEST_ALLOWED if kind == "guest" else _ALLOWED
        if not any(m == method and r.match(path) for m, r in rules):
            return await self._deny(scope, receive, send, 403, "Funzione non disponibile da questo dispositivo")
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
        self.guest_code: str | None = None  # invito per un amico a distanza (separato dal codice dei telefoni)
        self.guest_until = 0.0
        self.guest_fails = 0
        self.server = None
        self.thread = None
        self.error = ""
        self.dsu = None
        self.dsu_error = ""
        self.lock = threading.Lock()
        self.slots: dict[int, dict] = {}  # slot -> {"cid", "device", "pad", "motion", "name", "rumble", "mouse"}
        self.rev = 0  # cambia a ogni collegamento, scollegamento o scambio: i telefoni aggiornano la sala giocatori
        self.mic_busy = False
        self.screen = None  # schermo inferiore DS/3DS della partita in corso (remote/dualscreen.Session), se c'e'
        self.screen_clients = 0
        self.last_touch = 0.0  # ultimo tocco sullo schermo del telefono (per riportarlo sul PC se non lo si usa)
        self.screen_seen = 0.0  # ultimo momento in cui un telefono guardava lo schermo

    # ------------------------------------------------------------ impostazioni
    @property
    def conf(self) -> dict:
        # mouse: il telefono puo' muovere il mouse del PC (touchpad / pistola ottica); spento finche' non lo attiva il PC
        # touchscreen: nei giochi DS/3DS il telefono fa da schermo inferiore (se e' collegato all'avvio)
        # screen_idle: secondi senza tocchi dopo i quali lo schermo inferiore torna sul PC
        # public_url: indirizzo pubblico del PC (tunnel) per il link d'invito dell'ospite a distanza
        # video_addr: indirizzo da scrivere in Moonlight (per esempio quello di Tailscale) mostrato all'ospite
        return {"enabled": False, "port": PORT, "devices": [], "mouse": False, "mic_device": "", "touchscreen": True,
                "screen_idle": 30, "public_url": "", "video_addr": "", **(self.cfg.app.get("phone") or {})}

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

    def pair(self, code: str, name: str, kind: str = "phone") -> str:
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
               "created": time.time(), "last": time.time(), "kind": kind}
        self._save(devices=[*self.conf["devices"], dev])
        return token

    # ------------------------------------------------------------ amico a distanza
    def new_invite(self) -> str:
        """Codice d'invito per l'ospite (6 cifre, 10 minuti, un ospite): diverso da quello dei telefoni,
        perche' questo lo si manda a qualcuno che non e' in casa."""
        with self.lock:
            self.guest_code = f"{secrets.randbelow(1_000_000):06d}"
            self.guest_until = time.time() + CODE_TTL
            self.guest_fails = 0
            return self.guest_code

    def join(self, code: str, name: str) -> str:
        """Token dell'ospite; ValueError se il codice e' sbagliato, scaduto o gia' usato."""
        with self.lock:
            if not self.guest_code or time.time() > self.guest_until or self.guest_fails >= MAX_FAILS:
                raise ValueError("Invito scaduto: sul PC ne serve uno nuovo")
            if not secrets.compare_digest(str(code).strip(), self.guest_code):
                self.guest_fails += 1
                raise ValueError("Codice sbagliato")
            self.guest_code = None
        token = secrets.token_urlsafe(32)
        dev = {"id": secrets.token_hex(6), "name": (name or "Ospite").strip()[:40], "token": _sha(token),
               "created": time.time(), "last": time.time(), "kind": "guest"}
        self._save(devices=[*self.conf["devices"], dev])
        return token

    def base_url(self) -> str:
        """Indirizzo con cui si raggiunge il PC da fuori: quello pubblico (tunnel) se c'e', altrimenti la rete di casa."""
        pub = str(self.conf.get("public_url") or "").strip().rstrip("/")
        if pub:
            return pub if "://" in pub else f"https://{pub}"
        urls = self.urls()
        return urls[0].removesuffix("/m/") if urls else f"https://127.0.0.1:{self.conf['port']}"

    def invite_link(self) -> str:
        code = f"#code={self.guest_code}" if self.guest_code else ""
        return f"{self.base_url()}/g/{code}"

    def invite(self) -> dict:
        """Invito attivo (o nessun codice): lo mostra la scheda del PC."""
        live = bool(self.guest_code) and time.time() <= self.guest_until
        return {"code": self.guest_code if live else None, "until": self.guest_until if live else 0.0,
                "link": self.invite_link()}

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
            except BaseException as e:
                self.error = f"Errore server HTTPS ({e})"
            finally:
                if self.server and not self.server.started:
                    self.server = None
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        for _ in range(150):
            if self.server is None or self.server.started:
                break
            time.sleep(0.1)
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
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)
        self.thread = None
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
                    self.slots[n] = {"cid": secrets.token_hex(4), "device": device, "name": name, "pad": None, "motion": False,
                                     "kick": False, "since": time.time(), "rumble": None, "mouse": None}
                    self.rev += 1
                    return n
        return None

    def slot_of(self, cid: str) -> int | None:
        """Slot attuale di un collegamento (cambia se i giocatori si scambiano il posto)."""
        return next((n for n, c in list(self.slots.items()) if c["cid"] == cid), None)

    def close_slot(self, n: int):
        with self.lock:
            c = self.slots.pop(n, None)
            if c:
                self.rev += 1
        if not c:
            return
        if c.get("pad"):
            try:
                c["pad"].close()
            except Exception:
                pass
        if c.get("mouse"):
            c["mouse"].release()
        if self.dsu:
            self.dsu.disconnect(n)

    def ensure_pad(self, n: int) -> str | None:
        """Crea subito il controller virtuale: cosi' il numero del giocatore segue l'ordine di collegamento."""
        c = self.slots.get(n)
        if not c or c["pad"] is not None:
            return None
        from .vigem import VirtualPad, ViGEmError
        try:
            # vibrazioni del gioco -> telefono di questo slot (anche dopo uno scambio di posto)
            c["pad"] = VirtualPad(on_rumble=lambda large, small, n=n: self._rumble(n, large, small))
        except ViGEmError as e:
            c["pad"] = False
            return str(e)
        return None

    def _rumble(self, n: int, large: int, small: int):
        c = self.slots.get(n)
        if c and c.get("rumble"):
            c["rumble"](large, small)

    def player(self, n: int) -> int:
        """Numero del giocatore come lo vedono giochi ed emulatori (indice XInput + 1), altrimenti slot + 1."""
        c = self.slots.get(n)
        pad = c and c.get("pad")
        i = pad.user_index() if pad else None
        return (i if i is not None else n) + 1

    def players(self) -> list[dict]:
        return [{"slot": n, "player": self.player(n), "name": c["name"], "color": PLAYER_COLORS[(self.player(n) - 1) % 4]}
                for n, c in sorted(self.slots.items())]

    def swap(self, a: int, b: int):
        """Scambia di posto due telefoni (anche con un posto libero): il controller virtuale e lo slot DSU restano
        al loro posto, cambia il telefono che li usa. Nessuno deve ricollegarsi."""
        if a == b or not (0 <= a < 4 and 0 <= b < 4):
            raise ValueError("Posti non validi")
        with self.lock:
            ca, cb = self.slots.get(a), self.slots.get(b)
            if not ca and not cb:
                raise ValueError("Nessun telefono in questi posti")
            keep = {"pad", "motion"}  # restano allo slot
            pa = {k: ca[k] for k in keep} if ca else None
            pb = {k: cb[k] for k in keep} if cb else None
            for src, dst, pads in ((ca, b, pb), (cb, a, pa)):
                if src:
                    src.update(pads or {"pad": None, "motion": False})
                    self.slots[dst] = src
                else:
                    self.slots.pop(dst, None)
                    if pads and pads["pad"]:  # posto lasciato libero: il controller virtuale si scollega
                        try:
                            pads["pad"].close()
                        except Exception:
                            pass
            self.rev += 1
        if self.dsu:
            for n in (a, b):
                if n not in self.slots:
                    self.dsu.disconnect(n)

    def pad_input(self, n: int, m: dict) -> str | None:
        """Stato del controller dal telefono; ritorna un errore (una volta) se ViGEm non e' disponibile."""
        c = self.slots.get(n)
        if not c:
            return None
        err = self.ensure_pad(n) if c["pad"] is None else None
        f = lambda k: max(-1.0, min(1.0, float(m.get(k) or 0)))  # noqa: E731
        st = (int(m.get("b") or 0) & 0xFFFF, max(0.0, f("lt")), max(0.0, f("rt")), f("lx"), f("ly"), f("rx"), f("ry"))
        if c["pad"]:
            c["pad"].update(*st)
        if self.dsu:  # gli stessi tasti anche al server DSU (Wii Remote di Dolphin tutto su DSU)
            from .dsu import pad_bytes
            self.dsu.update_pad(n, pad_bytes(*st))
        return err

    def motion(self, n: int, accel, gyro):
        c = self.slots.get(n)
        if not c or not self.dsu:
            return
        c["motion"] = True
        self.dsu.update_motion(n, accel, gyro)

    def mouse_input(self, n: int, m: dict) -> str | None:
        """Touchpad / pistola ottica: posizione assoluta (0..1) e tasti. Solo se permesso dal PC."""
        c = self.slots.get(n)
        if not c:
            return None
        if not self.conf.get("mouse"):
            return "Mouse dal telefono non permesso: attivalo sul PC in Impostazioni › Telefono e streaming"
        if c["mouse"] is None:
            from .mouse import Mouse
            c["mouse"] = Mouse()
        try:
            c["mouse"].send(float(m.get("x", 0.5)), float(m.get("y", 0.5)), bool(m.get("l")), bool(m.get("r")))
        except (TypeError, ValueError):
            pass
        return None

    def pad_settings(self) -> dict:
        """Aspetto del pad, sensibilità e posizioni dei tasti: stanno sul PC (valgono per tutti i telefoni e
        finiscono nei backup), non nel browser del singolo telefono."""
        return self.conf.get("pad") or {}

    def set_pad_settings(self, body: dict) -> dict:
        pad = _clean_pad(body or {})
        self._save(pad=pad)
        return pad

    def key_input(self, name: str):
        """Tasto o tasto multimediale sul PC (telecomando dal telefono); ValueError se non e' previsto."""
        from .keyboard import press
        press(name)

    def pad_state(self) -> list[dict]:
        """Stato degli ultimi comandi ricevuti da ogni telefono: serve alla pagina «Prova controller» del PC."""
        from .vigem import BUTTONS
        out = []
        for n, c in sorted(self.slots.items()):
            pad = c.get("pad")
            r = getattr(pad, "report", None) if pad else None
            axes = (round((r.sThumbLX if r else 0) / 32767, 3), round((r.sThumbLY if r else 0) / 32767, 3),
                    round((r.sThumbRX if r else 0) / 32767, 3), round((r.sThumbRY if r else 0) / 32767, 3))
            out.append({"slot": n, "player": self.player(n), "name": c["name"],
                        "color": PLAYER_COLORS[(self.player(n) - 1) % 4], "connected": bool(pad),
                        "motion": bool(c.get("motion")),
                        "buttons": [k for k, bit in BUTTONS.items() if r and r.wButtons & bit],
                        "lt": round((r.bLeftTrigger if r else 0) / 255, 3),
                        "rt": round((r.bRightTrigger if r else 0) / 255, 3),
                        "lx": axes[0], "ly": axes[1], "rx": axes[2], "ry": axes[3]})
        return out

    def rumble_test(self, slot: int):
        """Fa vibrare il telefono di quel posto per un attimo: serve a capire se la vibrazione arriva."""
        c = self.slots.get(slot)
        if not c or not c.get("rumble"):
            raise ValueError("Nessun telefono collegato in questo posto")

        def stop():
            cur = self.slots.get(slot)
            if cur and cur.get("rumble"):
                cur["rumble"](0, 0)

        c["rumble"](255, 255)
        threading.Timer(0.7, stop).start()

    def status(self) -> dict:
        from .vigem import available
        ok, err = available()
        return {"enabled": self.conf["enabled"], "running": self.running(), "error": self.error, "port": self.conf["port"],
                "urls": self.urls(), "devices": [d for d in self.devices() if d.get("kind") not in ("pc", "guest")],
                "guests": [d for d in self.devices() if d.get("kind") == "guest"],
                "public_url": self.conf.get("public_url", ""), "video_addr": self.conf.get("video_addr", ""),
                "invite": self.invite(),
                "vigem": ok, "vigem_error": err,
                "dsu": bool(self.dsu), "dsu_error": self.dsu_error, "mouse": bool(self.conf.get("mouse")),
                "touchscreen": bool(self.conf.get("touchscreen")),
                "screen_idle": int(self.conf.get("screen_idle") or 30),
                "mic_device": self.conf.get("mic_device", ""), "mic_devices": _mic_devices(),
                "controllers": [{**p, "motion": self.slots[p["slot"]]["motion"], "pad": bool(self.slots[p["slot"]]["pad"])}
                                for p in self.players() if p["slot"] in self.slots]}


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

    @app.get("/m/sw.js")
    def m_sw():
        """Service worker servito dalla radice di /m (cosi' il suo ambito copre tutta l'app del telefono)."""
        return FileResponse(mdir / "sw.js", headers={"Service-Worker-Allowed": "/m/", "Cache-Control": "no-cache"})

    @app.get("/m/manifest.webmanifest")
    def m_manifest():
        return JSONResponse({"id": "/m/", "scope": "/m/", "start_url": "/m/", "name": "GameScraper",
                             "short_name": "GameScraper", "display": "standalone",
                             "background_color": "#0f1117", "theme_color": "#0f1117",
                             "icons": [{"src": "/m/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                                       {"src": "/m/static/icon.png", "sizes": "512x512", "type": "image/png"}]},
                            media_type="application/manifest+json")

    @app.get("/m/api/pad")
    def m_pad():
        """Aspetto, sensibilità e posizione dei tasti del controller: salvati sul PC, uguali per tutti i telefoni."""
        return hub.pad_settings()

    @app.post("/m/api/pad")
    def m_pad_set(body: dict = Body(...)):
        try:
            return hub.set_pad_settings(body)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/m/api/hello")
    def m_hello(request: Request):
        d = hub.device_for(request.cookies.get(COOKIE))
        return {"paired": bool(d) or request.client.host == "127.0.0.1", "guest": bool(d and d.get("kind") == "guest")}

    # ------------------------------------------------------------ ospite a distanza
    gdir = static_dir / "g"

    @app.get("/g")
    @app.get("/g/")
    def g_index():
        return FileResponse(gdir / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/g/api/hello")
    def g_hello(request: Request):
        """Per la pagina dell'ospite: e' gia' entrato? che indirizzo deve scrivere in Moonlight?"""
        d = hub.device_for(request.cookies.get(COOKIE))
        return {"paired": bool(d and d.get("kind") == "guest"), "video": hub.conf.get("video_addr", "")}

    @app.post("/g/api/join")
    def g_join(request: Request, body: dict = Body(...)):
        """L'ospite entra con il codice dell'invito: diventa un altro controller (giocatore 2, 3...)."""
        try:
            token = hub.join(str(body.get("code") or ""), str(body.get("name") or ""))
        except ValueError as e:
            raise HTTPException(403, str(e))
        r = JSONResponse({"ok": True})
        # vale mezza giornata: l'ospite e' di passaggio, se torna domani serve un invito nuovo
        r.set_cookie(COOKIE, token, max_age=12 * 3600, httponly=True, secure=request.url.scheme == "https",
                     samesite="strict", path="/")
        return r

    @app.post("/g/api/pin")
    def g_pin(request: Request, body: dict = Body(...)):
        """Il PIN che Moonlight mostra all'ospite: lo conferma il PC, cosi' nessuno deve aprire Sunshine."""
        from .sunshine import Sunshine
        conf = hub.cfg.app.get("sunshine") or {}
        pin = str(body.get("pin") or "").strip()
        if not re.fullmatch(r"\d{4}", pin):
            raise HTTPException(400, "Il PIN di Moonlight ha 4 cifre")
        if not (conf.get("user") and conf.get("password")):
            raise HTTPException(409, "Il PC non ha le credenziali di Sunshine: vanno salvate in Impostazioni › Telefono e streaming")
        dev = hub.device_for(request.cookies.get(COOKIE)) or {}
        s = Sunshine(conf["user"], conf["password"])
        try:
            return {"ok": True, **s.pin(pin, dev.get("name", ""))}
        except PermissionError as e:
            raise HTTPException(401, str(e))
        except Exception as e:
            raise HTTPException(502, f"Sunshine non risponde: {e}")
        finally:
            s.close()

    @app.post("/m/api/pair")
    def m_pair(request: Request, body: dict = Body(...)):
        try:
            token = hub.pair(str(body.get("code") or ""), str(body.get("name") or ""))
        except ValueError as e:
            raise HTTPException(403, str(e))
        r = JSONResponse({"ok": True})
        # il cookie e' Secure (il telefono usa HTTPS): se la pagina arriva in chiaro il browser lo scarterebbe
        r.set_cookie(COOKIE, token, max_age=3 * 365 * 86400, httponly=True,
                     secure=request.url.scheme == "https", samesite="strict", path="/")
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
        guest = next((d.get("kind") for d in hub.conf["devices"] if d["id"] == dev), "") == "guest"
        n = hub.open_slot(dev, name)
        if n is None:
            await ws.send_json({"t": "err", "msg": "Gia' 4 controller collegati"})
            await ws.close()
            return
        cid = hub.slots[n]["cid"]
        err0 = await asyncio.to_thread(hub.ensure_pad, n)
        from .mouse import screen_size
        await ws.send_json({"t": "ok", "slot": n, "player": hub.player(n), "dsu": bool(hub.dsu), "dsu_error": hub.dsu_error,
                            "screen": list(screen_size()), "guest": guest, "mouse": bool(hub.conf.get("mouse")) and not guest})
        told = set()
        if err0:
            told.add(err0)
            await ws.send_json({"t": "err", "msg": err0})
        # le vibrazioni arrivano da un thread del driver: passano alla coda di questo loop
        loop, queue = asyncio.get_running_loop(), asyncio.Queue()
        hub.slots[n]["rumble"] = lambda large, small: loop.call_soon_threadsafe(queue.put_nowait, (large, small))
        recv, rumble = asyncio.ensure_future(ws.receive_text()), asyncio.ensure_future(queue.get())
        seen_rev = -1
        try:
            while True:
                n = hub.slot_of(cid)
                if n is None or hub.slots[n].get("kick"):
                    await ws.close(code=4001)
                    break
                if seen_rev != hub.rev:  # sala giocatori: chi e' chi, con il colore
                    seen_rev = hub.rev
                    await ws.send_json({"t": "players", "me": hub.player(n), "slot": n, "list": hub.players()})
                done, _ = await asyncio.wait({recv, rumble}, timeout=1, return_when=asyncio.FIRST_COMPLETED)
                if rumble in done:
                    large, small = rumble.result()
                    await ws.send_json({"t": "rumble", "l": large, "s": small})
                    rumble = asyncio.ensure_future(queue.get())
                if recv not in done:
                    continue
                raw = recv.result()
                recv = asyncio.ensure_future(ws.receive_text())
                m = json.loads(raw)
                t = m.get("t")
                err = None
                if t == "pad":
                    err = hub.pad_input(n, m)
                elif t == "m" and isinstance(m.get("a"), list) and isinstance(m.get("g"), list):
                    hub.motion(n, m["a"][:3], m["g"][:3])
                elif t == "mouse" and not guest:
                    err = hub.mouse_input(n, m)
                elif t == "swap" and not guest and str(m.get("slot", "")).isdigit():
                    try:
                        hub.swap(n, int(m["slot"]))
                    except ValueError as e:
                        err = str(e)
                elif t == "ping":
                    await ws.send_json({"t": "pong", "ts": m.get("ts")})
                if err and err not in told:
                    told.add(err)
                    await ws.send_json({"t": "err", "msg": err})
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            for f in (recv, rumble):
                f.cancel()
            n = hub.slot_of(cid)
            if n is not None:
                hub.close_slot(n)

    @app.websocket("/m/screen")
    async def m_screen(ws: WebSocket):
        """Schermo inferiore DS/3DS: fotogrammi JPEG (binari) verso il telefono, tocchi del pennino verso l'emulatore.
        Finche' un telefono guarda lo schermo e lo si usa, sul PC resta nascosto: a decidere quando riportarlo sul PC
        e' il controllo in server.py (telefono scollegato o fermo da troppo tempo)."""
        import asyncio
        await ws.accept()
        with hub.lock:
            hub.screen_clients += 1
        hub.last_touch = hub.screen_seen = time.time()
        shown = None
        recv = asyncio.ensure_future(ws.receive_text())
        try:
            while True:
                with hub.lock:
                    s = hub.screen
                if s is not shown:  # partita nuova (o finita): lo schermo va sul telefono
                    shown = s
                    if s:
                        await asyncio.to_thread(s.hide)
                    await ws.send_json({"t": "screen", "on": bool(s)})
                if s:
                    f = await asyncio.to_thread(s.frame)
                    if f:
                        await ws.send_bytes(f)
                done, _ = await asyncio.wait({recv}, timeout=1 / 30 if s else 1)
                if recv in done:
                    m = json.loads(recv.result())
                    recv = asyncio.ensure_future(ws.receive_text())
                    if s and m.get("t") == "touch":
                        hub.last_touch = time.time()  # il telefono si sta usando: lo schermo resta suo
                        await asyncio.to_thread(s.touch, float(m.get("x", 0)), float(m.get("y", 0)), bool(m.get("d")))
        except (WebSocketDisconnect, RuntimeError, ValueError, OSError):
            pass
        finally:
            recv.cancel()
            with hub.lock:
                hub.screen_clients -= 1
                s, last = hub.screen, hub.screen_clients <= 0
            hub.screen_seen = time.time()
            if s and last and s.down:  # nessun telefono guarda piu': pennino sollevato (il riquadro sul PC lo mette il controllo)
                try:
                    s.touch(0, 0, False)
                except OSError:
                    pass

    @app.websocket("/m/mic")
    async def m_mic(ws: WebSocket):
        """Microfono del telefono (PCM 16 bit mono 16 kHz) -> uscita audio del PC scelta (cavo virtuale per SingStar)."""
        import asyncio
        await ws.accept()
        if hub.mic_busy:
            await ws.send_json({"t": "err", "msg": "Un altro telefono sta già usando il microfono"})
            await ws.close()
            return
        from . import mic
        names = await asyncio.to_thread(mic.devices)
        dev = mic.pick(names, hub.conf.get("mic_device", ""))
        if dev is None:
            await ws.send_json({"t": "err", "msg": "Nessun cavo audio virtuale sul PC (es. VB-Cable): installalo e sceglilo "
                                                   "in Impostazioni › Telefono e streaming"})
            await ws.close()
            return
        try:
            player = mic.Player(dev)
        except OSError as e:
            await ws.send_json({"t": "err", "msg": str(e)})
            await ws.close()
            return
        hub.mic_busy = True
        await ws.send_json({"t": "ok", "device": names[dev], "rate": mic.RATE})
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    player.play(msg["bytes"][:16000])
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.mic_busy = False
            player.close()

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
        if "mouse" in body:
            hub._save(mouse=bool(body["mouse"]))
        if "touchscreen" in body:
            hub._save(touchscreen=bool(body["touchscreen"]))
        if "screen_idle" in body:
            try:
                secs = int(float(body["screen_idle"]))
            except (TypeError, ValueError):
                raise HTTPException(400, "Secondi non validi per lo schermo sul PC")
            hub._save(screen_idle=min(600, max(5, secs)))
        if "mic_device" in body:
            hub._save(mic_device=str(body["mic_device"] or "")[:64])
        if "public_url" in body:
            hub._save(public_url=str(body["public_url"] or "").strip().rstrip("/")[:120])
        if "video_addr" in body:
            hub._save(video_addr=str(body["video_addr"] or "").strip()[:64])
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

    @app.post("/api/phone/invite")
    def api_phone_invite(request: Request):
        """Link e codice per far entrare un amico a distanza (accende il server HTTPS se serve)."""
        _local(request)
        if not hub.running() and not hub.start(app):
            raise HTTPException(500, hub.error or "Server di rete non avviato")
        hub.new_invite()
        return api_phone(request)

    @app.delete("/api/phone/devices/{did}")
    def api_phone_revoke(request: Request, did: str):
        _local(request)
        hub.revoke(did)
        return api_phone(request)

    @app.post("/api/phone/swap")
    def api_phone_swap(request: Request, body: dict = Body(...)):
        _local(request)
        try:
            hub.swap(int(body.get("a")), int(body.get("b")))
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e) or "Posti non validi")
        return api_phone(request)

    @app.get("/api/phone/pads")
    def api_phone_pads(request: Request):
        """Ultimo stato ricevuto da ogni telefono collegato: pulsanti premuti, grilletti e levette (pagina di prova)."""
        _local(request)
        st = hub.status()
        return {"pads": hub.pad_state(), "vigem": st["vigem"], "error": st["vigem_error"]}

    @app.post("/api/phone/pads/rumble")
    def api_phone_rumble(request: Request, body: dict = Body(...)):
        _local(request)
        try:
            hub.rumble_test(int(body.get("slot", 0)))
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e) or "Posto non valido")
        return {"ok": True}

    @app.post("/api/phone/emusetup")
    def api_phone_emusetup(request: Request, body: dict = Body(...)):
        """Configura PCSX2 (Buzz!, chitarra) o Dolphin (Wii Remote) per i telefoni; restore = rimette la copia."""
        _local(request)
        from . import emusetup
        what = str(body.get("what") or "")
        try:
            if body.get("restore"):
                return {"restored": emusetup.restore(what, hub.cfg.emulators)}
            return emusetup.setup(what, hub.cfg.emulators, int(body.get("players") or 1), bool(body.get("wiimotes", True)))
        except (RuntimeError, ValueError, OSError) as e:
            raise HTTPException(400, str(e))


def _mic_devices() -> list[str]:
    try:
        from .mic import devices
        return devices()
    except Exception:
        return []


def _qr_svg(text: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")
