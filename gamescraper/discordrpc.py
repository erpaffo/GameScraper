"""Presenza su Discord («Sto giocando a …») tramite IPC locale: nessuna dipendenza nuova.

Discord espone un canale locale (named pipe su Windows, socket su Unix) su cui si inviano frame
JSON: handshake con l'id dell'applicazione, poi `SET_ACTIVITY` per aggiornare la presenza.
Funziona solo con il client Discord aperto; se non c'è, si resta in silenzio e si riprova dopo.

Non si inviano immagini: Discord accetta solo asset caricati sull'applicazione (non URL), quindi
la copertina non è mostrabile via IPC. Titolo, piattaforma e tempo di gioco sì.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
import uuid
from pathlib import Path

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4

MAX_TEXT = 128


def pipe_paths(count: int = 10) -> list[str]:
    """Percorsi possibili dei canali di Discord (il primo libero è quello giusto)."""
    if sys.platform == "win32":
        return [rf"\\?\pipe\discord-ipc-{i}" for i in range(count)]
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    return [str(Path(base) / f"discord-ipc-{i}") for i in range(count)]


def frame(op: int, payload: dict) -> bytes:
    """Frame del protocollo: opcode e lunghezza (little endian) + JSON."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("<ii", op, len(body)) + body


def read_frame(read) -> tuple[int, dict]:
    """Legge un frame da `read(n) -> bytes` (usato anche nei test con un finto trasporto)."""
    head = read(8)
    if len(head) < 8:
        raise ConnectionError("canale chiuso")
    op, size = struct.unpack("<ii", head)
    body = b""
    while len(body) < size:
        chunk = read(size - len(body))
        if not chunk:
            raise ConnectionError("canale chiuso")
        body += chunk
    return op, json.loads(body.decode("utf-8") or "{}")


def _clip(text: str | None) -> str:
    return (text or "")[:MAX_TEXT]


def activity(details: str, state: str = "", started: float | None = None,
             large_text: str = "", large_image: str = "", small_text: str = "",
             small_image: str = "") -> dict:
    """Oggetto activity di Discord. I campi vuoti si omettono (Discord li rifiuta se vuoti)."""
    act: dict = {"details": _clip(details) or "GameScraper",
                 "instance": False}
    if state:
        act["state"] = _clip(state)
    if started:
        act["timestamps"] = {"start": int(started)}
    assets = {}
    if large_image:
        assets["large_image"] = large_image
    if large_text:
        assets["large_text"] = _clip(large_text)
    if small_image:
        assets["small_image"] = small_image
    if small_text:
        assets["small_text"] = _clip(small_text)
    if assets:
        act["assets"] = assets
    return act


class _Channel:
    """Canale verso Discord: pipe su Windows, socket su Unix. Scrittura/lettura bloccanti."""

    def __init__(self, path: str):
        self.path = path
        if sys.platform == "win32":
            self._f = open(path, "r+b", buffering=0)
            self._sock = None
        else:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(5)
            self._sock.connect(path)
            self._f = self._sock.makefile("rwb", buffering=0)

    def write(self, data: bytes):
        self._f.write(data)

    def read(self, n: int) -> bytes:
        try:
            return self._f.read(n) or b""
        except OSError:
            return b""

    def close(self):
        for obj in (self._f, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass


class DiscordPresence:
    """Connessione a Discord e aggiornamento della presenza, con riconnessione silenziosa.

    `connect` è iniettabile nei test: riceve un percorso e restituisce un oggetto con write/read/close.
    """

    def __init__(self, client_id: str, log=None, connect=None, pid: int | None = None):
        self.client_id = str(client_id or "").strip()
        self.pid = pid or os.getpid()
        self.log = log or (lambda m: None)
        self._connect = connect or _Channel
        self._ch = None
        self._ready = threading.Event()
        self._alive = False  # il lettore ? attivo: se il canale cade, il prossimo invio riconnette
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self.error: str | None = None

    # ---------------------------------------------------------------- connessione
    def connected(self) -> bool:
        return self._ch is not None and self._ready.is_set() and self._alive

    def connect(self) -> bool:
        """Apre il canale e fa l'handshake. False se Discord non è aperto (nessun errore mostrato)."""
        if not self.client_id:
            self.error = "manca l'ID applicazione Discord"
            return False
        with self._lock:
            if self.connected():
                return True
            self.close(quiet=True)
            last = None
            for path in pipe_paths():
                try:
                    self._ch = self._connect(path)
                    break
                except (OSError, FileNotFoundError) as e:
                    last = e
            if self._ch is None:
                self.error = "Discord non è aperto" if isinstance(last, (FileNotFoundError, ConnectionError)) else str(last)
                return False
            self._ready.clear()
            try:
                self._ch.write(frame(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id}))
            except OSError as e:
                self.error = f"handshake fallito: {e}"
                self.close(quiet=True)
                return False
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
        self._alive = True
        ok = self._ready.wait(4)
        self.error = None if ok else "Discord non ha risposto"
        return ok

    def _read_loop(self):
        ch = self._ch
        while ch is not None:
            try:
                op, data = read_frame(ch.read)
            except (ConnectionError, OSError, ValueError):
                self._alive = False  # canale chiuso: la presenza resta, si riconnette al prossimo invio
                return
            if op == OP_PING:
                try:
                    ch.write(frame(OP_PONG, data))
                except OSError:
                    return
            elif op == OP_CLOSE:
                self._alive = False
                return
            elif op == OP_FRAME:
                if data.get("cmd") == "DISPATCH" and data.get("evt") == "READY":
                    self._ready.set()

    def close(self, quiet: bool = False):
        ch, self._ch = self._ch, None
        self._alive = False
        self._ready.clear()
        if ch is not None:
            try:
                ch.close()
            except OSError:
                if not quiet:
                    pass

    # ---------------------------------------------------------------- presenza
    def set(self, act: dict) -> bool:
        """Invia l'activity; se il canale è caduto prova a riconnettersi una volta."""
        if not self.client_id:
            self.error = "manca l'ID applicazione Discord"
            return False
        for attempt in (0, 1):
            if self.connect():
                try:
                    self._ch.write(frame(OP_FRAME, {"cmd": "SET_ACTIVITY",
                                                    "args": {"pid": self.pid, "activity": act},
                                                    "nonce": uuid.uuid4().hex}))
                    return True
                except OSError as e:
                    self.error = f"invio fallito: {e}"
                    self.close(quiet=True)
            if attempt:
                break
        return False

    def clear(self) -> bool:
        """Toglie la presenza («non sto giocando»)."""
        if not self.client_id:
            return False
        if self.connect():
            try:
                self._ch.write(frame(OP_FRAME, {"cmd": "SET_ACTIVITY",
                                                "args": {"pid": self.pid, "activity": None},
                                                "nonce": uuid.uuid4().hex}))
                return True
            except OSError:
                self.close(quiet=True)
        return False


class PresenceService:
    """Legge l'impostazione, tiene la presenza allineata al gioco in corso e la spegne alla chiusura."""

    def __init__(self, cfg, save=None, log=None, connect=None):
        self.cfg = cfg
        self.save = save
        self.log = log or (lambda m: None)
        self._connect = connect
        self.client: DiscordPresence | None = None
        self.current: dict | None = None
        self.on_air = False  # la presenza ? stata accettata da Discord
        self.conf()

    # ---------------------------------------------------------------- impostazioni
    def conf(self) -> dict:
        raw = (getattr(self.cfg, "app", None) or {}).get("discord") or {}
        conf = {"enabled": bool(raw.get("enabled")), "client_id": str(raw.get("client_id") or ""),
                "show_time": bool(raw.get("show_time", True))}
        self._ensure(conf)
        return conf

    def _ensure(self, conf: dict):
        if self.client is None or self.client.client_id != conf["client_id"]:
            if self.client is not None:
                self.client.close(quiet=True)
            self.client = DiscordPresence(conf["client_id"], log=self.log, connect=self._connect)
        if not conf["enabled"]:
            if self.current is not None:
                if self.client is not None and self.client.connected():
                    self.client.clear()
                self.current = None

    def apply(self, values: dict) -> dict:
        """Salva le impostazioni (enabled, client_id, show_time) e riallinea la presenza."""
        conf = self.conf()
        conf.update({k: values[k] for k in ("enabled", "client_id", "show_time") if k in values})
        conf["enabled"] = bool(conf["enabled"])
        conf["client_id"] = str(conf["client_id"] or "").strip()
        if self.save:
            self.save({"discord": conf})
        self._ensure(conf)
        if conf["enabled"] and conf["client_id"]:
            if self.current:
                self._push(self.current, conf)
            else:
                self.client.connect()  # verifica subito: l'interfaccia mostra se Discord risponde
        return self.state()

    def state(self) -> dict:
        conf = self.conf()
        return {"enabled": conf["enabled"], "client_id": conf["client_id"], "show_time": conf["show_time"],
                "connected": bool(self.client and self.client.connected()),
                "error": (self.client.error if self.client else None),
                "playing": (self.current or {}).get("title") if self.on_air else None}

    # ---------------------------------------------------------------- eventi di gioco
    def game_started(self, info: dict):
        conf = self.conf()
        if not conf["enabled"]:
            return
        self.current = dict(info or {})
        self.on_air = self._push(self.current, conf)

    def game_stopped(self, info: dict | None = None):
        self.current = None
        self.on_air = False
        if self.client is not None:
            self.client.clear()

    def _push(self, info: dict, conf: dict) -> bool:
        from . import platforms as P

        key = str(info.get("key") or "")
        pid = key.split("/", 1)[0]
        plat = getattr(P.PLATFORMS.get(pid), "name", "") or str(info.get("emulator") or "")
        detail = str(info.get("title") or "GameScraper")
        sub = f"{plat} · su GameScraper" if plat else "su GameScraper"
        started = info.get("started") if conf.get("show_time", True) else None
        return self.client.set(activity(detail, sub, started=started, large_text=detail, large_image="gamescraper",
                                        small_text="GameScraper", small_image="logo"))
