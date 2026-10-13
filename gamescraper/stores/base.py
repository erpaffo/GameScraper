"""Base comune dei "fornitori di libreria" (store PC e giochi aggiunti a mano).

Ogni store produce una lista di StoreGame: giochi installati E posseduti-ma-non-installati,
ciascuno con le istruzioni per avviarlo (URI del launcher o eseguibile) e per installarlo
(URI che apre il client dello store direttamente sulla pagina del gioco).
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class StoreGame:
    store: str
    id: str
    title: str
    installed: bool = False
    install_dir: str = ""
    # avvio: {"type": "uri", "target": "steam://rungameid/1"} oppure {"type": "exe", "target": path, "args": [], "cwd": ""}
    launch: dict = field(default_factory=dict)
    install_uri: str = ""  # apre il client dello store sulla pagina d'installazione
    page: str = ""  # pagina web dello store
    playtime: float = 0  # secondi, se lo store lo fornisce
    last_played: float = 0
    images: dict = field(default_factory=dict)  # cover/hero/logo/icon/header -> url
    genres: list = field(default_factory=list)
    description: str = ""
    developer: str = ""
    publisher: str = ""
    release_date: str = ""
    reviews: dict = field(default_factory=dict)
    size: int = 0  # byte (se lo store lo indica)  # {"rating": 0..1, "rating_count": n, "label": ...}

    def to_dict(self) -> dict:
        return asdict(self)


class StoreProvider:
    id = "base"
    name = "Store"
    login: str | None = None  # None | "oauth" | "apikey"
    owned_note = ""  # spiegazione se lo store non permette di vedere i giochi non installati

    def __init__(self, cfg, cache, client, log):
        self.cfg, self.cache, self.client, self.log = cfg, cache, client, log

    @property
    def settings(self) -> dict:
        return (getattr(self.cfg, "stores", {}) or {}).get(self.id, {})

    def detected(self) -> bool:
        """Il client dello store e' installato o l'account e' collegato."""
        return False

    def account(self) -> dict:
        """Stato dell'account per la pagina Impostazioni › Account."""
        return {}

    async def fetch(self) -> list[StoreGame]:
        raise NotImplementedError


# ---------------------------------------------------------------- utilita' Windows

def is_windows() -> bool:
    return sys.platform == "win32"


def reg_get(root: str, path: str, name: str | None = None):
    """Legge un valore (o tutti i valori se name=None) dal registro di Windows."""
    if not is_windows():
        return None
    import winreg
    hive = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[root]
    try:
        with winreg.OpenKey(hive, path) as k:
            if name is not None:
                return winreg.QueryValueEx(k, name)[0]
            out, i = {}, 0
            while True:
                try:
                    n, v, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[n] = v
                i += 1
            return out
    except OSError:
        return None


def reg_subkeys(root: str, path: str) -> list[str]:
    if not is_windows():
        return []
    import winreg
    hive = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[root]
    out = []
    try:
        with winreg.OpenKey(hive, path) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return out


def dpapi_decrypt(data: bytes) -> bytes | None:
    """Decifra dati protetti con DPAPI dell'utente corrente (es. database di Amazon Games)."""
    if not is_windows():
        return None
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def powershell_json(script: str, timeout: int = 40):
    """Esegue PowerShell e interpreta l'output (ConvertTo-Json)."""
    if not is_windows():
        return None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script],
                           capture_output=True, timeout=timeout, creationflags=0x08000000)
        txt = r.stdout.decode("utf-8", errors="replace").strip()
        if not txt:
            return []
        data = json.loads(txt)
        return data if isinstance(data, list) else [data]
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


# ---------------------------------------------------------------- VDF (formato testo di Steam)

_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    root: dict = {}
    stack = [root]
    key = None
    for m in _VDF_TOKEN.finditer(text):
        s, brace = m.group(1), m.group(2)
        if brace == "{":
            d: dict = {}
            stack[-1][key] = d
            stack.append(d)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = s.replace("\\\\", "\\")
        else:
            stack[-1][key] = s.replace("\\\\", "\\")
            key = None
    return root


def first_existing(*paths) -> Path | None:
    for p in paths:
        if p and Path(os.path.expandvars(str(p))).exists():
            return Path(os.path.expandvars(str(p)))
    return None
