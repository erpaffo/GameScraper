"""Copie di sicurezza dei salvataggi di un gioco, prese dalle cartelle dell'emulatore.

Supportati (salvataggi riconoscibili per singolo gioco):
- famiglia yuzu (Eden, yuzu, suyu, citron): <dati>/nand/user/save/0000000000000000/<utente>/<TITLEID>/
- Ryujinx: <dati>/bis/user/save/<n>/, con il Title ID nei primi 8 byte (little-endian) di ExtraData0
- RetroArch: file <nome della ROM>.srm / .state* nelle cartelle saves e states (anche nelle sottocartelle per core)

Ogni copia e' uno zip in <cache>/saves/<gioco>/ con un manifest.json che ricorda il percorso originale di ogni
elemento: il ripristino riscrive i file solo li', dopo aver salvato una copia dei salvataggi attuali.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
import zipfile
from pathlib import Path

KEEP = 10  # copie conservate per gioco
YUZU = ("eden", "yuzu", "suyu", "citron")


def _appdata(*parts) -> Path | None:
    v = os.environ.get("APPDATA")
    return Path(v).joinpath(*parts) if v else None


def _roots(emulator: str, exe: str | None) -> list[Path]:
    """Cartelle dati dell'emulatore: versione portatile (accanto all'exe) prima di quella installata."""
    base = Path(exe).parent if exe else None
    e = emulator.lower()
    if e in YUZU:
        c = [base / "user" if base else None, _appdata(e), Path.home() / ".local" / "share" / e]
    elif e == "ryujinx":
        c = [base / "portable" if base else None, _appdata("Ryujinx"), Path.home() / ".config" / "Ryujinx"]
    elif e == "retroarch":
        c = [base]
    else:
        c = []
    return [r for r in c if r and r.is_dir()]


def _retroarch_dirs(root: Path) -> list[Path]:
    out = [root / "saves", root / "states"]
    cfgf = root / "retroarch.cfg"
    if cfgf.is_file():
        text = cfgf.read_text(encoding="utf-8", errors="ignore")
        for k in ("savefile_directory", "savestate_directory"):
            m = re.search(rf'^{k}\s*=\s*"([^"]*)"', text, re.M)
            if m and m.group(1) and m.group(1) != "default":
                v = m.group(1)
                out.append(Path(str(root) + v[1:]) if v.startswith(":") else Path(v))  # ":\saves" = relativo all'exe
    return [d for d in out if d.is_dir()]


def supported(emulator: str) -> bool:
    return (emulator or "").lower() in (*YUZU, "ryujinx", "retroarch")


def game_saves(emulator: str, exe: str | None, *, rom_stem: str = "", title_id: str = "") -> list[Path]:
    """Cartelle o file dei salvataggi di un gioco. Vuoto se l'emulatore non e' supportato o non ce ne sono."""
    e = (emulator or "").lower()
    tid = (title_id or "").upper()
    out: list[Path] = []
    for root in _roots(e, exe):
        if e in YUZU and tid:
            out += [p for p in (root / "nand" / "user" / "save" / "0000000000000000").glob(f"*/{tid}") if p.is_dir()]
        elif e == "ryujinx" and tid:
            for d in (root / "bis" / "user" / "save").glob("*"):
                x = d / "ExtraData0"
                try:
                    with open(x, "rb") as f:
                        head = f.read(8)
                except OSError:
                    continue
                if len(head) == 8 and f"{struct.unpack('<Q', head)[0]:016X}" == tid:
                    out.append(d)
        elif e == "retroarch" and rom_stem:
            pre = rom_stem + "."
            for d in _retroarch_dirs(root):
                out += [f for f in d.rglob("*") if f.is_file() and f.name.startswith(pre)]
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(str(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def folder(cfg, key: str) -> Path:
    return Path(cfg.cache) / "saves" / hashlib.sha1(key.encode()).hexdigest()[:16]


def list_backups(cfg, key: str) -> list[dict]:
    d = folder(cfg, key)
    if not d.is_dir():
        return []
    out = [{"name": p.name, "size": p.stat().st_size, "time": p.stat().st_mtime} for p in d.glob("*.zip")]
    return sorted(out, key=lambda x: -x["time"])


def backup(cfg, key: str, title: str, paths: list[Path], reason: str = "manuale") -> Path | None:
    """Zip con i salvataggi indicati; None se non c'e' nulla da copiare."""
    files: list[tuple[Path, str]] = []
    manifest = {"key": key, "title": title, "time": time.time(), "items": []}
    for i, p in enumerate(paths):
        arc = f"{i:02d}"
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    files.append((f, f"{arc}/{f.relative_to(p).as_posix()}"))
            manifest["items"].append({"arc": arc, "path": str(p), "dir": True})
        elif p.is_file():
            files.append((p, f"{arc}/{p.name}"))
            manifest["items"].append({"arc": arc, "path": str(p.parent), "dir": False})
    if not files:
        return None
    d = folder(cfg, key)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{reason}.zip"
    n = 1
    while out.exists():  # due copie nello stesso secondo
        out = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{reason}-{n}.zip"
        n += 1
    tmp = out.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        for f, arc in files:
            z.write(f, arc)
    tmp.replace(out)
    for old in list_backups(cfg, key)[KEEP:]:
        (d / old["name"]).unlink(missing_ok=True)
    return out


def restore(cfg, key: str, name: str) -> dict:
    """Riscrive i salvataggi della copia scelta nei percorsi originali (solo li': niente percorsi dal contenuto dello zip).
    Prima salva una copia dei salvataggi attuali, cosi' il ripristino si puo' annullare."""
    p = folder(cfg, key) / Path(name).name
    if not p.is_file() or not zipfile.is_zipfile(p):
        raise FileNotFoundError(f"Copia non trovata: {name}")
    with zipfile.ZipFile(p) as z:
        man = json.loads(z.read("manifest.json"))
        if man.get("key") != key:
            raise ValueError("La copia appartiene a un altro gioco")
        items = {it["arc"]: it for it in man["items"]}
        current = [Path(it["path"]) if it["dir"] else None for it in man["items"]]
        cur_files = [Path(it["path"]) / Path(m).name for m in z.namelist() if m != "manifest.json"
                     for it in [items.get(m.split("/", 1)[0])] if it and not it["dir"]]
        safety = backup(cfg, key, man.get("title", ""), [c for c in current if c] + [f for f in cur_files if f.exists()],
                        "prima-del-ripristino")
        written = 0
        for m in z.namelist():
            if m == "manifest.json" or m.endswith("/"):
                continue
            arc, _, rel = m.partition("/")
            it = items.get(arc)
            if not it or not rel:
                continue
            base = Path(it["path"]).resolve()
            dest = (base / rel).resolve()
            if base not in dest.parents:  # rel con ".." o assoluto: ignorato
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(m))
            written += 1
    return {"written": written, "safety_copy": safety.name if safety else None}
