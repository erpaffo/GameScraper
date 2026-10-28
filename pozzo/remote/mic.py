"""Microfono del telefono -> uscita audio del PC (solo Windows, waveOut di winmm).

Per SingStar in PCSX2 il suono va mandato in un cavo audio virtuale (es. VB-Cable: si suona su «CABLE Input» e
PCSX2 registra da «CABLE Output»). Il telefono manda PCM 16 bit mono a 16 kHz in pacchetti brevi; se la coda
supera MAX_QUEUE_MS (rete lenta) i pacchetti si scartano: meglio un buco che un ritardo che cresce.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

RATE = 16000
MAX_QUEUE_MS = 250
# cavi virtuali conosciuti; «Steam Streaming Microphone» (installato da Steam) e' da provare come alternativa a VB-Cable
PREFERRED = ("CABLE Input", "VB-Audio", "Steam Streaming M")


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


class WAVEHDR(ctypes.Structure):
    pass


WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD), ("dwBytesRecorded", wintypes.DWORD),
                    ("dwUser", ctypes.c_size_t), ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                    ("lpNext", ctypes.POINTER(WAVEHDR)), ("reserved", ctypes.c_size_t)]


class WAVEOUTCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                ("szPname", wintypes.WCHAR * 32), ("dwFormats", wintypes.DWORD), ("wChannels", wintypes.WORD),
                ("wReserved1", wintypes.WORD), ("dwSupport", wintypes.DWORD)]


WHDR_DONE = 1
WAVE_MAPPER = 0xFFFFFFFF


def devices() -> list[str]:
    """Uscite audio del PC (i nomi di Windows sono tagliati a 31 caratteri)."""
    if sys.platform != "win32":
        return []
    w = ctypes.windll.winmm
    out = []
    for i in range(w.waveOutGetNumDevs()):
        caps = WAVEOUTCAPSW()
        if w.waveOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append(caps.szPname)
    return out


def pick(names: list[str], wanted: str = "") -> int | None:
    """Indice dell'uscita: quella scelta, altrimenti un cavo virtuale conosciuto; None se non c'e'."""
    for key in ([wanted] if wanted else []) + list(PREFERRED):
        for i, n in enumerate(names):
            if key.lower() in n.lower():
                return i
    return None


class Player:
    """Riproduce PCM 16 bit mono su un'uscita audio con una coda breve."""

    def __init__(self, device: int | None):
        self.w = ctypes.windll.winmm
        self.h = wintypes.HANDLE()
        fmt = WAVEFORMATEX(1, 1, RATE, RATE * 2, 2, 16, 0)
        dev = WAVE_MAPPER if device is None else device
        err = self.w.waveOutOpen(ctypes.byref(self.h), wintypes.UINT(dev), ctypes.byref(fmt), 0, 0, 0)
        if err:
            raise OSError(f"Uscita audio non apribile (codice {err})")
        self.pending: list[tuple[WAVEHDR, ctypes.Array]] = []
        self.lock = threading.Lock()

    def queued_ms(self) -> int:
        return sum(h.dwBufferLength for h, _ in self.pending) * 1000 // (RATE * 2)

    def _reap(self):
        keep = []
        for h, buf in self.pending:
            if h.dwFlags & WHDR_DONE:
                self.w.waveOutUnprepareHeader(self.h, ctypes.byref(h), ctypes.sizeof(h))
            else:
                keep.append((h, buf))
        self.pending = keep

    def play(self, pcm: bytes) -> bool:
        """False se il pacchetto e' stato scartato (coda troppo lunga)."""
        if not pcm or len(pcm) % 2:
            return False
        with self.lock:
            self._reap()
            if self.queued_ms() > MAX_QUEUE_MS:
                return False
            buf = ctypes.create_string_buffer(pcm, len(pcm))
            h = WAVEHDR(ctypes.cast(buf, ctypes.c_void_p), len(pcm), 0, 0, 0, 0, None, 0)
            self.w.waveOutPrepareHeader(self.h, ctypes.byref(h), ctypes.sizeof(h))
            self.w.waveOutWrite(self.h, ctypes.byref(h), ctypes.sizeof(h))
            self.pending.append((h, buf))  # i buffer restano vivi finche' il driver li usa
            return True

    def close(self):
        with self.lock:
            self.w.waveOutReset(self.h)
            self._reap()
            self.w.waveOutClose(self.h)
            self.pending = []
