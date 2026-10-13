"""Schermate dei giochi: catturate dall'app durante il gioco o importate dagli emulatori e da Steam.

Tre modi per averle nell'app (tutte copiate in <cache>/screens, gli originali non vengono toccati):
  1. scorciatoia dell'app mentre si gioca: tastiera (predefinita Ctrl+Shift+S) o controller
     (Select/View + R1/RB): cattura la finestra del gioco, con qualsiasi emulatore;
  2. a fine sessione si importano le schermate fatte con il tasto dell'emulatore stesso, cercando
     nelle cartelle in cui le salva (RetroArch, Dolphin, PCSX2, DuckStation, PPSSPP, Cemu, Ryujinx, ...)
     e nella cartella di Xbox Game Bar: solo i file creati durante la sessione;
  3. schermate di Steam dei giochi Steam della libreria (userdata/<utente>/760/remote/<appid>).
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS screenshots(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, file TEXT, taken REAL,
                                       source TEXT, origin TEXT UNIQUE, w INT, h INT);
CREATE INDEX IF NOT EXISTS screenshots_key ON screenshots(key);
"""
DEFAULTS = {"hotkey": "ctrl+shift+s", "pad": True, "import_emulator": True}


def settings(cfg) -> dict:
    return {**DEFAULTS, **(cfg.app.get("screenshots") or {})}


class Shots:
    """Archivio delle schermate (file in <cache>/screens, elenco in userdata.db)."""

    def __init__(self, cache_folder, db_path):
        self.root = Path(cache_folder) / "screens"
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _dest(self, key: str, taken: float, ext: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:80]
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(taken))
        d = self.root / safe
        d.mkdir(parents=True, exist_ok=True)
        p, i = d / f"{stamp}{ext}", 1
        while p.exists():
            p, i = d / f"{stamp}-{i}{ext}", i + 1
        return p

    def _insert(self, key, path: Path, taken, source, origin, w=0, h=0) -> int | None:
        rel = path.relative_to(self.root).as_posix()
        with self.lock:
            try:
                cur = self.db.execute("INSERT INTO screenshots(key, file, taken, source, origin, w, h) VALUES(?,?,?,?,?,?,?)",
                                      (key, rel, taken, source, origin, w, h))
                self.db.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:  # gia' importata
                path.unlink(missing_ok=True)
                return None

    def add_image(self, key: str, img, source: str = "GameScraper") -> int | None:
        taken = time.time()
        p = self._dest(key, taken, ".png")
        img.save(p, "PNG")
        return self._insert(key, p, taken, source, f"capture:{p.name}:{taken}", img.width, img.height)

    def add_file(self, key: str, src: Path, source: str) -> int | None:
        origin = f"file:{os.path.normcase(str(src.resolve()))}:{int(src.stat().st_mtime)}"
        with self.lock:
            if self.db.execute("SELECT 1 FROM screenshots WHERE origin=?", (origin,)).fetchone():
                return None
        taken = src.stat().st_mtime
        p = self._dest(key, taken, src.suffix.lower())
        shutil.copy2(src, p)
        w = h = 0
        try:
            from PIL import Image
            with Image.open(p) as im:
                w, h = im.size
        except Exception:
            pass
        return self._insert(key, p, taken, source, origin, w, h)

    def list(self, keys: list[str] | None = None, limit: int = 5000) -> list[dict]:
        with self.lock:
            if keys is None:
                rows = self.db.execute("SELECT id, key, file, taken, source, w, h FROM screenshots ORDER BY taken DESC LIMIT ?",
                                       (limit,)).fetchall()
            else:
                if not keys:
                    return []
                q = ",".join("?" * len(keys))
                rows = self.db.execute(f"SELECT id, key, file, taken, source, w, h FROM screenshots WHERE key IN ({q}) "
                                       "ORDER BY taken DESC", keys).fetchall()
        return [{"id": r[0], "key": r[1], "file": r[2], "taken": r[3], "source": r[4], "w": r[5], "h": r[6]} for r in rows]

    def counts(self) -> dict[str, int]:
        with self.lock:
            return dict(self.db.execute("SELECT key, COUNT(*) FROM screenshots GROUP BY key").fetchall())

    def path(self, sid: int) -> Path | None:
        with self.lock:
            r = self.db.execute("SELECT file FROM screenshots WHERE id=?", (sid,)).fetchone()
        return self.root / r[0] if r else None

    def delete(self, sid: int):
        p = self.path(sid)
        with self.lock:
            self.db.execute("DELETE FROM screenshots WHERE id=?", (sid,))
            self.db.commit()
        if p:
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------- cattura della finestra del gioco
def capture_foreground():
    """Immagine della finestra in primo piano (Windows); altrove tutto lo schermo."""
    from PIL import ImageGrab
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
        hwnd = user32.GetForegroundWindow()
        rect = wintypes.RECT()
        # bordi reali della finestra (senza ombra), poi in alternativa GetWindowRect
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        ok = False
        try:
            ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
                                                            ctypes.sizeof(rect)) == 0
        except Exception:
            ok = False
        if not ok:
            ok = bool(user32.GetWindowRect(hwnd, ctypes.byref(rect)))
        if ok and rect.right - rect.left > 50 and rect.bottom - rect.top > 50:
            return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
        return ImageGrab.grab(all_screens=True)
    return ImageGrab.grab()


def beep():
    if sys.platform == "win32":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass


# ---------------------------------------------------------------- scorciatoia da tastiera (Windows)
_MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}


def parse_hotkey(s: str) -> tuple[int, int] | None:
    """'ctrl+shift+s' -> (modificatori, codice tasto virtuale)."""
    parts = [p.strip().lower() for p in (s or "").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            return None
        mods |= _MODS[p]
    k = parts[-1]
    if len(k) == 1 and k.isalnum():
        vk = ord(k.upper())
    elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", k):
        vk = 0x70 + int(k[1:]) - 1
    else:
        vk = {"printscreen": 0x2C, "print": 0x2C, "pause": 0x13, "insert": 0x2D, "home": 0x24, "end": 0x23}.get(k)
        if vk is None:
            return None
    return mods | 0x4000, vk  # 0x4000 = MOD_NOREPEAT


class Hotkey:
    """Scorciatoia globale (funziona anche con il gioco in primo piano). Solo Windows."""

    def __init__(self, combo: str, callback):
        self.combo, self.callback = combo, callback
        self.tid = None
        self.thread = None

    def start(self) -> bool:
        if sys.platform != "win32" or not parse_hotkey(self.combo):
            return False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True

    def _loop(self):
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        mods, vk = parse_hotkey(self.combo)
        self.tid = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, mods, vk):
            return  # scorciatoia gia' usata da un altro programma
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:  # WM_HOTKEY
                    try:
                        self.callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self.tid and sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)  # WM_QUIT


# ---------------------------------------------------------------- cartelle delle schermate degli emulatori
def _home(*parts) -> Path:
    return Path.home().joinpath(*parts)


def _env(var, *parts) -> Path | None:
    v = os.environ.get(var)
    return Path(v).joinpath(*parts) if v else None


def emulator_dirs(emulator: str, exe: str | None) -> list[Path]:
    """Cartelle in cui l'emulatore salva le schermate (versione portatile e installata)."""
    base = Path(exe).parent if exe else None
    docs = _home("Documents")
    c: list[Path | None] = []
    e = (emulator or "").lower()
    if e == "retroarch" and base:
        c += [base / "screenshots"]
        cfgf = base / "retroarch.cfg"
        if cfgf.exists():
            m = re.search(r'^screenshot_directory\s*=\s*"([^"]*)"', cfgf.read_text(encoding="utf-8", errors="ignore"), re.M)
            if m and m.group(1) and m.group(1) != "default":
                d = m.group(1).replace(":\\", str(base) + "\\", 1) if m.group(1).startswith(":") else m.group(1)
                c.append(Path(d))
    elif e == "dolphin":
        c += [base / "User" / "ScreenShots" if base else None, docs / "Dolphin Emulator" / "ScreenShots",
              _env("APPDATA", "Dolphin Emulator", "ScreenShots"), _home(".local", "share", "dolphin-emu", "ScreenShots")]
    elif e == "pcsx2":
        c += [base / "snaps" if base else None, docs / "PCSX2" / "snaps", _home(".config", "PCSX2", "snaps")]
    elif e == "duckstation":
        c += [base / "screenshots" if base else None, docs / "DuckStation" / "screenshots",
              _home(".local", "share", "duckstation", "screenshots")]
    elif e == "ppsspp":
        c += [base / "memstick" / "PSP" / "SCREENSHOT" if base else None, docs / "PPSSPP" / "PSP" / "SCREENSHOT"]
    elif e == "ryujinx":
        c += [_home("Pictures", "Ryujinx"), base / "portable" / "screenshots" if base else None]
    elif e in ("azahar", "lime3ds", "citra"):
        name = {"azahar": "Azahar", "lime3ds": "Lime3DS", "citra": "Citra"}[e]
        c += [base / "user" / "screenshots" if base else None, _env("APPDATA", name, "screenshots")]
    elif e in ("cemu", "rpcs3", "xenia", "vita3k", "shadps4", "flycast", "xemu", "melonds"):
        c += [base / "screenshots" if base else None, base / "ScreenShots" if base else None]
    if base:  # generiche, per gli emulatori non previsti
        c += [base / "screenshots", base / "Screenshots", base / "snaps"]
    c += [_home("Videos", "Captures")]  # Xbox Game Bar (Win+Alt+Stamp)
    seen, out = set(), []
    for d in c:
        if d and d.is_dir() and os.path.normcase(str(d)) not in seen:
            seen.add(os.path.normcase(str(d)))
            out.append(d)
    return out


def import_session(shots: Shots, key: str, emulator: str, exe: str | None, start: float, end: float) -> int:
    """Copia le schermate create durante la sessione nelle cartelle dell'emulatore."""
    n = 0
    for d in emulator_dirs(emulator, exe):
        try:
            files = [f for f in d.rglob("*") if f.suffix.lower() in IMG_EXT and f.is_file()]
        except OSError:
            continue
        for f in files:
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if start - 5 <= mt <= end + 90:
                src = "Xbox Game Bar" if "Captures" in f.parts else emulator
                if shots.add_file(key, f, src):
                    n += 1
    return n


def steam_dirs(steam_root: Path) -> dict[str, list[Path]]:
    """{appid: [cartelle schermate]} dalle cartelle utente di Steam."""
    out: dict[str, list[Path]] = {}
    ud = steam_root / "userdata"
    if not ud.is_dir():
        return out
    for user in ud.iterdir():
        remote = user / "760" / "remote"
        if not remote.is_dir():
            continue
        for app in remote.iterdir():
            d = app / "screenshots"
            if d.is_dir():
                out.setdefault(app.name, []).append(d)
    return out


def import_steam(shots: Shots, steam_root: Path, keys_by_appid: dict[str, str]) -> int:
    n = 0
    for appid, dirs in steam_dirs(steam_root).items():
        key = keys_by_appid.get(appid)
        if not key:
            continue
        for d in dirs:
            for f in d.iterdir():  # niente sottocartella "thumbnails"
                if f.is_file() and f.suffix.lower() in IMG_EXT:
                    if shots.add_file(key, f, "Steam"):
                        n += 1
    return n

