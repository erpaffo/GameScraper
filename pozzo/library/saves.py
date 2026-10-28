"""Versioni dei salvataggi di un gioco: copie prima e dopo ogni partita, ripristino (rollback) e percorsi manuali.

Dove gli emulatori salvano (portatile accanto all'exe prima della versione installata):
- famiglia yuzu (Eden, yuzu, suyu, citron): nand/user/save/0000000000000000/<utente>/<TITLEID>/
- Ryujinx: bis/user/save/<n>/, con il Title ID nei primi 8 byte (little-endian) di ExtraData0
- RetroArch: <nome della ROM>.srm / .state* nelle cartelle saves e states (anche nelle sottocartelle per core)
- Dolphin: GC/<regione>/Card ?/<editore>-<ID4>-*.gci, Wii/title/00010000/<ID4 in esadecimale>/data, StateSaves/<ID6>.s??
  (le memory card .raw sono condivise da tutti i giochi)
- PCSX2: sstates/<seriale> (<crc>).<n>.p2s; memcards/*.ps2 condivise
- DuckStation: memcards/<nome o seriale>_<n>.mcd, savestates/<seriale>_<n>.sav; shared_card_*.mcd condivise
- PPSSPP: memstick/PSP/SAVEDATA/<ULUS12345>*, PSP/PPSSPP_STATE/<ULUS12345>*
- RPCS3: dev_hdd0/home/00000001/savedata/<TITLEID>*;  Vita3K: ux0/user/00/savedata/<TITLEID>
- melonDS: <ROM>.sav e <ROM>.ml? accanto alla ROM;  shadPS4: user/savedata/1/<CUSA...>
- Azahar / Lime3DS / Citra: sdmc/Nintendo 3DS/*/*/title/00040000/<id basso>/data, states/<TITLEID>.*
- Cemu: mlc01/usr/save/00050000/<id basso>;  Flycast: data/<ROM>_vmu_save_*.bin, <ROM>.nvmem*; vmu_save_*.bin condivise
- xemu (un unico disco qcow2) e Xenia (ID non ricavabile dal file): percorsi manuali o rilevati dalla partita.

Le copie sono nel formato di Ludusavi (vedi «versioni» sotto): il ripristino riscrive i file nei percorsi originali,
dopo aver salvato una copia dei salvataggi attuali. <cache>/saves/<hash della chiave>/index.json tiene percorsi
manuali, cartella del gioco tra le copie, motivo di ogni copia e i percorsi proposti dal rilevamento.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import struct
import sys
import time
import zipfile
from pathlib import Path

KEEP = 20  # versioni conservate per gioco (quelle fissate non contano)
YUZU = ("eden", "yuzu", "suyu", "citron")
CITRA = {"azahar": "Azahar", "lime3ds": "Lime3DS", "citra": "Citra"}

# emulatore: [(percorso relativo alla cartella dati, condiviso con altri giochi)]; {x} = ID del gioco (vedi game_ids)
RULES: dict[str, list[tuple[str, bool]]] = {
    **{e: [("nand/user/save/0000000000000000/*/{tid}", False)] for e in YUZU},
    "dolphin": [("GC/*/Card ?/*-{gid4}-*.gci", False), ("Wii/title/00010000/{gid4hex}/data", False),
                ("StateSaves/{gid}.s??", False), ("GC/MemoryCard?.*.raw", True), ("GC/*/MemoryCard?.*.raw", True)],
    "pcsx2": [("sstates/{serial} (*).*.p2s", False), ("memcards/*.ps2", True)],
    "duckstation": [("memcards/{stem}_?.mcd", False), ("memcards/{serial}_?.mcd", False),
                    ("savestates/{serial}_*.sav", False), ("memcards/shared_card_?.mcd", True)],
    "ppsspp": [("PSP/SAVEDATA/{serialnd}*", False), ("PSP/PPSSPP_STATE/{serialnd}*", False)],
    "rpcs3": [("dev_hdd0/home/00000001/savedata/{tid}*", False)],
    "vita3k": [("ux0/user/00/savedata/{tid}", False)],
    "melonds": [("{stem}.sav", False), ("{stem}.ml?", False)],
    **{e: [("sdmc/Nintendo 3DS/*/*/title/00040000/{lowid}/data", False), ("states/{tid}.*", False)] for e in CITRA},
    "cemu": [("mlc01/usr/save/00050000/{lowid}", False)],
    "flycast": [("{stem}_vmu_save_*.bin", False), ("{stem}.nvmem*", False), ("vmu_save_*.bin", True)],
    "shadps4": [("savedata/1/{tid}", False)],
}


def _appdata(*parts) -> Path | None:
    v = os.environ.get("APPDATA")
    return Path(v).joinpath(*parts) if v else None


def _docs(*parts) -> Path:
    from ..remote.emusetup import documents_dir
    return documents_dir().joinpath(*parts)


def data_roots(emulator: str, exe: str | None, rom_dir: str = "") -> list[Path]:
    """Cartelle dati dell'emulatore: versione portatile (accanto all'exe) prima di quella installata."""
    base = Path(exe).parent if exe else None
    e = emulator.lower()
    portable = bool(base and (base / "portable.txt").exists())
    if e in YUZU:
        c = [base / "user" if base else None, _appdata(e), Path.home() / ".local" / "share" / e]
    elif e == "ryujinx":
        c = [base / "portable" if base else None, _appdata("Ryujinx"), Path.home() / ".config" / "Ryujinx"]
    elif e in ("retroarch", "rpcs3"):
        c = [base]
    elif e == "dolphin":
        from ..remote.emusetup import dolphin_dir
        c = [dolphin_dir(exe)]
    elif e == "pcsx2":
        from ..remote.emusetup import pcsx2_ini
        c = [pcsx2_ini(exe).parent.parent]
    elif e == "duckstation":
        c = [base if portable else None, _docs("DuckStation")]
    elif e == "ppsspp":
        c = [base / "memstick" if base else None, _docs("PPSSPP")]
    elif e == "vita3k":
        c = [base if base and (base / "ux0").is_dir() else None, _appdata("Vita3K", "Vita3K")]
    elif e == "melonds":
        c = [Path(rom_dir) if rom_dir else None]
    elif e in CITRA:
        c = [base / "user" if base else None, _appdata(CITRA[e])]
    elif e == "cemu":
        c = [base if base and (base / "mlc01").is_dir() else None, _appdata("Cemu")]
    elif e == "flycast":
        c = [base / "data" if base else None, _appdata("flycast", "data")]
    elif e == "shadps4":
        c = [base / "user" if base else None, _appdata("shadPS4")]
    else:
        c = []
    return _uniq([r for r in c if r and r.is_dir()])


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
    return (emulator or "").lower() in (*RULES, "ryujinx", "retroarch")


def _uniq(paths: list[Path]) -> list[Path]:
    seen, out = set(), []
    for p in paths:
        k = os.path.normcase(str(p))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


# ------------------------------------------------------------ ID del gioco letti dal file della ROM
def _head(p: Path, n: int, at: int = 0) -> bytes:
    try:
        with open(p, "rb") as f:
            f.seek(at)
            return f.read(n)
    except OSError:
        return b""


def _sfo(p: Path) -> dict:
    """Chiavi testuali di un PARAM.SFO (PS3/PS4/Vita)."""
    b = _head(p, 1 << 16)
    if b[:4] != b"\0PSF":
        return {}
    keys, data, n = struct.unpack_from("<III", b, 8)
    out = {}
    for i in range(min(n, 64)):
        ko, fmt, ln, _, do = struct.unpack_from("<HHIII", b, 20 + i * 16)
        k = b[keys + ko:b.index(b"\0", keys + ko)].decode("ascii", "ignore")
        if fmt in (0x0204, 0x0004):
            out[k] = b[data + do:data + do + ln].split(b"\0")[0].decode("utf-8", "ignore")
    return out


def _near(rom: Path, *rels: str) -> Path | None:
    """Primo file `rels` nella cartella della ROM o in quelle sopra (ROM come cartella o come file al suo interno)."""
    for d in [rom, *list(rom.parents)[:3]]:
        for r in rels:
            if (d / r).is_file():
                return d / r
    return None


def game_ids(platform: str, rom: str | Path | None) -> dict:
    """ID usati dagli emulatori nei nomi dei salvataggi: gid (GC/Wii), serial (PS1/PS2/PSP), tid/lowid. Vuoto se ignoto."""
    if not rom:
        return {}
    rom = Path(rom)
    ext = rom.suffix.lower()
    out: dict = {}
    try:
        if platform in ("gc", "wii"):
            at = {".wbfs": 0x200, ".rvz": 0x58, ".wia": 0x58, ".ciso": 0x8000}.get(ext, 0)
            gid = _head(rom, 6, at).decode("ascii", "ignore")
            if re.fullmatch(r"[A-Z0-9]{6}", gid):
                out = {"gid": gid, "gid4": gid[:4], "gid4hex": gid[:4].encode().hex()}
        elif platform in ("psx", "ps2", "psp"):
            if ext == ".cue":  # il seriale sta nel primo .bin indicato dal cue
                m = re.search(r'FILE\s+"([^"]+)"', rom.read_text(encoding="utf-8", errors="ignore"))
                rom = rom.parent / m.group(1) if m else rom
            b = _head(rom, 4 << 20)
            m = (re.search(rb"([A-Z]{4})-(\d{5})\|[0-9A-F]{16}\|", b) if platform == "psp"
                 else re.search(rb"BOOT2?\s*=\s*cdrom0?:\\*([A-Z]{4})[_-](\d{3})\.(\d{2})", b))
            if m:
                code, num = m.group(1).decode(), b"".join(m.groups()[1:]).decode()
                out = {"serial": f"{code}-{num}", "serialnd": code + num}
        elif platform == "3ds":
            b = _head(rom, 0x200)
            tid = (struct.unpack_from("<Q", b, 0x108)[0] if b[0x100:0x104] == b"NCSD"
                   else struct.unpack_from("<Q", b, 0x118)[0] if b[0x100:0x104] == b"NCCH" else 0)
            if tid:
                out = {"tid": f"{tid:016X}"}
        elif platform == "wiiu":
            meta = _near(rom, "meta/meta.xml")
            m = meta and re.search(r"<title_id[^>]*>\s*([0-9A-Fa-f]{16})", meta.read_text(encoding="utf-8", errors="ignore"))
            if m:
                out = {"tid": m.group(1).upper()}
        elif platform in ("ps3", "ps4", "psvita"):
            sfo = _near(rom, "PS3_GAME/PARAM.SFO", "PARAM.SFO", "sce_sys/param.sfo", "sce_sys/PARAM.SFO")
            tid = _sfo(sfo).get("TITLE_ID", "") if sfo else ""
            m = tid or re.search(r"(?:[BN][CLP][A-Z]{2}\d{5}|CUSA\d{5}|PCS[A-H]\d{5})", str(rom).upper())
            if m:
                out = {"tid": tid or m.group(0)}
    except (OSError, ValueError, struct.error):
        return {}
    if out.get("tid"):
        out["lowid"] = out["tid"][-8:].lower()
    return out


def game_saves(emulator: str, exe: str | None, *, rom_stem: str = "", title_id: str = "", ids: dict | None = None,
               rom_dir: str = "") -> tuple[list[Path], list[Path]]:
    """(salvataggi del gioco, salvataggi condivisi con altri giochi) trovati nelle cartelle dell'emulatore."""
    e = (emulator or "").lower()
    v = {k: x for k, x in (ids or {}).items() if x}
    tid = (title_id or v.get("tid") or "").upper()
    if tid:
        v["tid"] = tid
        v.setdefault("lowid", tid[-8:].lower())
    if rom_stem:
        v["stem"] = rom_stem
    esc = {k: glob.escape(x) for k, x in v.items()}
    out: list[Path] = []
    shared: list[Path] = []
    for root in data_roots(e, exe, rom_dir):
        if e == "ryujinx" and tid:
            for d in (root / "bis" / "user" / "save").glob("*"):
                head = _head(d / "ExtraData0", 8)
                if len(head) == 8 and f"{struct.unpack('<Q', head)[0]:016X}" == tid:
                    out.append(d)
        elif e == "retroarch" and rom_stem:
            pre = rom_stem + "."
            for d in _retroarch_dirs(root):
                out += [f for f in d.rglob("*") if f.is_file() and f.name.startswith(pre)]
        for pat, sh in RULES.get(e, ()):
            try:
                rel = pat.format_map(esc)
            except KeyError:  # manca l'ID che serve a questo percorso
                continue
            (shared if sh else out).extend(root.glob(rel))
    return _uniq(out), _uniq(shared)


# ------------------------------------------------------------ indice per gioco (percorsi manuali, versioni fissate, note)
def folder(cfg, key: str) -> Path:
    return Path(cfg.cache) / "saves" / hashlib.sha1(key.encode()).hexdigest()[:16]


def _index(cfg, key: str) -> dict:
    try:
        return json.loads((folder(cfg, key) / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_index(cfg, key: str, idx: dict):
    d = folder(cfg, key)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "index.json.part"
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / "index.json")


def _protected(cfg) -> list[Path]:
    """Cartelle che non possono essere «salvataggi»: troppo grandi, di sistema, o dell'app stessa."""
    env = [os.environ.get(k) for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "ProgramFiles", "ProgramFiles(x86)",
                                       "SystemRoot", "ProgramData")]
    local = os.environ.get("LOCALAPPDATA")
    out = [Path.home(), _docs(), Path.home() / "Saved Games", Path(cfg.cache), Path(__file__).resolve().parents[2],
           *(Path(x) for x in env if x), *([Path(local).parent / "LocalLow"] if local else [])]
    return [p.resolve() for p in out]


def _expand(raw: str) -> Path:
    """%VAR% (stile Windows) e $VAR su ogni sistema: fuori da Windows expandvars conosce solo $VAR."""
    s = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), str(raw).strip().strip('"'))
    return Path(os.path.expandvars(s)).expanduser()


def check_path(cfg, raw: str) -> Path:
    """Percorso manuale valido (le variabili come %APPDATA% si espandono), altrimenti ValueError con il motivo."""
    p = _expand(raw)
    if not p.is_absolute():
        raise ValueError(f"Serve un percorso completo: {raw}")
    if not p.exists():
        raise ValueError(f"Percorso non trovato: {raw}")
    p = p.resolve()
    if p == Path(p.anchor) or any(p == x or p in x.parents for x in _protected(cfg)):
        raise ValueError(f"Cartella troppo generica, scegli quella dei salvataggi del gioco: {p}")
    if any(x in (p, *p.parents) for x in (Path(cfg.cache).resolve(), backup_root(cfg).resolve())):
        raise ValueError("Questa e' la cartella delle copie dell'app")
    return p


def manual_paths(cfg, key: str) -> list[str]:
    return list(_index(cfg, key).get("paths") or [])


def resolved_manual(cfg, key: str) -> list[Path]:
    """Percorsi manuali che esistono su questo PC (gli altri si tengono, magari tornano: disco esterno, altro PC)."""
    out = []
    for raw in manual_paths(cfg, key):
        p = _expand(raw)
        if p.exists():
            out.append(p)
    return out


def set_paths(cfg, key: str, paths: list[str]) -> list[str]:
    for raw in paths:
        check_path(cfg, raw)
    idx = _index(cfg, key)
    idx["paths"] = list(dict.fromkeys(str(x).strip().strip('"') for x in paths if str(x).strip()))
    _write_index(cfg, key, idx)
    return idx["paths"]


# ------------------------------------------------------------ versioni, nel formato di Ludusavi
# <cartella delle copie>/<nome del gioco>/mapping.yaml + backup-<AAAAMMGGThhmmssZ>.zip, con i file nello zip come
# drive-C/Users/... e in mapping.yaml il percorso originale, l'hash SHA-1 e la dimensione di ognuno. Con la stessa
# cartella, Ludusavi vede e ripristina le copie dell'app, e l'app quelle di Ludusavi (copie complete; le differenziali
# di Ludusavi non si mostrano). Quello che Ludusavi non conosce (motivo della copia, cartelle copiate intere per il
# rollback esatto, punto «prima della partita») sta nell'index.json del gioco, fuori dalla cartella delle copie.
_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RISKY = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".lnk", ".scr", ".com", ".msi", ".reg"}  # mai ripristinati
_DRIVE = re.compile(r"^([A-Za-z]:)/")
_OLD = re.compile(r"^\d{8}-\d{6}-.+\.zip$")  # copie del formato precedente (zip con manifest.json)


def backup_root(cfg) -> Path:
    """Cartella delle copie: quella scelta in Impostazioni (es. la cartella di Ludusavi) o <cache>/saves."""
    return Path((getattr(cfg, "app", None) or {}).get("save_dir") or Path(cfg.cache) / "saves")


def escape_name(name: str) -> str:
    """Nome della cartella del gioco come lo calcola Ludusavi (escape_folder_name)."""
    s = re.sub(r"^\.|\.$", "_", _INVALID.sub("_", name.strip()))
    if not s.strip("_"):
        import base64
        s = "ludusavi-renamed-" + base64.b64encode(name.encode()).decode().replace("/", "_")
    return s


def _mkey(p: Path) -> str:
    """Percorso come chiave di mapping.yaml: C:\\Users\\a.sav -> C:/Users/a.sav."""
    return str(p.resolve()).replace("\\", "/")


def _entry(drives: dict, key: str) -> str:
    """Voce dello zip per una chiave: C:/Users/a.sav -> drive-C/Users/a.sav (drive-0 per i percorsi senza unita')."""
    m = _DRIVE.match(key)
    drive, rest = (m.group(1), key[3:]) if m else ("", key.lstrip("/"))
    folder = next((k for k, v in drives.items() if v == drive), None)
    return f"{folder or ('drive-' + escape_name(drive.replace(':', '')) if drive else 'drive-0')}/{rest}"


def _sha1(f: Path) -> str:
    h = hashlib.sha1()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _when(v) -> float:
    """Data di una copia (stringa RFC 3339 o datetime, come la legge PyYAML) in secondi."""
    import datetime as dt
    if isinstance(v, dt.datetime):
        return (v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)).timestamp()
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _game_dir(cfg, key: str, title: str | None = None) -> Path | None:
    """Cartella del gioco tra le copie; con title la crea (la prima volta) e la ricorda nell'indice."""
    idx = _index(cfg, key)
    if idx.get("dir"):
        return backup_root(cfg) / idx["dir"]
    if not title:
        return None
    idx["dir"] = escape_name(title)
    _write_index(cfg, key, idx)
    return backup_root(cfg) / idx["dir"]


def _mapping(d: Path | None, name: str = "") -> dict:
    import yaml
    try:
        m = yaml.safe_load((d / "mapping.yaml").read_text(encoding="utf-8")) if d else None
    except (OSError, yaml.YAMLError):
        m = None
    m = m if isinstance(m, dict) else {}
    m.setdefault("name", name)
    m["drives"] = m.get("drives") or {}
    m["backups"] = [b for b in m.get("backups") or [] if isinstance(b, dict) and b.get("name")]
    return m


def _write_mapping(d: Path, m: dict):
    import yaml
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "mapping.yaml.part"
    tmp.write_text(yaml.safe_dump(m, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(d / "mapping.yaml")


def _os() -> str:
    return "windows" if os.name == "nt" else "mac" if sys.platform == "darwin" else "linux"


def _store(cfg, key: str, title: str, files: dict[str, tuple[Path | None, bytes | None]], reason: str, dirs: list[str],
           when: float | None = None, protect: str = "") -> Path:
    """Scrive una copia (file presi dal disco o gia' in memoria) e la aggiunge a mapping.yaml e all'indice."""
    d = _game_dir(cfg, key, title)
    m = _mapping(d, title)
    when = when or time.time()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(when))
    names = {b["name"] for b in m["backups"]}
    name, n = f"backup-{stamp}.zip", 1
    while name in names or (d / name).exists():  # due copie nello stesso secondo
        name, n = f"backup-{stamp}-{n}.zip", n + 1
    meta = {}
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for k, (src, data) in sorted(files.items()):
            mk = _DRIVE.match(k)
            drive = mk.group(1) if mk else ""
            folder = "drive-" + escape_name(drive.replace(":", "")) if drive else "drive-0"
            m["drives"].setdefault(folder, drive)
            if data is None:
                z.write(src, _entry(m["drives"], k))
                meta[k] = {"hash": _sha1(src), "size": src.stat().st_size}
            else:
                z.writestr(_entry(m["drives"], k), data)
                meta[k] = {"hash": hashlib.sha1(data).hexdigest(), "size": len(data)}
    tmp.replace(d / name)
    m["backups"].append({"name": name, "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when)), "os": _os(),
                         "files": meta, "registry": {"hash": None}, "children": []})
    _write_mapping(d, m)
    idx = _index(cfg, key)
    idx.setdefault("backups", {})[name] = {"reason": reason, "dirs": dirs}
    _write_index(cfg, key, idx)
    keep = int((getattr(cfg, "app", None) or {}).get("save_keep") or KEEP)
    for old in [b for b in list_backups(cfg, key) if not b["pinned"] and b["name"] != protect][keep:]:
        try:
            delete(cfg, key, old["name"])
        except OSError:  # file aperto: si toglie la prossima volta
            pass
    return d / name


_MIGRATING: set[str] = set()


def _migrate(cfg, key: str):
    """Converte le copie del formato precedente (zip con manifest.json) nel formato di Ludusavi, una volta sola."""
    old_dir = folder(cfg, key)
    olds = sorted(p for p in old_dir.glob("*.zip") if _OLD.match(p.name)) if old_dir.is_dir() else []
    if not olds or key in _MIGRATING:  # _store e set_meta, chiamati qui sotto, rientrano qui
        return
    _MIGRATING.add(key)
    try:
        _convert(cfg, key, olds)
    finally:
        _MIGRATING.discard(key)


def _convert(cfg, key: str, olds: list[Path]):
    for p in olds:
        try:
            with zipfile.ZipFile(p) as z:
                man = json.loads(z.read("manifest.json"))
                if man.get("key") != key:
                    continue
                items = {it["arc"]: it for it in man.get("items") or []}
                files = {}
                for mname in z.namelist():
                    arc, _, rel = mname.partition("/")
                    it = items.get(arc)
                    if not it or not rel or mname.endswith("/"):
                        continue
                    base = Path(it["path"]).resolve()
                    dest = (base / rel).resolve()
                    if base in dest.parents:
                        files[_mkey(dest)] = (None, z.read(mname))
            if files:
                idx = _index(cfg, key)
                new = _store(cfg, key, man.get("title") or key, files, man.get("reason") or "",
                             [it["path"] for it in items.values() if it.get("dir")], when=man.get("time"))
                if p.name in (idx.get("pins") or []) or (idx.get("notes") or {}).get(p.name):
                    set_meta(cfg, key, new.name, pinned=p.name in (idx.get("pins") or []) or None,
                             note=(idx.get("notes") or {}).get(p.name))
                if idx.get("session") == p.name:
                    idx = _index(cfg, key)
                    idx["session"] = new.name
                    _write_index(cfg, key, idx)
            p.unlink()
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue  # copia illeggibile: resta li', non si perde nulla


def list_backups(cfg, key: str) -> list[dict]:
    _migrate(cfg, key)
    d = _game_dir(cfg, key)
    if not d:
        return []
    info = _index(cfg, key).get("backups") or {}
    out = []
    for b in _mapping(d)["backups"]:
        p = d / b["name"]
        size = p.stat().st_size if p.is_file() else sum(int((x or {}).get("size") or 0) for x in (b.get("files") or {}).values())
        out.append({"name": b["name"], "size": size, "time": _when(b.get("when")),
                    "reason": (info.get(b["name"]) or {}).get("reason") or "ludusavi",
                    "pinned": bool(b.get("locked")), "note": b.get("comment") or ""})
    return sorted(out, key=lambda x: -x["time"])


def backup(cfg, key: str, title: str, paths: list[Path], reason: str = "manuale", name: str | None = None,
           protect: str = "") -> Path | None:
    """Copia dei salvataggi indicati; None se non c'e' nulla da copiare. Se il contenuto e' uguale all'ultima versione
    non si crea un doppione: ritorna quella. name = nome del gioco per Ludusavi (predefinito: il titolo)."""
    _migrate(cfg, key)
    files: dict[str, tuple[Path, None]] = {}
    dirs = []
    for p in paths:
        if p.is_dir():
            dirs.append(str(p))
            files.update((_mkey(f), (f, None)) for f in p.rglob("*") if f.is_file())
        elif p.is_file():
            files[_mkey(p)] = (p, None)
    if not files:
        return None
    d = _game_dir(cfg, key, name or title)
    last = list_backups(cfg, key)
    if last:
        prev = next((b for b in _mapping(d)["backups"] if b["name"] == last[0]["name"]), {})
        pf = {k: v or {} for k, v in (prev.get("files") or {}).items()}
        if set(pf) == set(files) and all(pf[k].get("size") == f.stat().st_size and pf[k].get("hash") == _sha1(f)
                                         for k, (f, _) in files.items()):
            return _mark(cfg, key, d / last[0]["name"], reason)
    return _mark(cfg, key, _store(cfg, key, name or title, files, reason, dirs, protect=protect), reason)


def _mark(cfg, key: str, out: Path, reason: str) -> Path:
    """La copia fatta all'avvio e' il punto a cui torna «Annulla l'ultima partita» (anche se uguale a una precedente)."""
    if reason == "prima-della-partita":
        idx = _index(cfg, key)
        idx["session"] = out.name
        _write_index(cfg, key, idx)
    return out


def session_point(cfg, key: str) -> str | None:
    """Versione da ripristinare per annullare l'ultima partita, se esiste ancora."""
    name = _index(cfg, key).get("session")
    return name if name and any(b["name"] == name for b in list_backups(cfg, key)) else None


def _open(cfg, key: str, name: str) -> tuple[Path, dict]:
    """(cartella del gioco, voce della copia in mapping.yaml)."""
    _migrate(cfg, key)
    d = _game_dir(cfg, key)
    b = next((x for x in _mapping(d)["backups"] if x["name"] == name), None) if d else None
    if not b or (name != "." and Path(name).name != name):
        raise FileNotFoundError(f"Copia non trovata: {name}")
    return d, b


def _members(d: Path, b: dict, drives: dict):
    """(funzione che legge il file della copia, percorso di destinazione, {hash, size}). I tipi eseguibili si saltano:
    mapping.yaml puo' arrivare da fuori (Ludusavi, altro PC) e indicare qualunque percorso."""
    zf = zipfile.ZipFile(d / b["name"]) if b["name"].endswith(".zip") else None
    base = d if b["name"] == "." else d / b["name"]
    try:
        for k, meta in (b.get("files") or {}).items():
            dest = Path(k)
            if not dest.is_absolute() or dest.suffix.lower() in _RISKY or ".." in dest.parts:
                continue
            e = _entry(drives, k)
            yield (lambda e=e: zf.read(e)) if zf else (lambda e=e: (base / e).read_bytes()), dest, meta or {}
    finally:
        if zf:
            zf.close()


def _extra(dirs: list[str], inside: set[str]) -> list[Path]:
    """File presenti adesso nelle cartelle copiate intere ma non nella copia (creati dopo)."""
    return [f for x in dirs for f in Path(x).rglob("*") if f.is_file() and os.path.normcase(str(f.resolve())) not in inside]


def diff(cfg, key: str, name: str) -> dict:
    """Cosa cambierebbe ripristinando la copia: file diversi, mancanti sul disco, e in piu' (che verrebbero tolti)."""
    d, b = _open(cfg, key, name)
    changed, missing, same, inside = [], [], 0, set()
    for _, dest, meta in _members(d, b, _mapping(d)["drives"]):
        inside.add(os.path.normcase(str(dest.resolve())))
        if not dest.is_file():
            missing.append(str(dest))
        elif dest.stat().st_size != meta.get("size") or _sha1(dest) != meta.get("hash"):
            changed.append(str(dest))
        else:
            same += 1
    dirs = ((_index(cfg, key).get("backups") or {}).get(name) or {}).get("dirs") or []
    return {"changed": changed, "missing": missing, "extra": [str(f) for f in _extra(dirs, inside)], "same": same}


def restore(cfg, key: str, name: str) -> dict:
    """Riporta i salvataggi alla copia scelta, nei percorsi originali. Nelle cartelle copiate intere si tolgono anche
    i file creati dopo, cosi' il rollback e' esatto. Prima salva una copia dei salvataggi attuali (annullabile)."""
    d, b = _open(cfg, key, name)
    m = _mapping(d)
    dirs = [x for x in ((_index(cfg, key).get("backups") or {}).get(name) or {}).get("dirs") or [] if Path(x).is_dir()]
    members = list(_members(d, b, m["drives"]))
    cur_files = [dest for _, dest, _ in members if dest.is_file()
                 and not any(Path(x).resolve() in dest.resolve().parents for x in dirs)]
    safety = backup(cfg, key, m["name"], [Path(x) for x in dirs] + cur_files, "prima-del-ripristino", protect=name)
    inside = {os.path.normcase(str(dest.resolve())) for _, dest, _ in members}
    removed = 0
    for f in _extra(dirs, inside):
        f.unlink(missing_ok=True)
        removed += 1
    written = 0
    for read, dest, _ in _members(d, b, m["drives"]):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(read())
        written += 1
    return {"written": written, "removed": removed, "safety_copy": safety.name if safety else None}


def delete(cfg, key: str, name: str):
    d = _game_dir(cfg, key)
    if not d:
        return
    m = _mapping(d)
    if not any(b["name"] == name for b in m["backups"]) or (name != "." and Path(name).name != name):
        return
    import shutil
    p = d / name
    if name == ".":  # copia «semplice» unica di Ludusavi: le cartelle drive-* accanto a mapping.yaml
        for x in d.glob("drive-*"):
            shutil.rmtree(x, ignore_errors=True)
    elif p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink(missing_ok=True)
    m["backups"] = [b for b in m["backups"] if b["name"] != name]
    _write_mapping(d, m)
    idx = _index(cfg, key)
    (idx.get("backups") or {}).pop(name, None)
    if idx.get("session") == name:
        idx.pop("session")
    _write_index(cfg, key, idx)


def set_meta(cfg, key: str, name: str, pinned: bool | None = None, note: str | None = None):
    """Versione fissata = «locked» di Ludusavi (mai cancellata), nota = «comment»."""
    d, _ = _open(cfg, key, name)
    m = _mapping(d)
    b = next(x for x in m["backups"] if x["name"] == name)
    if pinned is not None:
        if pinned:
            b["locked"] = True
        else:
            b.pop("locked", None)
    if note is not None:
        if note.strip():
            b["comment"] = note.strip()[:200]
        else:
            b.pop("comment", None)
    _write_mapping(d, m)


# ------------------------------------------------------------ rilevamento: file cambiati durante la partita
_SKIP_DIR = re.compile(r"log|cache|shader|crash|dump|screenshot|temp|tmp|telemetry|nvidia|amd|intel|microsoft|google|"
                       r"mozilla|chrome|edge|discord|steam|epic|gog|origin|ubisoft|battle\.net|onedrive|packages|"
                       r"pozzo|moonlight|sunshine|\.git|node_modules|__pycache__", re.I)
_SKIP_EXT = {".log", ".tmp", ".dmp", ".etl", ".lock", ".pdb", ".exe", ".dll", ".png", ".jpg", ".bmp", ".webp", ".mp4",
             ".pyc", ".lnk", ".ini~"}


def changed_since(roots: list[Path], t0: float, exclude: list[Path] = (), *, max_seconds: float = 15,
                  max_files: int = 200_000) -> list[Path]:
    """Cartelle con file modificati da t0, quelle con piu' file per prime. Scansione limitata nel tempo e nei file."""
    stop = time.time() + max_seconds
    skip = {os.path.normcase(str(x)) for x in exclude}
    count: dict[str, int] = {}
    seen = 0
    stack = [str(r) for r in roots if r and Path(r).is_dir()]
    while stack and time.time() < stop and seen < max_files:
        d = stack.pop()
        if os.path.normcase(d) in skip:
            continue
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not _SKIP_DIR.search(e.name):
                                stack.append(e.path)
                        elif (os.path.splitext(e.name)[1].lower() not in _SKIP_EXT
                              and e.stat().st_mtime >= t0):
                            count[d] = count.get(d, 0) + 1
                    except OSError:
                        continue
        except OSError:
            continue
    return [Path(d) for d, _ in sorted(count.items(), key=lambda x: -x[1])]


def suggest(cfg, key: str, found: list[Path]) -> list[str]:
    """Registra i percorsi proposti (al massimo 5), senza quelli ignorati, gia' scelti o non validi."""
    idx = _index(cfg, key)
    known = {os.path.normcase(x) for x in (idx.get("ignored") or []) + (idx.get("paths") or [])}
    out = []
    for p in found:
        try:
            s = str(check_path(cfg, str(p)))
        except ValueError:
            continue
        if os.path.normcase(s) not in known and s not in out:
            out.append(s)
    idx["suggested"] = out[:5]
    _write_index(cfg, key, idx)
    return idx["suggested"]


def suggestions(cfg, key: str) -> list[str]:
    return list(_index(cfg, key).get("suggested") or [])


def answer_suggestion(cfg, key: str, path: str, accept: bool):
    """Proposta confermata (diventa un percorso manuale) o scartata (non si ripropone)."""
    idx = _index(cfg, key)
    if path not in (idx.get("suggested") or []):
        raise ValueError("Proposta non trovata")
    if accept:
        set_paths(cfg, key, manual_paths(cfg, key) + [path])
        idx = _index(cfg, key)
    else:
        idx["ignored"] = list(dict.fromkeys((idx.get("ignored") or []) + [path]))
    idx["suggested"] = [x for x in idx.get("suggested") or [] if x != path]
    _write_index(cfg, key, idx)
