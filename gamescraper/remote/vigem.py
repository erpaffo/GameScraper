"""Controller Xbox 360 virtuali (driver ViGEmBus, solo Windows): il telefono diventa un controller vero
per Windows, per gli emulatori e per i giochi PC.

Il driver ViGEmBus deve essere installato (https://github.com/nefarius/ViGEmBus/releases); la libreria
client (ViGEmClient.dll, licenza MIT) e' inclusa in vendor/.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import c_short, c_uint, c_ushort, c_void_p
from pathlib import Path

# pulsanti XInput
BUTTONS = {
    "up": 0x0001, "down": 0x0002, "left": 0x0004, "right": 0x0008, "start": 0x0010, "back": 0x0020,
    "ls": 0x0040, "rs": 0x0080, "lb": 0x0100, "rb": 0x0200, "guide": 0x0400,
    "a": 0x1000, "b": 0x2000, "x": 0x4000, "y": 0x8000,
}


class XUSB_REPORT(ctypes.Structure):
    _fields_ = [("wButtons", c_ushort), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", c_short), ("sThumbLY", c_short), ("sThumbRX", c_short), ("sThumbRY", c_short)]


class ViGEmError(RuntimeError):
    pass


_lib = None
_client = None
_lock = threading.Lock()


def _dll_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", "")) / "gamescraper" / "remote" / "vendor" if getattr(sys, "frozen", False) \
        else Path(__file__).parent / "vendor"
    return base / "ViGEmClient.dll"


def _load():
    global _lib
    if _lib is not None:
        return _lib
    if sys.platform != "win32":
        raise ViGEmError("I controller virtuali sono disponibili solo su Windows")
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise ViGEmError("Serve Python/GameScraper a 64 bit")
    try:
        lib = ctypes.CDLL(str(_dll_path()))
    except OSError as e:
        raise ViGEmError(f"Libreria ViGEmClient non caricabile: {e}") from e
    lib.vigem_alloc.restype = c_void_p
    lib.vigem_free.argtypes = (c_void_p,)
    lib.vigem_connect.argtypes = (c_void_p,)
    lib.vigem_connect.restype = c_uint
    lib.vigem_disconnect.argtypes = (c_void_p,)
    lib.vigem_target_x360_alloc.restype = c_void_p
    lib.vigem_target_free.argtypes = (c_void_p,)
    lib.vigem_target_add.argtypes = (c_void_p, c_void_p)
    lib.vigem_target_add.restype = c_uint
    lib.vigem_target_remove.argtypes = (c_void_p, c_void_p)
    lib.vigem_target_remove.restype = c_uint
    lib.vigem_target_x360_update.argtypes = (c_void_p, c_void_p, XUSB_REPORT)
    lib.vigem_target_x360_update.restype = c_uint
    _lib = lib
    return lib


VIGEM_ERROR_NONE = 0x20000000
VIGEM_ERROR_BUS_NOT_FOUND = 0xE0000001


def _get_client():
    global _client
    with _lock:
        if _client is None:
            lib = _load()
            cl = lib.vigem_alloc()
            if not cl:
                raise ViGEmError("Memoria esaurita (vigem_alloc)")
            err = lib.vigem_connect(cl)
            if err != VIGEM_ERROR_NONE:
                lib.vigem_free(cl)
                if err == VIGEM_ERROR_BUS_NOT_FOUND:
                    raise ViGEmError("Driver ViGEmBus non installato: https://github.com/nefarius/ViGEmBus/releases")
                raise ViGEmError(f"Connessione a ViGEmBus fallita (codice {err:#x})")
            _client = cl
        return _client


def available() -> tuple[bool, str]:
    try:
        _get_client()
        return True, ""
    except ViGEmError as e:
        return False, str(e)


class VirtualPad:
    """Un controller Xbox 360 virtuale; si scollega con close()."""

    def __init__(self):
        self.lib = _load()
        self.client = _get_client()
        self.target = self.lib.vigem_target_x360_alloc()
        err = self.lib.vigem_target_add(self.client, self.target)
        if err != VIGEM_ERROR_NONE:
            self.lib.vigem_target_free(self.target)
            self.target = None
            raise ViGEmError(f"Impossibile creare il controller virtuale (codice {err:#x})")
        self.report = XUSB_REPORT()

    @staticmethod
    def _axis(v: float) -> int:
        return max(-32768, min(32767, int(round(v * 32767))))

    def update(self, buttons: int = 0, lt: float = 0, rt: float = 0, lx: float = 0, ly: float = 0, rx: float = 0, ry: float = 0):
        """Assi -1..1 (Y positivo = su), grilletti 0..1."""
        if not self.target:
            return
        r = self.report
        r.wButtons = buttons & 0xFFFF
        r.bLeftTrigger = max(0, min(255, int(lt * 255)))
        r.bRightTrigger = max(0, min(255, int(rt * 255)))
        r.sThumbLX, r.sThumbLY = self._axis(lx), self._axis(ly)
        r.sThumbRX, r.sThumbRY = self._axis(rx), self._axis(ry)
        self.lib.vigem_target_x360_update(self.client, self.target, r)

    def close(self):
        if self.target:
            try:
                self.update()  # rilascia tutto prima di scollegare
                self.lib.vigem_target_remove(self.client, self.target)
            finally:
                self.lib.vigem_target_free(self.target)
                self.target = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

