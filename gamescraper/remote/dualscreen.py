"""Telefono come schermo touch per i giochi a due schermi (DS con melonDS, 3DS con Citra/Azahar/Lime3DS), solo Windows.

Con il telefono collegato al controller, prima dell'avvio si configura l'emulatore a finestre separate (lo leggono solo
all'avvio): lo schermo superiore a schermo intero sul PC, quello inferiore in una seconda finestra tenuta dietro, che
viene catturata e mandata al telefono; i tocchi tornano come clic inviati solo a quella finestra (il mouse del PC non si
muove). A fine partita la configurazione dell'emulatore torna com'era. Senza telefono non si tocca nulla.

melonDS 1.x (melonDS.toml):  [Instance0.Window0] ScreenSizing = 4 (TopOnly), [Instance0.Window1] Enabled + ScreenSizing = 5
Citra e derivati (qt-config.ini, QSettings): [Layout] layout_option=4 (SeparateWindows)
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import os
import re
import sys
from pathlib import Path

from .emusetup import backup, ini_get, ini_update

PLATFORMS = {"nds": "melon", "3ds": "citra"}
FAMILY = {"melonds": "melon", "citra": "citra", "azahar": "citra", "lime3ds": "citra"}
APPDATA_NAME = {"citra": "Citra", "azahar": "Azahar", "lime3ds": "Lime3DS"}
KEYS = {
    "melon": {"Instance0.Window0": {"ScreenSizing": "4"}, "Instance0.Window1": {"Enabled": "true", "ScreenSizing": "5"}},
    "citra": {"Layout": {"layout_option": "4", "layout_option\\default": "false"}},
}
SEP = {"melon": " = ", "citra": "="}
FULLSCREEN_ARG = "-f"  # melonDS e citra-qt: schermo intero all'avvio
MAX_SIDE = 640  # lato lungo dei fotogrammi mandati al telefono


def family(emulator_id: str) -> str | None:
    """Famiglia dell'emulatore («melon» o «citra») dal nome configurato, senza badare a maiuscole e spazi."""
    return FAMILY.get(str(emulator_id or "").strip().lower())


def supported() -> bool:
    return sys.platform == "win32"


def wanted(platform_id: str, emulator_id: str, enabled: bool, phone_connected: bool) -> bool:
    """Usare il telefono come schermo touch per questa partita?"""
    fam = family(emulator_id)
    return bool(supported() and enabled and phone_connected and fam and PLATFORMS.get(platform_id) == fam)


def config_file(emulator_id: str, exe: str) -> Path:
    eid = str(emulator_id or "").strip().lower()
    fam = family(eid)
    if fam is None:
        raise ValueError(f"emulatore non gestito per il doppio schermo: {emulator_id!r}")
    d = Path(exe).parent
    if fam == "melon":  # portatile se il file e' accanto all'exe (come quello dell'utente)
        return d / "melonDS.toml" if (d / "melonDS.toml").exists() else Path(os.environ.get("APPDATA", "")) / "melonDS" / "melonDS.toml"
    if (d / "user").is_dir():
        return d / "user" / "config" / "qt-config.ini"
    return Path(os.environ.get("APPDATA", "")) / APPDATA_NAME.get(eid, "Citra") / "config" / "qt-config.ini"


def _drop(text: str, section: str, key: str) -> str:
    """Toglie una chiave da una sezione (per rimettere com'era una chiave che prima non c'era)."""
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"\s*\[([^\]]+)\]\s*$", line)
        if m:
            cur = m.group(1)
        elif cur == section and "=" in line and line.split("=", 1)[0].strip() == key:
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def apply(emulator_id: str, exe: str) -> dict:
    """Scrive la configurazione a finestre separate; ritorna lo stato per restore() (valori originali)."""
    eid = str(emulator_id or "").strip().lower()
    fam = family(eid)
    if fam is None:
        raise ValueError(f"emulatore non gestito per il doppio schermo: {emulator_id!r}")
    p = config_file(eid, exe)
    text = p.read_text(encoding="utf-8-sig", errors="replace") if p.exists() else ""
    orig = {s: {k: ini_get(text, s, k) for k in kv} for s, kv in KEYS[fam].items()}
    backup(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ini_update(text, KEYS[fam], sep=SEP[fam]), encoding="utf-8")
    return {"emulator": eid, "file": str(p), "family": fam, "orig": orig}


def restore(state: dict) -> None:
    """Rimette i valori originali (chi non c'era viene tolto); il resto del file, salvato dall'emulatore, resta."""
    p = Path(state["file"])
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    keep = {s: {k: v for k, v in kv.items() if v is not None} for s, kv in state["orig"].items()}
    text = ini_update(text, {s: kv for s, kv in keep.items() if kv}, sep=SEP[state["family"]])
    for s, kv in state["orig"].items():
        for k, v in kv.items():
            if v is None:
                text = _drop(text, s, k)
    p.write_text(text, encoding="utf-8")


def client_point(x: float, y: float, w: int, h: int) -> tuple[int, int]:
    """Tocco normalizzato (0..1) sull'immagine del telefono -> punto nell'area client della finestra."""
    return min(w - 1, max(0, round(x * w))), min(h - 1, max(0, round(y * h)))


# ---------------------------------------------------------------- finestre dell'emulatore (Win32)

WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0200, 0x0201, 0x0202, 0x0001
HWND_TOPMOST, HWND_NOTOPMOST, HWND_BOTTOM = -1, -2, 1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


def _user32():
    """user32 con i tipi dichiarati: su Windows a 64 bit HWND_TOPMOST (-1) e gli HWND vanno passati come puntatori."""
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wintypes.UINT]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
    u.ChildWindowFromPointEx.restype = wintypes.HWND
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HANDLE
    u.GetDC.argtypes = [wintypes.HWND]
    u.GetDC.restype = wintypes.HDC
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    for f in ("GetClientRect", "GetWindowRect"):
        getattr(u, f).argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    # gli handle non devono essere troncati a 32 bit (touch e primo piano fallirebbero a intermittenza)
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.MapWindowPoints.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(wintypes.POINT), wintypes.UINT]
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    return u


def _gdi32():
    from ctypes import wintypes
    g = ctypes.windll.gdi32
    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]
    return g


def _windows(pids: set[int]) -> list[tuple[int, tuple[int, int, int, int]]]:
    """Finestre visibili e abbastanza grandi dei processi indicati: [(hwnd, (sinistra, alto, destra, basso))]."""
    from ctypes import wintypes
    u = _user32()
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(h):
            r = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 120 and r.bottom - r.top > 90:
                out.append((h, (r.left, r.top, r.right, r.bottom)))
        return True
    u.EnumWindows(cb, 0)
    return out


def screen_rect(w: int, h: int, bar: int) -> tuple[int, int, int, int]:
    """Dove l'emulatore disegna lo schermo (4:3, sia DS sia 3DS) nell'area client w x h con `bar` px di menu in alto:
    centrato e il piu' grande possibile nello spazio sotto il menu."""
    ph = max(1, h - bar)
    sw, sh = (w, w * 3 // 4) if w * 3 <= ph * 4 else (ph * 4 // 3, ph)
    x, y = (w - sw) // 2, bar + (ph - sh) // 2
    return x, y, x + sw, y + sh


class Session:
    """Lo schermo inferiore di una partita: trovato, catturato, toccato, nascosto o in riquadro."""

    def __init__(self, pid: int):
        self.pid, self.hwnd, self.main, self.bar, self.down, self.last = pid, None, None, 0, False, None
        if supported():
            try:
                ctypes.windll.user32.SetProcessDPIAware()  # coordinate in pixel veri anche con lo zoom di Windows
            except Exception:
                pass

    def find(self) -> bool:
        """Le due finestre dell'emulatore: quella piu' piccola e' lo schermo inferiore (l'altra e' a schermo intero)."""
        try:
            import psutil
            pids = {self.pid, *(c.pid for c in psutil.Process(self.pid).children(recursive=True))}
        except Exception:
            pids = {self.pid}
        wins = _windows(pids)
        if len(wins) < 2:
            return False
        wins.sort(key=lambda w: (w[1][2] - w[1][0]) * (w[1][3] - w[1][1]))
        self.hwnd, self.main = wins[0][0], wins[-1][0]
        self._measure()
        return True

    def _measure(self):
        """Altezza della barra dei menu (melonDS; Citra non ne ha): l'emulatore dimensiona la finestra sullo schermo,
        quindi l'area client in piu' rispetto al 4:3 e' la barra. Si misura finche' non la si trova, prima di ridimensionare."""
        if not self.bar:
            w, h = self._client()
            extra = h - w * 3 // 4
            self.bar = extra if 0 < extra <= 60 else 0

    def _client(self) -> tuple[int, int]:
        from ctypes import wintypes
        r = wintypes.RECT()
        _user32().GetClientRect(self.hwnd, ctypes.byref(r))
        return r.right, r.bottom

    def _area(self) -> tuple[int, int, int, int]:
        return screen_rect(*self._client(), self.bar)

    def frame(self) -> bytes | None:
        """JPEG dello schermo inferiore (anche se la finestra e' coperta); None se uguale al precedente."""
        from PIL import Image
        u, g = _user32(), _gdi32()
        w, h = self._client()
        if w <= 0 or h <= 0:
            return None
        hdc = u.GetDC(self.hwnd)
        mem = g.CreateCompatibleDC(hdc)
        bmp = g.CreateCompatibleBitmap(hdc, w, h)
        old = g.SelectObject(mem, bmp)
        try:
            u.PrintWindow(self.hwnd, mem, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
            bi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # altezza negativa = dall'alto in basso
            buf = ctypes.create_string_buffer(w * h * 4)
            g.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        finally:
            g.SelectObject(mem, old)
            g.DeleteObject(bmp)
            g.DeleteDC(mem)
            u.ReleaseDC(self.hwnd, hdc)
        digest = hashlib.blake2b(buf.raw, digest_size=16).digest()
        if digest == self.last:
            return None
        self.last = digest
        img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB").crop(self._area())
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80)
        return out.getvalue()

    def touch(self, x: float, y: float, down: bool):
        """Pennino: clic alla sola finestra dell'emulatore (alla finestra figlia sotto il punto, se c'e')."""
        from ctypes import wintypes
        u = _user32()
        left, top, right, bottom = self._area()
        cx, cy = client_point(x, y, right - left, bottom - top)
        cx, cy = cx + left, cy + top  # dall'immagine (solo lo schermo) all'area client della finestra
        target = u.ChildWindowFromPointEx(self.hwnd, wintypes.POINT(cx, cy), 0x3) or self.hwnd  # salta invisibili/disattivate
        pt = wintypes.POINT(cx, cy)
        if target != self.hwnd:
            u.MapWindowPoints(self.hwnd, target, ctypes.byref(pt), 1)
        lp = (pt.y & 0xFFFF) << 16 | (pt.x & 0xFFFF)
        if down and not self.down:
            u.PostMessageW(target, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        elif down:
            u.PostMessageW(target, WM_MOUSEMOVE, MK_LBUTTON, lp)
        elif self.down:
            u.PostMessageW(target, WM_LBUTTONUP, 0, lp)
        self.down = down

    def _place(self, after: int, x: int | None, y: int | None, width: int, flags: int):
        """Finestra con l'area client larga `width` e alta quanto lo schermo 4:3 (+ la barra dei menu)."""
        from ctypes import wintypes
        u = _user32()
        wr, (cw, ch) = wintypes.RECT(), self._client()
        u.GetWindowRect(self.hwnd, ctypes.byref(wr))
        bw, bh = (wr.right - wr.left) - cw, (wr.bottom - wr.top) - ch  # bordi e barra del titolo
        if x is None:
            flags |= SWP_NOMOVE
        u.SetWindowPos(self.hwnd, after, x or 0, y or 0, width + bw, width * 3 // 4 + self.bar + bh, flags)

    def hide(self):
        """Dietro lo schermo intero: sul PC si vede solo lo schermo superiore. Larga 640 px: immagine nitida sul telefono."""
        u = _user32()
        self._measure()
        self._place(HWND_NOTOPMOST, None, None, MAX_SIDE, SWP_NOACTIVATE)
        u.SetWindowPos(self.hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        if self.main:
            u.SetForegroundWindow(self.main)  # lo schermo superiore davanti a tutto, con i tasti

    def pip(self):
        """Telefono scollegato: lo schermo inferiore in un riquadro in basso a destra, sopra il gioco, usabile col mouse."""
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]
        u = _user32()
        mi = MONITORINFO(ctypes.sizeof(MONITORINFO))
        u.GetMonitorInfoW(u.MonitorFromWindow(self.main or self.hwnd, 2), ctypes.byref(mi))
        r = mi.rcMonitor
        w = (r.right - r.left) * 3 // 10
        h = w * 3 // 4 + self.bar + 60  # per la posizione; la dimensione esatta la calcola _place
        self._place(HWND_TOPMOST, r.right - w - 40, r.bottom - h - 40, w, SWP_NOACTIVATE | SWP_SHOWWINDOW)
