"""Avvio dei giochi con gli emulatori e registrazione del tempo di gioco.

Configurazione (config.toml):

    [emulators]
    retroarch = "C:/Emulatori/RetroArch/retroarch.exe"
    dolphin   = "C:/Emulatori/Dolphin/Dolphin.exe"
    ...

    [launch.psx]                 # opzionale: sovrascrive il preset di una console
    emulator = "retroarch"
    core = "swanstation"         # solo per RetroArch
    args = ["-L", "{core}", "{rom}"]

Per ogni console si usa il primo emulatore dei preset che risulta configurato.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from .. import platforms as P

RA = "retroarch"
# piattaforma -> candidati (emulatore, core RetroArch) in ordine di preferenza
PRESETS: dict[str, list[tuple[str, str | None]]] = {
    "nes": [(RA, "mesen"), (RA, "nestopia"), (RA, "fceumm")],
    "fds": [(RA, "mesen"), (RA, "fceumm")],
    "snes": [(RA, "snes9x"), (RA, "bsnes")],
    "n64": [(RA, "mupen64plus_next")],
    "gb": [(RA, "gambatte"), (RA, "mgba")],
    "gbc": [(RA, "gambatte"), (RA, "mgba")],
    "gba": [(RA, "mgba")],
    "nds": [("melonds", None), (RA, "melondsds")],
    "3ds": [("azahar", None), ("lime3ds", None), ("citra", None)],
    "virtualboy": [(RA, "mednafen_vb")],
    "pokemini": [(RA, "pokemini")],
    "gc": [("dolphin", None), (RA, "dolphin")],
    "wii": [("dolphin", None), (RA, "dolphin")],
    "wiiu": [("cemu", None)],
    "switch": [("ryujinx", None)],
    "sg-1000": [(RA, "genesis_plus_gx")],
    "mastersystem": [(RA, "genesis_plus_gx")],
    "megadrive": [(RA, "genesis_plus_gx")],
    "gamegear": [(RA, "genesis_plus_gx")],
    "segacd": [(RA, "genesis_plus_gx")],
    "sega32x": [(RA, "picodrive")],
    "saturn": [(RA, "mednafen_saturn"), (RA, "yabasanshiro")],
    "dreamcast": [("flycast", None), (RA, "flycast")],
    "psx": [("duckstation", None), (RA, "swanstation"), (RA, "pcsx_rearmed")],
    "ps2": [("pcsx2", None)],
    "psp": [("ppsspp", None), (RA, "ppsspp")],
    "ps3": [("rpcs3", None)],
    "psvita": [("vita3k", None)],
    "xbox": [("xemu", None)],
    "xbox360": [("xenia", None)],
    "ps4": [("shadps4", None)],
    "pcengine": [(RA, "mednafen_pce_fast")],
    "pcenginecd": [(RA, "mednafen_pce_fast")],
    "pcfx": [(RA, "mednafen_pcfx")],
    "neogeo": [(RA, "fbneo")],
    "neogeocd": [(RA, "neocd")],
    "ngp": [(RA, "mednafen_ngp")],
    "ngpc": [(RA, "mednafen_ngp")],
    "wonderswan": [(RA, "mednafen_wswan")],
    "wonderswancolor": [(RA, "mednafen_wswan")],
    "atari2600": [(RA, "stella")],
    "atari5200": [(RA, "a5200")],
    "atari7800": [(RA, "prosystem")],
    "atarilynx": [(RA, "handy")],
    "atarijaguar": [(RA, "virtualjaguar")],
    "3do": [(RA, "opera")],
    "coleco": [(RA, "gearcoleco")],
    "intellivision": [(RA, "freeintv")],
    "vectrex": [(RA, "vecx")],
    "msx": [(RA, "bluemsx")],
    "msx2": [(RA, "bluemsx")],
    "c64": [(RA, "vice_x64")],
    "amiga": [(RA, "puae")],
    "amstradcpc": [(RA, "cap32")],
    "zxspectrum": [(RA, "fuse")],
    "dos": [(RA, "dosbox_pure")],
    "arcade": [(RA, "mame"), (RA, "fbneo")],
    "fbneo": [(RA, "fbneo")],
}

STANDALONE_ARGS = {
    "dolphin": ["-b", "-e", "{rom}"],
    "pcsx2": ["-batch", "{rom}"],
    "duckstation": ["-batch", "{rom}"],
    "ppsspp": ["{rom}"],
    "cemu": ["-g", "{rom}"],
    "ryujinx": ["{rom}"],
    "rpcs3": ["--no-gui", "{rom}"],
    "xenia": ["{rom}"],
    "xemu": ["-dvd_path", "{rom}"],
    "azahar": ["{rom}"],
    "lime3ds": ["{rom}"],
    "citra": ["{rom}"],
    "melonds": ["{rom}"],
    "flycast": ["{rom}"],
    "vita3k": ["{rom}"],
    "shadps4": ["{rom}"],
}

# nomi degli eseguibili per la ricerca automatica degli emulatori in una cartella
KNOWN_EXES = {
    "retroarch": ["retroarch.exe", "retroarch"],
    "dolphin": ["dolphin.exe", "dolphin-emu"],
    "pcsx2": ["pcsx2-qt.exe", "pcsx2*.exe", "pcsx2-qt"],
    "duckstation": ["duckstation-qt*.exe", "duckstation*.exe", "duckstation-qt"],
    "ppsspp": ["ppssppwindows64.exe", "ppssppwindows.exe", "ppsspp*.exe", "ppssppqt"],
    "rpcs3": ["rpcs3.exe", "rpcs3"],
    "vita3k": ["vita3k.exe", "vita3k"],
    "melonds": ["melonds.exe", "melonds"],
    "azahar": ["azahar.exe", "azahar"],
    "lime3ds": ["lime3ds*.exe"],
    "citra": ["citra-qt.exe", "citra*.exe"],
    "cemu": ["cemu.exe", "cemu"],
    "ryujinx": ["ryujinx.exe", "ryujinx"],
    "xenia": ["xenia_canary.exe", "xenia.exe"],
    "xemu": ["xemu.exe", "xemu"],
    "flycast": ["flycast.exe", "flycast"],
    "shadps4": ["shadps4.exe", "shadps4"],
}


def detect_emulators(folder: Path, max_depth: int = 4) -> dict[str, str]:
    """Cerca gli eseguibili noti in una cartella (es. C:/Emulatori). Ritorna {emulatore: percorso}."""
    import fnmatch
    found: dict[str, str] = {}
    folder = Path(folder)
    if not folder.is_dir():
        return found

    def walk(d: Path, depth: int):
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_file():
                n = e.name.lower()
                for emu, pats in KNOWN_EXES.items():
                    if emu not in found and any(fnmatch.fnmatch(n, p) for p in pats):
                        found[emu] = str(e)
            elif depth < max_depth and not e.name.startswith(".") and e.name.lower() not in (
                    "cores", "saves", "states", "system", "shaders", "assets", "cache", "logs", "user", "screenshots"):
                walk(e, depth + 1)

    walk(folder, 1)
    return found

EMULATOR_NAMES = sorted({e for c in PRESETS.values() for e, _ in c} | set(STANDALONE_ARGS))
MAX_EXTRACTED = 3  # ZIP di dischi estratti tenuti in cache (i piu' recenti)


class LaunchError(Exception):
    pass


def open_uri(uri: str):
    """Apre un URI (steam://, com.epicgames.launcher://, ms-windows-store://, https://...) con l'app associata."""
    try:
        if sys.platform == "win32":
            os.startfile(uri)  # noqa: S606 - URI di store/launcher scelti dall'app
        else:
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", uri])
    except OSError as e:
        scheme = uri.split(":", 1)[0]
        raise LaunchError(f"Nessuna app installata per aprire «{scheme}:»: installa il client dello store. ({e})")


def _core_path(cfg, core: str) -> Path:
    cores = cfg.emulators.get("retroarch_cores")
    base = Path(cores) if cores else Path(cfg.emulators[RA]).parent / "cores"
    ext = ".dll" if sys.platform == "win32" else (".dylib" if sys.platform == "darwin" else ".so")
    return base / f"{core}_libretro{ext}"


def plan(cfg, platform: P.Platform) -> dict | None:
    """Emulatore scelto per una console: {emulator, exe, core, args} oppure None."""
    over = cfg.launch.get(platform.id, {})
    cands = [(over["emulator"], over.get("core"))] if over.get("emulator") else PRESETS.get(platform.id, [])
    for emu, core in cands:
        exe = (over.get("exe") if over.get("emulator") == emu else None) or cfg.emulators.get(emu)
        if not exe or not Path(exe).exists():
            continue
        if emu == RA:
            core = over.get("core", core)
            if not core or not _core_path(cfg, core).exists():
                continue
            args = over.get("args") or ["-L", "{core}", "{rom}"]
        else:
            args = over.get("args") or STANDALONE_ARGS.get(emu, ["{rom}"])
        return {"emulator": emu, "exe": exe, "core": core, "args": args}
    return None


def status(cfg) -> list[dict]:
    """Per la pagina impostazioni: emulatore configurato per ogni console."""
    out = []
    for pid in PRESETS:
        p = P.PLATFORMS.get(pid)
        if not p:
            continue
        pl = plan(cfg, p)
        out.append({"platform": pid, "name": p.name, "ok": bool(pl),
                    "emulator": pl["emulator"] + (f" ({pl['core']})" if pl and pl["core"] else "") if pl else None,
                    "candidates": [e + (f" ({c})" if c else "") for e, c in PRESETS[pid]]})
    return out


def resolve_target(platform: P.Platform, path: Path) -> Path:
    """File da passare all'emulatore. Per i giochi "a cartella" cerca il file giusto dentro."""
    if not path.is_dir():
        return path
    files = [f for f in path.rglob("*") if f.is_file()]
    if platform.id == "ps3":
        eboot = [f for f in files if f.name.upper() == "EBOOT.BIN" and f.parent.name.upper() == "USRDIR"]
        if eboot:
            return min(eboot, key=lambda f: len(f.parts))
    if platform.id == "xbox360":
        xex = [f for f in files if f.name.lower() == "default.xex"]
        if xex:
            return xex[0]
    exts = {"." + e for e in platform.exts}
    cands = [f for f in files if f.suffix.lower() in exts]
    if platform.id == "switch":  # il gioco base, non update/DLC
        base = [f for f in cands if not any(w in f.name.lower() for w in ("update", "upd", "dlc", "[v", "patch"))]
        cands = base or cands
    if cands:
        return max(cands, key=lambda f: f.stat().st_size)
    if files:
        return max(files, key=lambda f: f.stat().st_size)
    raise LaunchError(f"Nessun file avviabile nella cartella {path}")


def _needs_extract(pl: dict, platform: P.Platform, target: Path) -> bool:
    # RetroArch apre da solo gli ZIP di cartucce; i dischi e gli emulatori standalone no
    return target.suffix.lower() == ".zip" and (pl["emulator"] != RA or platform.cd)


def _extract(cfg, target: Path, ident: str, on_status) -> Path:
    root = Path(cfg.cache) / "extract"
    dest = root / ident
    with zipfile.ZipFile(target) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if not infos:
            raise LaunchError("ZIP vuoto")
        main = max(infos, key=lambda i: i.file_size)
        out = dest / Path(main.filename).name
        if out.exists() and out.stat().st_size == main.file_size:
            os.utime(dest)
            return out
        dest.mkdir(parents=True, exist_ok=True)
        on_status(f"Estrazione di {main.filename} ({main.file_size / 2**30:.1f} GB)...")
        for info in infos:  # estrae tutto (es. .cue + .bin)
            z.extract(info, dest)
            ex = dest / info.filename
            if ex.parent != dest:
                shutil.move(str(ex), dest / ex.name)
    # tiene solo gli ultimi MAX_EXTRACTED giochi estratti
    olds = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
    for d in olds[MAX_EXTRACTED:]:
        shutil.rmtree(d, ignore_errors=True)
    return out


def _xinput():
    """Lettura dei controller Xbox (XInput) da Windows: funziona anche quando la finestra attiva e'
    quella del gioco (il browser invece smette di leggere i controller se non ha il focus)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class GAMEPAD(ctypes.Structure):
        _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                    ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                    ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]

    class STATE(ctypes.Structure):
        _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", GAMEPAD)]

    for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            lib = getattr(ctypes.windll, dll)
            break
        except OSError:
            continue
    else:
        return None

    def buttons() -> int:
        out = 0
        for i in range(4):
            st = STATE()
            if lib.XInputGetState(i, ctypes.byref(st)) == 0:
                out |= st.Gamepad.wButtons
        return out
    return buttons


XINPUT_BACK, XINPUT_START = 0x0020, 0x0010


class Launcher:
    def __init__(self, cfg, userdata, on_event=None):
        self.cfg = cfg
        self.user = userdata
        self.on_event = on_event or (lambda name, data: None)
        self.state: dict = {}
        self.lock = threading.Lock()

    def _watch_combo(self):
        """Select/View + Start/Menu tenuti premuti per 2 secondi chiudono il gioco in esecuzione."""
        read = _xinput()
        if not read:
            return
        since = 0.0
        while self.state.get("status") == "running":
            pressed = read()
            if pressed & XINPUT_BACK and pressed & XINPUT_START:
                since = since or time.time()
                if time.time() - since >= 2:
                    self.stop()
                    return
            else:
                since = 0.0
            time.sleep(0.1)

    def stop(self) -> bool:
        """Chiude il gioco in esecuzione (es. Select+Start tenuti premuti nella Big Picture)."""
        if self.state.get("status") != "running":
            return False
        try:
            import psutil
        except ImportError:
            psutil = None
        procs = []
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            if psutil:
                try:
                    p = psutil.Process(proc.pid)
                    procs += p.children(recursive=True) + [p]
                except psutil.Error:
                    pass
            else:
                proc.terminate()
        track = getattr(self, "_track", None)
        if track and Path(track).is_dir():
            procs += self._game_procs(track)
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        if psutil and procs:
            _, alive = psutil.wait_procs(procs, timeout=5)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
        return bool(procs) or bool(proc)

    def running(self) -> dict:
        return dict(self.state)

    def launch(self, key: str, title: str, platform: P.Platform, path: Path):
        with self.lock:
            if self.state.get("status") in ("starting", "extracting", "running"):
                raise LaunchError(f"E' gia' in esecuzione: {self.state.get('title')}")
            pl = plan(self.cfg, platform)
            if not pl:
                cands = ", ".join(e + (f" (core {c})" if c else "") for e, c in PRESETS.get(platform.id, [])) or "nessun preset"
                raise LaunchError(f"Nessun emulatore configurato per {platform.name}. Aggiungi in config.toml, "
                                  f"sezione [emulators], uno di: {cands}")
            self.state = {"key": key, "title": title, "status": "starting", "message": "", "emulator": pl["emulator"],
                          "started": time.time()}
        threading.Thread(target=self._run, args=(key, platform, path, pl), daemon=True).start()

    # ------------------------------------------------------------ giochi PC (store e manuali)
    LAUNCHER_EXES = {"explorer.exe", "battle.net launcher.exe", "battle.net.exe"}

    def launch_pc(self, key: str, title: str, launch: dict, install_dir: str, store: str):
        with self.lock:
            if self.state.get("status") in ("starting", "extracting", "running"):
                raise LaunchError(f"E' gia' in esecuzione: {self.state.get('title')}")
            if not launch or not launch.get("target"):
                raise LaunchError("Questo gioco non ha un comando di avvio (prova ad aprirlo dallo store)")
            self.state = {"key": key, "title": title, "status": "starting", "message": "", "emulator": store,
                          "started": time.time()}
        threading.Thread(target=self._run_pc, args=(key, launch, install_dir), daemon=True).start()

    @staticmethod
    def _game_procs(folder: str) -> list:
        """Processi in esecuzione il cui eseguibile sta nella cartella del gioco."""
        try:
            import psutil
        except ImportError:
            return []
        root = os.path.normcase(os.path.abspath(folder)) + os.sep
        out = []
        for p in psutil.process_iter(["exe"]):
            exe = p.info.get("exe")
            if exe and os.path.normcase(exe).startswith(root):
                out.append(p)
        return out

    def _run_pc(self, key: str, launch: dict, install_dir: str):
        try:
            target, args = launch["target"], launch.get("args") or []
            t0 = time.time()
            proc = None
            if launch.get("type") == "uri":
                open_uri(target)
            else:
                if not Path(target).exists() and Path(target).name.lower() not in self.LAUNCHER_EXES:
                    raise LaunchError(f"Eseguibile non trovato: {target}")
                proc = subprocess.Popen([target, *args], cwd=launch.get("cwd") or str(Path(target).parent))
            self._set(status="running", message=target)
            self._proc = proc
            self._track = launch.get("track_dir") or install_dir
            via_launcher = proc is None or Path(target).name.lower() in self.LAUNCHER_EXES
            track = launch.get("track_dir") or install_dir
            if not via_launcher:
                proc.wait()  # gioco avviato direttamente: si aspetta il suo processo
            elif track and Path(track).is_dir():
                # avviato tramite launcher: si aspetta che compaia un processo del gioco, poi che finisca
                deadline = time.time() + 180
                while time.time() < deadline and not self._game_procs(track):
                    time.sleep(2)
                t0 = time.time() if self._game_procs(track) else t0
                while self._game_procs(track):
                    time.sleep(3)
            played = time.time() - t0 if (not via_launcher or track) else 0
            self.user.record_play(key, played)
            self._set(status="finished", played=played, code=proc.returncode if proc else 0)
        except LaunchError as e:
            self._set(status="error", message=str(e))
        except Exception as e:
            self._set(status="error", message=f"{type(e).__name__}: {e}")

    def _set(self, **kw):
        with self.lock:
            prev = self.state.get("status")
            self.state.update(kw)
            st = dict(self.state)
        if kw.get("status") == "running" and prev != "running":
            self.on_event("game_started", st)
            if self.cfg.app.get("exit_combo", True):
                threading.Thread(target=self._watch_combo, daemon=True).start()
        elif kw.get("status") in ("finished", "error") and prev in ("running", "starting", "extracting"):
            self.on_event("game_stopped", st)

    def _run(self, key: str, platform: P.Platform, path: Path, pl: dict):
        try:
            target = resolve_target(platform, path)
            if _needs_extract(pl, platform, target):
                self._set(status="extracting")
                target = _extract(self.cfg, target, key.replace("/", "_").replace(":", "_"),
                                  lambda m: self._set(message=m))
            core = str(_core_path(self.cfg, pl["core"])) if pl["core"] else ""
            args = [a.replace("{rom}", str(target)).replace("{core}", core) for a in pl["args"]]
            cmd = [pl["exe"], *args]
            self._set(status="running", message=" ".join(f'"{c}"' if " " in c else c for c in cmd))
            t0 = time.time()
            proc = subprocess.Popen(cmd, cwd=str(Path(pl["exe"]).parent))
            self._proc, self._track = proc, None
            proc.wait()
            played = time.time() - t0
            self.user.record_play(key, played)
            self._set(status="finished", played=played, code=proc.returncode)
        except LaunchError as e:
            self._set(status="error", message=str(e))
        except Exception as e:
            self._set(status="error", message=f"{type(e).__name__}: {e}")
