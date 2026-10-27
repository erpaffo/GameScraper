"""Configurazione automatica degli emulatori per i controller del telefono.

- PCSX2 Buzz!: porta USB 1 = «Buzz Controller», il giocatore N usa il controller XInput N-1 (il telefono N);
  rosso = A, blu = Y, arancione = X, verde = B, giallo = RB (come l'aspetto «Buzz» dell'app del telefono).
- PCSX2 chitarra: le porte 1..N diventano «Guitar» (Guitar Hero), tasti come una chitarra Xbox 360.
- Dolphin Wii Remote + Nunchuk: tutto dal server DSU di GameScraper (tasti, levetta, accelerometro e giroscopio),
  cosi' un solo dispositivo per Wii Remote: profili «GameScraper Telefono N» e, a richiesta, i Wii Remote 1..N.

Formati verificati sui sorgenti: PCSX2 (USB «BuzzDevice» con i tasti Red1..Yellow4, Pad «Guitar», XInput-N/A),
Dolphin (DSUClient.ini [Server] Entries = nome:indirizzo:porta;, dispositivo DSUClient/<slot>/<nome>).
Prima di scrivere si fa una copia del file accanto all'originale (.gamescraper-<data>); l'emulatore deve essere chiuso,
altrimenti all'uscita riscriverebbe le sue impostazioni.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

DSU_NAME = "GameScraper"
BACKUP_TAG = ".gamescraper-"

BUZZ = {"Red": "A", "Blue": "Y", "Orange": "X", "Green": "B", "Yellow": "RightShoulder"}
GUITAR = {"Green": "A", "Red": "B", "Yellow": "Y", "Blue": "X", "Orange": "LeftShoulder", "Up": "DPadUp", "Down": "DPadDown",
          "Select": "Back", "Start": "Start", "Whammy": "+RightX", "Tilt": "RightShoulder"}


# ------------------------------------------------------------------ ini
def ini_update(text: str, changes: dict[str, dict[str, str]], replace: set[str] = frozenset()) -> str:
    """Cambia le chiavi indicate lasciando il resto del file com'e' (commenti, ordine, sezioni duplicate comprese).
    Le sezioni in `replace` vengono sostituite per intero."""
    blocks: list[list] = [[None, []]]  # [nome sezione, righe]
    for line in text.splitlines():
        m = re.match(r"\s*\[([^\]]+)\]\s*$", line)
        if m:
            blocks.append([m.group(1), [line]])
        else:
            blocks[-1][1].append(line)
    todo = {s: dict(kv) for s, kv in changes.items()}
    for b in blocks:
        name, lines = b
        if name not in todo:
            continue
        kv = todo.pop(name)
        if name in replace:
            b[1] = [lines[0]] + [f"{k} = {v}" for k, v in kv.items()] + [""]
            continue
        for i, line in enumerate(lines[1:], 1):
            k = line.split("=", 1)[0].strip() if "=" in line else None
            if k in kv:
                lines[i] = f"{k} = {kv.pop(k)}"
        end = len(lines)
        while end > 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = [f"{k} = {v}" for k, v in kv.items()]
    for name, kv in todo.items():  # sezioni nuove in fondo
        if blocks[-1][1] and blocks[-1][1][-1].strip():
            blocks[-1][1].append("")
        blocks.append([name, [f"[{name}]"] + [f"{k} = {v}" for k, v in kv.items()] + [""]])
    out = "\n".join(line for _, lines in blocks for line in lines)
    return out.rstrip("\n") + "\n"


def ini_get(text: str, section: str, key: str) -> str | None:
    cur = None
    for line in text.splitlines():
        m = re.match(r"\s*\[([^\]]+)\]\s*$", line)
        if m:
            cur = m.group(1)
        elif cur == section and "=" in line and line.split("=", 1)[0].strip() == key:
            return line.split("=", 1)[1].strip()
    return None


# ------------------------------------------------------------------ cartelle degli emulatori
def documents_dir() -> Path:
    if sys.platform == "win32":
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL (anche se spostata su OneDrive)
            return Path(buf.value)
    return Path.home() / "Documents"


def pcsx2_ini(exe: str | None) -> Path:
    if exe:
        d = Path(exe).parent
        if (d / "portable.ini").exists() or (d / "portable.txt").exists():
            return d / "inis" / "PCSX2.ini"
    return documents_dir() / "PCSX2" / "inis" / "PCSX2.ini"


def dolphin_dir(exe: str | None) -> Path:
    if exe and (Path(exe).parent / "portable.txt").exists():
        return Path(exe).parent / "User"
    appdata = Path(os.environ.get("APPDATA", "")) / "Dolphin Emulator"
    if appdata.exists():
        return appdata
    return documents_dir() / "Dolphin Emulator"


def _running(names: tuple[str, ...]) -> bool:
    try:
        import psutil
        return any((p.info.get("name") or "").lower() in names for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def backup(p: Path) -> Path | None:
    if not p.exists():
        return None
    dst = p.with_name(p.name + BACKUP_TAG + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(p, dst)
    return dst


def latest_backup(p: Path) -> Path | None:
    found = sorted(p.parent.glob(p.name + BACKUP_TAG + "*"))
    return found[-1] if found else None


def _write(p: Path, changes: dict, replace: set[str] = frozenset()) -> str | None:
    text = p.read_text(encoding="utf-8-sig", errors="replace") if p.exists() else ""
    b = backup(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ini_update(text, changes, replace), encoding="utf-8")
    return str(b) if b else None


# ------------------------------------------------------------------ PCSX2
def pcsx2_changes(what: str, players: int) -> tuple[dict, set[str]]:
    players = max(1, min(4, players))
    ch: dict[str, dict[str, str]] = {"InputSources": {"XInput": "true"}}
    rep: set[str] = set()
    if what == "buzz":
        ch["USB1"] = {"Type": "BuzzDevice", **{f"BuzzDevice_{c}{p}": f"XInput-{p - 1}/{b}"
                                               for p in range(1, players + 1) for c, b in BUZZ.items()}}
        rep.add("USB1")
    elif what == "guitar":
        for p in range(1, players + 1):
            ch[f"Pad{p}"] = {"Type": "Guitar", **{k: f"XInput-{p - 1}/{v}" for k, v in GUITAR.items()}}
            rep.add(f"Pad{p}")
    else:
        raise ValueError(what)
    return ch, rep


# ------------------------------------------------------------------ Dolphin
def wiimote_keys(slot: int) -> dict[str, str]:
    acc = ["Up", "Down", "Left", "Right", "Forward", "Backward"]
    gyr = ["Pitch Up", "Pitch Down", "Roll Left", "Roll Right", "Yaw Left", "Yaw Right"]
    return {
        "Device": f"DSUClient/{slot}/{DSU_NAME}",
        "Buttons/A": "Cross", "Buttons/B": "Circle", "Buttons/1": "Square", "Buttons/2": "Triangle",
        "Buttons/-": "Share", "Buttons/+": "Options", "Buttons/Home": "PS",
        "D-Pad/Up": "`Pad N`", "D-Pad/Down": "`Pad S`", "D-Pad/Left": "`Pad W`", "D-Pad/Right": "`Pad E`",
        **{f"IMUAccelerometer/{a}": f"`Accel {a}`" for a in acc},
        **{f"IMUGyroscope/{g}": f"`Gyro {g}`" for g in gyr},
        "Extension": "Nunchuk",
        "Nunchuk/Buttons/C": "L1", "Nunchuk/Buttons/Z": "L2",
        "Nunchuk/Stick/Up": "`Left Y+`", "Nunchuk/Stick/Down": "`Left Y-`",
        "Nunchuk/Stick/Left": "`Left X-`", "Nunchuk/Stick/Right": "`Left X+`",
    }


def dsu_entries(current: str | None, port: int = 26760) -> str:
    cur = [e for e in (current or "").split(";") if e.strip()]
    if not any(e.split(":")[1:3] == ["127.0.0.1", str(port)] for e in cur if e.count(":") >= 2):
        cur.append(f"{DSU_NAME}:127.0.0.1:{port}")
    return "".join(e + ";" for e in cur)


# ------------------------------------------------------------------ azioni
def setup(what: str, emulators: dict, players: int = 1, apply_wiimotes: bool = True) -> dict:
    """what: pcsx2_buzz | pcsx2_guitar | dolphin_wii. Ritorna {"files": [...], "backups": [...], "note": str}."""
    players = max(1, min(4, int(players or 1)))
    if what.startswith("pcsx2_"):
        if _running(("pcsx2-qt.exe", "pcsx2.exe")):
            raise RuntimeError("Chiudi PCSX2 prima: all'uscita riscriverebbe le sue impostazioni")
        p = pcsx2_ini(emulators.get("pcsx2"))
        if not p.exists():
            raise RuntimeError(f"Impostazioni di PCSX2 non trovate ({p}): avvia PCSX2 una volta e chiudilo")
        ch, rep = pcsx2_changes(what[6:], players)
        b = _write(p, ch, rep)
        return {"files": [str(p)], "backups": [b] if b else [],
                "note": "In PCSX2 il telefono N è il controller XInput N-1 (giocatore N). Le associazioni sono in Impostazioni › Controller."}
    if what == "dolphin_wii":
        if _running(("dolphin.exe",)):
            raise RuntimeError("Chiudi Dolphin prima: all'uscita riscriverebbe le sue impostazioni")
        base = dolphin_dir(emulators.get("dolphin")) / "Config"
        files, backups = [], []
        dsu = base / "DSUClient.ini"
        cur = ini_get(dsu.read_text(encoding="utf-8-sig", errors="replace"), "Server", "Entries") if dsu.exists() else None
        backups.append(_write(dsu, {"Server": {"Enabled": "True", "Entries": dsu_entries(cur)}}))
        files.append(str(dsu))
        prof = base / "Profiles" / "Wiimote"
        prof.mkdir(parents=True, exist_ok=True)
        for n in range(players):
            f = prof / f"GameScraper Telefono {n + 1}.ini"
            f.write_text(ini_update("", {"Profile": wiimote_keys(n)}), encoding="utf-8")
            files.append(str(f))
        if apply_wiimotes:
            wm = base / "WiimoteNew.ini"
            ch = {f"Wiimote{n + 1}": {"Source": "1", **wiimote_keys(n)} for n in range(players)}
            backups.append(_write(wm, ch, set(ch)))
            files.append(str(wm))
        return {"files": files, "backups": [b for b in backups if b],
                "note": "In Dolphin il Wii Remote N usa il telefono N (giocatore N) con aspetto Wii Remote o Nunchuk: tasti, levetta e movimento."}
    raise ValueError("Configurazione sconosciuta")


def restore(what: str, emulators: dict) -> list[str]:
    """Rimette l'ultima copia fatta da GameScraper."""
    if what.startswith("pcsx2_"):
        files = [pcsx2_ini(emulators.get("pcsx2"))]
    elif what == "dolphin_wii":
        base = dolphin_dir(emulators.get("dolphin")) / "Config"
        files = [base / "DSUClient.ini", base / "WiimoteNew.ini"]
    else:
        raise ValueError("Configurazione sconosciuta")
    done = []
    for f in files:
        b = latest_backup(f)
        if b:
            shutil.copy2(b, f)
            done.append(str(f))
    if not done:
        raise RuntimeError("Nessuna copia di GameScraper da rimettere")
    return done
