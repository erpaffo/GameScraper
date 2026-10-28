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
Un singolo gioco puo' avere un profilo proprio (scheda del gioco > Profilo di avvio): emulatore, core,
eseguibile diverso (es. un'altra versione di PCSX2), argomenti e argomenti aggiuntivi.
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
    "switch": [("ryujinx", None), ("eden", None)],
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
    "eden": ["-g", "{rom}"],  # fork di yuzu: stessa riga di comando
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
    "eden": ["eden.exe", "eden*.appimage", "eden"],
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


def _split_args(s) -> list[str]:
    if isinstance(s, list):
        return [str(x) for x in s]
    import shlex
    return [a.strip('"') for a in shlex.split(str(s or ""), posix=False)]


def default_core(platform_id: str, emulator: str) -> str | None:
    return next((c for e, c in PRESETS.get(platform_id, []) if e == emulator and c), None)


def plan(cfg, platform: P.Platform, profile: dict | None = None) -> dict | None:
    """Emulatore scelto per una console (o per un gioco con un profilo proprio):
    {emulator, exe, core, args, profile} oppure None."""
    over = dict(cfg.launch.get(platform.id, {}))
    prof = {k: v for k, v in (profile or {}).items() if v not in (None, "", []) and k not in SCRIPT_KEYS + COMPANION_KEYS}
    if prof:
        emu = prof.get("emulator") or over.get("emulator")
        if emu:
            # emulatore del profilo: si parte dal suo preset per questa console, non da quello della console
            over = {"emulator": emu, "core": prof.get("core") or (over.get("core") if over.get("emulator") == emu else None)
                    or default_core(platform.id, emu), "exe": prof.get("exe") or (over.get("exe") if over.get("emulator") == emu else None),
                    "args": _split_args(prof["args"]) if prof.get("args") else (over.get("args") if over.get("emulator") == emu else None)}
        else:  # solo core/eseguibile/argomenti diversi sul preset della console
            p0 = plan(cfg, platform)
            if p0:
                over = {"emulator": p0["emulator"], "core": prof.get("core") or p0["core"], "exe": prof.get("exe") or p0["exe"],
                        "args": _split_args(prof["args"]) if prof.get("args") else p0["args"]}
        if prof.get("exe") and not Path(prof["exe"]).exists():
            raise LaunchError(f"Profilo del gioco: eseguibile non trovato ({prof['exe']})")
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
        extra = _split_args(prof.get("extra")) if prof.get("extra") else []
        if extra:  # argomenti aggiuntivi prima del file del gioco
            i = next((n for n, a in enumerate(args) if "{rom}" in a), len(args))
            if i > 0 and args[i - 1] in ("-g", "--game", "-e", "-dvd_path"):
                i -= 1
            args = [*args[:i], *extra, *args[i:]]
        return {"emulator": emu, "exe": exe, "core": core, "args": list(args), "profile": bool(prof)}
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
        base = [f for f in cands if "000]" in f.name.upper() or "000 " in f.name.upper()] or [
            f for f in cands
            if not any(w in f.relative_to(path).as_posix().lower() for w in ("update", "upd", "dlc", "patch"))
            and ("[v0]" in f.name.lower() or "[v" not in f.name.lower())
        ]
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


XINPUT_BACK, XINPUT_START, XINPUT_RB = 0x0020, 0x0010, 0x0200

# applicazione da avviare insieme al gioco (es. il deck tracker di Hearthstone): programma, argomenti, "1" = chiudila a fine partita
COMPANION_KEYS = ("companion", "companion_args", "companion_close")
SCRIPT_KEYS = ("before", "after")  # script prima/dopo il gioco: in cfg.app["launch"] (tutti i giochi) e nel profilo del gioco
# processo del launcher di ogni store, chiuso a fine partita se l'aveva aperto il gioco (cfg.app["launch"]["close_launcher"])
STORE_LAUNCHERS = {"steam": "steam.exe", "epic": "epicgameslauncher.exe", "gog": "galaxyclient.exe",
                   "battlenet": "battle.net.exe", "amazon": "amazon games.exe"}
BEFORE_WAIT = 15  # secondi massimi di attesa di uno script «prima» del gioco, poi il gioco parte comunque
LAUNCHER_START_WAIT = 40  # secondi massimi per veder comparire il processo del launcher (Battle.net) prima di lanciare il gioco
LAUNCHER_SETTLE = 8       # secondi di pausa dopo la sua comparsa: il launcher deve essere attivo per accettare il comando
LAUNCH_TRIES = 3          # tentativi di avvio di un gioco passato dal launcher
LAUNCH_CHECK = 75         # secondi di attesa del processo del gioco prima di riprovare
LAUNCHER_GRACE = 10 # secondi dopo la partita prima di chiudere il launcher: finisce la sincronizzazione dei salvataggi


def _procs_named(name: str) -> list:
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() == name:
            out.append(p)
    return out


def close_launcher(name: str):
    """Chiude il launcher dello store: Steam con -shutdown (chiusura pulita), gli altri terminando il processo."""
    procs = _procs_named(name)
    if name == "steam.exe" and procs:
        try:
            subprocess.Popen([procs[0].exe(), "-shutdown"])
            return
        except Exception:
            pass
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass


def hide_launcher_windows(name: str, seconds: float = 40):
    """Riduce a icona le finestre del launcher (Steam, Battle.net...) comparse per avviare il gioco, cosi' non
    coprono il gioco: il launcher resta attivo, in sottofondo. Solo Windows; non tocca un launcher gia' aperto."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    end = time.time() + seconds
    while time.time() < end:
        try:
            pids = {p.pid for p in _procs_named(name)}
        except Exception:  # funzione solo estetica: un errore non deve mai disturbare l'avvio
            return

        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 7)  # SW_SHOWMINNOACTIVE: a icona, senza rubare il fuoco al gioco
            return True

        if pids:
            user32.EnumWindows(proc(visit), 0)
        time.sleep(0.5)


class Launcher:
    def __init__(self, cfg, userdata, on_event=None, shots=None, profile_of=None, prelaunch=None):
        self.cfg = cfg
        # prelaunch(stato) -> argomenti in piu' prima di quelli del gioco (es. DS/3DS con il telefono come schermo touch)
        self.prelaunch = prelaunch or (lambda st: [])
        self.user = userdata
        self.on_event = on_event or (lambda name, data: None)
        self.shots = shots  # archivio delle schermate (screens.Shots)
        self.profile_of = profile_of or (lambda key: None)  # profilo di avvio del gioco
        self._stop_evt = threading.Event()  # "Chiudi gioco": fa smettere di aspettare il gioco, anche se non si trova il processo
        self.stop_found = False  # l'ultimo stop ha trovato (e chiuso) dei processi?
        self.state: dict = {}
        self.lock = threading.Lock()
        self._hotkey = None

    def screenshot(self) -> bool:
        """Cattura la finestra del gioco e la salva nell'app."""
        key = self.state.get("key")
        if not self.shots or not key or self.state.get("status") != "running":
            return False
        from . import screens
        try:
            img = screens.capture_foreground()
            sid = self.shots.add_image(key, img)
        except Exception as e:
            self._set(message=f"Schermata non riuscita: {e}")
            return False
        screens.beep()
        with self.lock:
            self.state["shots"] = self.state.get("shots", 0) + 1
            self.state["last_shot"] = sid
        self.on_event("screenshot", {"key": key, "id": sid})
        return True

    def _start_capture(self):
        from . import screens
        st = screens.settings(self.cfg)
        if self.shots and st.get("hotkey"):
            self._hotkey = screens.Hotkey(st["hotkey"], self.screenshot)
            self._hotkey.start()

    def _stop_capture(self, st: dict):
        if self._hotkey:
            self._hotkey.stop()
            self._hotkey = None
        from . import screens
        if self.shots and screens.settings(self.cfg).get("import_emulator") and st.get("key") and st.get("started"):
            try:  # schermate fatte con il tasto dell'emulatore durante la sessione
                n = screens.import_session(self.shots, st["key"], st.get("emulator_id") or st.get("emulator") or "",
                                           st.get("exe"), st["started"], time.time())
                if n:
                    with self.lock:
                        self.state["shots"] = self.state.get("shots", 0) + n
                    self.on_event("screenshot", {"key": st["key"], "imported": n})
            except Exception:
                pass

    def _watch_combo(self):
        """Controller (XInput) durante il gioco: Select/View + Start/Menu tenuti 2 secondi chiudono il gioco,
        Select/View + R1/RB fanno una schermata."""
        read = _xinput()
        if not read:
            return
        from . import screens
        pad_shot = screens.settings(self.cfg).get("pad", True)
        exit_combo = self.cfg.app.get("exit_combo", True)
        if not pad_shot and not exit_combo:
            return
        since = 0.0
        shot_held = False
        while self.state.get("status") == "running":
            pressed = read()
            if exit_combo and pressed & XINPUT_BACK and pressed & XINPUT_START:
                since = since or time.time()
                if time.time() - since >= 2:
                    self.stop()
                    return
            else:
                since = 0.0
            combo = bool(pressed & XINPUT_BACK and pressed & XINPUT_RB)
            if pad_shot and combo and not shot_held:
                threading.Thread(target=self.screenshot, daemon=True).start()
            shot_held = combo
            time.sleep(0.05)

    def _wait_proc(self, proc):
        """Aspetta la fine del processo, ma si ferma subito se l'utente preme «Chiudi gioco»."""
        while proc.poll() is None and not self._stop_evt.is_set():
            self._stop_evt.wait(0.5)

    def stop(self) -> bool:
        """Chiude il gioco in esecuzione (pulsante «Chiudi gioco», Select+Start nella Big Picture).
        Se non trova nessun processo da chiudere (gioco avviato da un launcher, cartella sconosciuta...) interrompe
        comunque l'attesa: il launcher smette di mostrarlo «in esecuzione». False solo se non c'era nessun gioco."""
        self.stop_found = False
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
        self.stop_found = bool(procs) or bool(proc)
        self._stop_evt.set()  # l'attesa in _run_pc/_run finisce: tempo di gioco registrato e stato «finished»
        return True

    def running(self) -> dict:
        return dict(self.state)

    def launch(self, key: str, title: str, platform: P.Platform, path: Path):
        with self.lock:
            if self.state.get("status") in ("starting", "extracting", "running"):
                raise LaunchError(f"E' gia' in esecuzione: {self.state.get('title')}")
            pl = plan(self.cfg, platform, self.profile_of(key))
            if not pl:
                cands = ", ".join(e + (f" (core {c})" if c else "") for e, c in PRESETS.get(platform.id, [])) or "nessun preset"
                raise LaunchError(f"Nessun emulatore configurato per {platform.name}. Aggiungi in config.toml, "
                                  f"sezione [emulators], uno di: {cands}")
            self._stop_evt.clear()
            self.state = {"key": key, "title": title, "status": "starting", "message": "", "emulator": pl["emulator"],
                          "emulator_id": pl["emulator"], "exe": pl["exe"], "profile": pl.get("profile", False),
                          "started": time.time(), "shots": 0}
        threading.Thread(target=self._run, args=(key, platform, path, pl), daemon=True).start()

    # ------------------------------------------------------------ giochi PC (store e manuali)
    LAUNCHER_EXES = {"explorer.exe", "battle.net launcher.exe", "battle.net.exe"}

    def launch_pc(self, key: str, title: str, launch: dict, install_dir: str, store: str, store_id: str = ""):
        with self.lock:
            if self.state.get("status") in ("starting", "extracting", "running"):
                raise LaunchError(f"E' gia' in esecuzione: {self.state.get('title')}")
            if not launch or not launch.get("target"):
                raise LaunchError("Questo gioco non ha un comando di avvio (prova ad aprirlo dallo store)")
            self._stop_evt.clear()
            self.state = {"key": key, "title": title, "status": "starting", "message": "", "emulator": store,
                          "emulator_id": "", "exe": launch.get("target") if launch.get("type") != "uri" else None,
                          "started": time.time(), "shots": 0}
        threading.Thread(target=self._run_pc, args=(key, launch, install_dir, store_id), daemon=True).start()

    def _scripts(self, key: str, when: str):
        """Script dell'utente prima o dopo il gioco: prima quello di tutti i giochi, poi quello del gioco
        (dopo la partita al contrario). Un errore non blocca il gioco: finisce nel messaggio di stato."""
        cmds = [(self.cfg.app.get("launch") or {}).get(when), (self.profile_of(key) or {}).get(when)]
        env = {**os.environ, "POZZO_KEY": key, "POZZO_TITLE": self.state.get("title") or ""}
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        for cmd in filter(None, cmds if when == "before" else cmds[::-1]):
            try:
                p = subprocess.Popen(cmd, shell=True, env=env, creationflags=flags)
                try:
                    # prima del gioco si aspetta poco: uno script che non finisce (es. un tracker) resta in
                    # esecuzione e il gioco parte lo stesso
                    code = p.wait(timeout=BEFORE_WAIT if when == "before" else 120)
                    if code:
                        self._set(message=f"Script «{when}» terminato con codice {code}: {cmd}")
                except subprocess.TimeoutExpired:
                    if when != "before":
                        p.kill()
                        self._set(message=f"Script «{when}» interrotto dopo 120 s: {cmd}")
            except Exception as e:
                self._set(message=f"Script «{when}» non riuscito ({type(e).__name__}): {cmd}")

    def _companion(self, key: str):
        """Avvia (staccata) l'applicazione abbinata al gioco, se c'e'. Un errore non blocca il gioco."""
        import shlex
        pr = self.profile_of(key) or {}
        if not pr.get("companion"):
            return None
        try:
            args = [a.strip('"') for a in shlex.split(pr.get("companion_args") or "", posix=False)]
            return subprocess.Popen([pr["companion"], *args], cwd=str(Path(pr["companion"]).parent))
        except Exception as e:
            self._set(message=f"Applicazione abbinata non avviata ({type(e).__name__}): {pr['companion']}")
            return None

    def _end_companion(self, proc, key: str):
        if proc and (self.profile_of(key) or {}).get("companion_close") and proc.poll() is None:
            proc.terminate()

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

    def _run_pc(self, key: str, launch: dict, install_dir: str, store_id: str = ""):
        launcher = STORE_LAUNCHERS.get(store_id) if (self.cfg.app.get("launch") or {}).get("close_launcher") else None
        launcher_was_open = bool(launcher and _procs_named(launcher))
        # launcher dello store aperto dal gioco: finestra ridotta a icona (disattivabile: launch.hide_launcher = false)
        lname = STORE_LAUNCHERS.get(store_id)
        quiet = bool(lname and (self.cfg.app.get("launch") or {}).get("hide_launcher", True) and not _procs_named(lname))
        self._scripts(key, "before")
        comp = self._companion(key)
        self.on_event("game_launching", dict(self.state))  # sincrono: la copia dei salvataggi finisce prima del gioco
        try:
            target, args = launch["target"], launch.get("args") or []
            t0 = time.time()
            is_uri = launch.get("type") == "uri"
            if not is_uri and not Path(target).exists() and Path(target).name.lower() not in self.LAUNCHER_EXES:
                raise LaunchError(f"Eseguibile non trovato: {target}")

            def ensure_launcher():
                """Battle.net: prima si apre da solo e si aspetta che sia attivo, poi riceve il comando del gioco."""
                if store_id != "battlenet" or is_uri or not lname or _procs_named(lname):
                    return
                subprocess.Popen([target], cwd=launch.get("cwd") or str(Path(target).parent))
                end = time.time() + LAUNCHER_START_WAIT
                while time.time() < end and not _procs_named(lname) and not self._stop_evt.is_set():
                    self._stop_evt.wait(1)
                self._stop_evt.wait(LAUNCHER_SETTLE)

            def start():
                ensure_launcher()
                if is_uri:
                    open_uri(target)
                    return None
                return subprocess.Popen([target, *args], cwd=launch.get("cwd") or str(Path(target).parent))

            proc = start()
            if quiet:
                threading.Thread(target=hide_launcher_windows, args=(lname,), daemon=True).start()
            self._set(status="running", message=target)
            self._proc = proc
            self._track = launch.get("track_dir") or install_dir
            via_launcher = proc is None or Path(target).name.lower() in self.LAUNCHER_EXES
            track = launch.get("track_dir") or install_dir
            if not via_launcher:
                self._wait_proc(proc)  # gioco avviato direttamente: si aspetta il suo processo
            elif track and Path(track).is_dir():
                # avviato tramite launcher: si controlla che il gioco parta davvero, altrimenti si riprova
                # (start() riapre il launcher se nel frattempo e' stato chiuso)
                for attempt in range(LAUNCH_TRIES):
                    deadline = time.time() + LAUNCH_CHECK
                    while time.time() < deadline and not self._game_procs(track) and not self._stop_evt.is_set():
                        self._stop_evt.wait(2)
                    if self._game_procs(track) or self._stop_evt.is_set() or attempt == LAUNCH_TRIES - 1:
                        break
                    self._set(message=f"Il gioco non e' partito: nuovo tentativo {attempt + 2}/{LAUNCH_TRIES}")
                    proc = start()
                    self._proc = proc
                if lname and self._game_procs(track) and (self.cfg.app.get("launch") or {}).get("hide_launcher", True):
                    threading.Thread(target=hide_launcher_windows, args=(lname, 15), daemon=True).start()  # il gioco passa avanti
                t0 = time.time() if self._game_procs(track) else t0
                while self._game_procs(track) and not self._stop_evt.is_set():
                    self._stop_evt.wait(3)
            played = time.time() - t0 if (not via_launcher or track) else 0
            self.user.record_play(key, played)
            self._end_companion(comp, key)
            self._scripts(key, "after")
            self._set(status="finished", played=played, code=proc.returncode if proc else 0)
            if launcher and not launcher_was_open:  # solo se l'ha aperto il gioco: un launcher gia' aperto resta
                time.sleep(LAUNCHER_GRACE)
                if self.state.get("status") == "finished":  # nessun altro gioco avviato nel frattempo
                    close_launcher(launcher)
        except LaunchError as e:
            self._end_companion(comp, key)
            self._set(status="error", message=str(e))
        except Exception as e:
            self._end_companion(comp, key)
            self._set(status="error", message=f"{type(e).__name__}: {e}")

    def _set(self, **kw):
        with self.lock:
            prev = self.state.get("status")
            self.state.update(kw)
            st = dict(self.state)
        if kw.get("status") == "running" and prev != "running":
            try:
                self.user.mark_started(st["key"])
            except Exception:  # i dati utente non devono impedire di giocare
                pass
            self.on_event("game_started", st)
            self._start_capture()
            threading.Thread(target=self._watch_combo, daemon=True).start()
        elif kw.get("status") in ("finished", "error") and prev in ("running", "starting", "extracting"):
            if prev == "running":
                self._stop_capture(st)
                with self.lock:
                    st = dict(self.state)
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
            self._scripts(key, "before")
            comp = self._companion(key)
            cmd = [pl["exe"], *self.prelaunch(dict(self.state)), *args]
            self.on_event("game_launching", dict(self.state))
            self._set(status="running", message=" ".join(f'"{c}"' if " " in c else c for c in cmd))
            t0 = time.time()
            proc = subprocess.Popen(cmd, cwd=str(Path(pl["exe"]).parent))
            self._proc, self._track = proc, None
            self._wait_proc(proc)
            played = time.time() - t0
            self.user.record_play(key, played)
            self._end_companion(comp, key)
            self._scripts(key, "after")
            self._set(status="finished", played=played, code=proc.returncode)
        except LaunchError as e:
            self._set(status="error", message=str(e))
        except Exception as e:
            self._set(status="error", message=f"{type(e).__name__}: {e}")
