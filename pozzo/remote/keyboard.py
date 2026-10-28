"""Tasti e tasti multimedia del PC premuti dal telefono (volume, play/pausa, Esc, Invio, Alt+Tab).

Solo Windows. Si manda un tocco (premi e rilascia), non un tasto tenuto: basta per i menu dei giochi
e per il telecomando. L'elenco e' chiuso di proposito: il telefono non puo' digitare testo arbitrario.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# nome usato dal telefono -> tasti da premere in ordine (Virtual-Key code); il rilascio va al contrario
KEYS: dict[str, tuple[int, ...]] = {
    "vol_up": (0xAF,), "vol_down": (0xAE,), "mute": (0xAD,),
    "play": (0xB3,), "next": (0xB0,), "prev": (0xB1,), "stop_media": (0xB2,),
    "esc": (0x1B,), "enter": (0x0D,), "space": (0x20,), "backspace": (0x08,), "tab": (0x09,),
    "shift_tab": (0x10, 0x09), "alt_tab": (0x12, 0x09), "alt_f4": (0x12, 0x73),
    "win": (0x5B,), "f11": (0x7A,),
    "left": (0x25,), "right": (0x27,), "up": (0x26,), "down": (0x28,),
}
# tasti "estesi" (E0): multimedia, frecce, Windows. Senza il flag alcuni programmi li ignorano
_EXTENDED = {0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3, 0x5B, 0x25, 0x26, 0x27, 0x28}
# shortcut: un tocco = premi e rilascia, niente tasti tenuti; se un gioco chiede di tenere premuto, aggiungere down/up

_KEYUP, _EXTENDEDKEY = 0x0002, 0x0001


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # solo per la dimensione: l'unione di INPUT deve essere quella piu' grande
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def press(name: str):
    """Preme e rilascia il tasto (o la combinazione) `name`; ValueError se non e' nell'elenco."""
    combo = KEYS.get(str(name or ""))
    if not combo:
        raise ValueError("Tasto non previsto")
    if sys.platform != "win32":
        return
    events = [(vk, _EXTENDEDKEY if vk in _EXTENDED else 0) for vk in combo]
    events += [(vk, flags | _KEYUP) for vk, flags in reversed(events)]
    arr = (INPUT * len(events))(*[INPUT(type=1, ki=KEYBDINPUT(vk, 0, flags, 0, 0)) for vk, flags in events])
    ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
