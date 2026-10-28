"""Mouse del PC mosso dal telefono (touchpad / pistola ottica), solo Windows.

Posizione assoluta sullo schermo principale (0..1), tasto sinistro tenuto finche' il dito tocca: serve alle
pistole ottiche degli emulatori (GunCon in DuckStation/PCSX2, Zapper in Dolphin), al pennino di DS/3DS e ai menu.
Da' il controllo del mouse a chi ha il telefono: si attiva solo dal PC (Impostazioni › Telefono).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

MOVE, LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP, ABSOLUTE = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x8000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]  # MOUSEINPUT e' il membro piu' grande: la dimensione di INPUT resta giusta
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def to_abs(v: float) -> int:
    """0..1 -> coordinate assolute di SendInput (0..65535)."""
    return round(max(0.0, min(1.0, float(v))) * 65535)


def events(left: bool, right: bool, prev: tuple[bool, bool]) -> list[int]:
    """Flag degli eventi da mandare: movimento + i tasti che cambiano rispetto a prima."""
    out = [MOVE | ABSOLUTE]
    if left != prev[0]:
        out.append(LEFTDOWN if left else LEFTUP)
    if right != prev[1]:
        out.append(RIGHTDOWN if right else RIGHTUP)
    return out


class Mouse:
    def __init__(self):
        self.prev = (False, False)

    def send(self, x: float, y: float, left: bool = False, right: bool = False):
        if sys.platform != "win32":
            return
        flags = events(left, right, self.prev)
        self.prev = (left, right)
        _send([MOUSEINPUT(to_abs(x), to_abs(y), 0, f | ABSOLUTE, 0, 0) for f in flags])

    def release(self):
        """Rilascia i tasti tenuti (telefono scollegato) senza spostare il puntatore."""
        flags = [LEFTUP] * self.prev[0] + [RIGHTUP] * self.prev[1]
        self.prev = (False, False)
        if flags and sys.platform == "win32":
            _send([MOUSEINPUT(0, 0, 0, f, 0, 0) for f in flags])


def _send(inputs: list[MOUSEINPUT]):
    arr = (INPUT * len(inputs))(*[INPUT(type=0, mi=m) for m in inputs])
    ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def screen_size() -> tuple[int, int]:
    if sys.platform != "win32":
        return 1920, 1080
    u = ctypes.windll.user32
    return int(u.GetSystemMetrics(0)) or 1920, int(u.GetSystemMetrics(1)) or 1080
