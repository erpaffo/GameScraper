"""Scansione cartelle ROM e calcolo hash.

Ottimizzazioni rispetto a Skyscraper originale (che leggeva i file a blocchi da 1 KB,
calcolando sempre MD5+SHA1+CRC in un solo thread per gioco):
- le cartelle console sono cercate anche in profondita' (es. Myrient/No-Intro/<set>)
- la scansione NON legge i file: l'hash si calcola solo dopo aver provato l'identificazione
  per nome esatto sui DAT No-Intro/Redump (vedi pipeline), quindi per collezioni gia'
  nominate correttamente non si legge quasi nulla
- ZIP: il CRC32 del file interno si legge dalla central directory, senza decomprimere
- hashing adattato al disco: su HDD i file grandi si leggono uno alla volta in ordine
  (niente salti della testina), su SSD in parallelo su tutti i core
- risultati memorizzati in cache per (path, size, mtime): non si rilegge mai un file invariato
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import zipfile
import zlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import platforms as P

CHUNK = 8 * 1024 * 1024
LIGHT_MAX = 32 * 1024 * 1024  # file sotto questa soglia (o ZIP) = lavoro "leggero"
SKIP_DIRS = {"media", "images", "videos", "manuals", "downloaded_images", "snap", "boxart"}
MAX_DEPTH = 4


@dataclass
class Game:
    path: Path
    platform: P.Platform
    size: int
    mtime: float
    is_dir: bool = False
    crc: str = ""
    crc_nh: str = ""  # CRC senza header (NES/Lynx/7800) per i DAT No-Intro
    md5: str = ""
    sha1: str = ""
    inner_name: str = ""
    inner_size: int = 0
    # dati presi dal DAT quando il gioco e' identificato per nome (senza leggere il file)
    dat_crc: str = ""
    dat_md5: str = ""
    dat_sha1: str = ""
    dat_size: int = 0
    needs_hash: bool = False
    # risultati
    data: dict[str, object] = field(default_factory=dict)  # field -> valore unito
    origin: dict[str, str] = field(default_factory=dict)  # field -> sorgente
    canonical: str = ""  # nome No-Intro/Redump identificato offline

    @property
    def stem(self) -> str:
        return self.path.name if self.is_dir else self.path.stem

    @property
    def rom_size(self) -> int:
        return self.inner_size or self.dat_size or self.size

    @property
    def best_crc(self) -> str:
        return self.crc or self.dat_crc

    @property
    def key(self) -> str:
        # La chiave usa solo hash letti davvero dal file, cosi' resta stabile tra le esecuzioni
        if self.crc:
            ident = f"{self.crc}:{self.inner_size or self.size}"
        else:
            ident = hashlib.sha1(f"{self.stem.lower()}:{self.size}".encode()).hexdigest()[:16]
        return f"{self.platform.id}/{ident}"

    @property
    def search_name(self) -> str:
        return clean_title(self.canonical or self.stem)

    def missing(self, wanted: set[str]) -> set[str]:
        return {w for w in wanted if not self.data.get(w)}

    def apply_file_data(self, d: dict):
        for k, v in d.items():
            if hasattr(self, k) and k not in ("data", "origin", "path", "platform"):
                setattr(self, k, v)


_TAGS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


# nomi in stile "scene": Sekiro.Shadows.Die.Twice.PS4-CUSA12047, Game_Name.PS4.MULTi.v1.04-GRP
_SERIALS = r"(?:CUSA|PPSA|PCSE|PCSB|PCSA|PCSG|BLES|BLUS|BCES|BCUS|NPEB|NPUB)"
_SCENE_ID = re.compile(r"[\s._-]*(?:PS[345]|PSV|NSW|SWITCH)?[\s._-]*" + _SERIALS + r"[\s._-]?\d{5}.*$", re.I)
_SCENE_TAIL = re.compile(r"[\s._-]+(?:PS[345]|PSV|NSW|MULTi\d*|REPACK|PROPER|INTERNAL|FW\d[\d.]*|v\d+(?:\.\d+)+"
                         r"|UPDATE|BACKPORT)(?![a-z0-9]).*$", re.I)


def clean_scene(name: str) -> str:
    """'Sekiro.Shadows.Die.Twice.PS4-CUSA12047' -> 'Sekiro Shadows Die Twice'.
    I codici tra parentesi, es. 'Persona 4 Golden (PCSE00812)', li toglie gia' clean_title."""
    t = _TAGS.sub("", name).strip()
    scene = (" " not in t and t.count(".") >= 2) or bool(_SCENE_ID.search(t))
    if not scene:
        return name
    if " " not in t:
        t = t.replace(".", " ")
    t = _SCENE_ID.sub("", t)
    t = _SCENE_TAIL.sub("", t)
    return t.strip(" ._-") or name


def scene_serial(name: str) -> str:
    """Codice prodotto nel nome (CUSA12047, PCSE00812...), se presente."""
    m = re.search(r"(" + _SERIALS + r")[\s._-]?(\d{5})", name, re.I)
    return f"{m.group(1).upper()}{m.group(2)}" if m else ""


def clean_title(name: str) -> str:
    """'Legend of Zelda, The - A Link to the Past (USA) [!]' -> 'The Legend of Zelda - A Link to the Past'"""
    t = _TAGS.sub("", clean_scene(name)).strip()
    m = re.match(r"^(.*?), (The|A|An|Il|La|Le|Les|Der|Die|Das)( - .*|: .*)?$", t)
    if m:
        t = f"{m.group(2)} {m.group(1)}{m.group(3) or ''}"
    return re.sub(r"\s+", " ", t.replace("_", " ")).strip()


REGION_TAGS = {
    "usa": "us", "us": "us", "europe": "eu", "eu": "eu", "japan": "jp", "jp": "jp", "world": "wor",
    "italy": "it", "france": "fr", "germany": "de", "spain": "sp", "uk": "uk", "korea": "kr",
    "australia": "au", "brazil": "br", "asia": "asi", "china": "cn",
}


def regions_of(name: str) -> list[str]:
    out = []
    for grp in re.findall(r"\(([^)]*)\)", name):
        for part in re.split(r",\s*", grp):
            r = REGION_TAGS.get(part.strip().lower())
            if r and r not in out:
                out.append(r)
    return out


# ---------------------------------------------------------------- hashing

def _hash_job(args: tuple[str, str, bool, int, int]) -> dict:
    path, ext, full, max_bytes, header = args
    out: dict = {}
    try:
        if ext == "zip":
            with zipfile.ZipFile(path) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                if infos:
                    i = max(infos, key=lambda x: x.file_size)
                    out["crc"] = f"{i.CRC:08X}"
                    out["inner_name"] = i.filename
                    out["inner_size"] = i.file_size
                    if full and i.file_size <= max_bytes:
                        md5, sha1 = hashlib.md5(), hashlib.sha1()
                        with z.open(i) as f:
                            while b := f.read(CHUNK):
                                md5.update(b)
                                sha1.update(b)
                        out["md5"], out["sha1"] = md5.hexdigest().upper(), sha1.hexdigest().upper()
            return out
        if os.path.getsize(path) > max_bytes:
            return out
        crc = crc_nh = 0
        md5 = hashlib.md5() if full else None
        sha1 = hashlib.sha1() if full else None
        first = True
        with open(path, "rb", buffering=0) as f:
            while b := f.read(CHUNK):
                crc = zlib.crc32(b, crc)
                if header:
                    crc_nh = zlib.crc32(b[header:] if first else b, crc_nh)
                if md5:
                    md5.update(b)
                    sha1.update(b)
                first = False
        out["crc"] = f"{crc & 0xFFFFFFFF:08X}"
        if header:
            out["crc_nh"] = f"{crc_nh & 0xFFFFFFFF:08X}"
        if md5:
            out["md5"], out["sha1"] = md5.hexdigest().upper(), sha1.hexdigest().upper()
    except (OSError, zipfile.BadZipFile) as e:
        out["error"] = str(e)
    return out


_DISK_CACHE: dict[str, str] = {}


def disk_kind(path: Path, setting: str = "auto") -> str:
    """'hdd' o 'ssd'. Su Windows lo chiede a PowerShell (una volta per lettera di unita')."""
    if setting in ("hdd", "ssd"):
        return setting
    drive = Path(path).resolve().drive.rstrip(":").upper()
    if drive in _DISK_CACHE:
        return _DISK_CACHE[drive]
    kind = "hdd"  # nel dubbio, la strategia HDD e' quella che fa meno danni
    if sys.platform == "win32" and len(drive) == 1:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Partition -DriveLetter {drive} | Get-Disk | Get-PhysicalDisk | Select-Object -First 1).MediaType"],
                capture_output=True, text=True, timeout=20)
            if "SSD" in r.stdout.upper():
                kind = "ssd"
        except (OSError, subprocess.SubprocessError):
            pass
    elif sys.platform != "win32":
        try:
            dev = os.stat(path).st_dev
            major, minor = os.major(dev), os.minor(dev)
            rot = Path(f"/sys/dev/block/{major}:{minor}/../queue/rotational").resolve()
            if rot.exists() and rot.read_text().strip() == "0":
                kind = "ssd"
        except OSError:
            pass
    _DISK_CACHE[drive] = kind
    return kind


def hash_games(cfg, cache, games: list[Game], progress=None, log=print):
    """Calcola gli hash dei giochi indicati, con strategia adatta al tipo di disco."""
    todo = [g for g in games if g.needs_hash and not g.is_dir]
    if not todo:
        return
    max_bytes = cfg.max_hash_mb * 1024 * 1024
    light, heavy = [], []
    for g in todo:
        ext = g.path.suffix.lower().lstrip(".")
        skip = (g.platform.cd and ext != "zip") or ext in ("7z", "m3u", "cue", "gdi", "chd", "cdi")
        job = (str(g.path), ext, cfg.full_hash, 0 if skip else max_bytes, g.platform.headered)
        if skip or ext == "zip" or g.size <= LIGHT_MAX:
            light.append((g, job))
        else:
            heavy.append((g, job))
    heavy.sort(key=lambda x: x[1][0])  # ordine di percorso = letture piu' sequenziali su HDD

    cpus = cfg.hash_workers or os.cpu_count() or 4
    kinds = {disk_kind(g.path, cfg.disk) for g, _ in heavy} or {"ssd"}
    hdd = "hdd" in kinds
    io_workers = cfg.io_workers or (1 if hdd else cpus)
    light_workers = min(cpus, 4) if hdd else cpus
    if heavy:
        log(f"Hash: {len(light):,} file leggeri ({light_workers} processi), {len(heavy):,} file grandi "
            f"({io_workers} in parallelo, disco {'HDD' if hdd else 'SSD'})")

    task = progress.add_task("Hash ROM", total=len(todo)) if progress else None
    with ProcessPoolExecutor(max_workers=light_workers) as ex_light, \
            ProcessPoolExecutor(max_workers=max(1, io_workers)) as ex_heavy:
        futs = {ex_light.submit(_hash_job, job): g for g, job in light}
        futs.update({ex_heavy.submit(_hash_job, job): g for g, job in heavy})
        for fut in as_completed(futs):
            g = futs[fut]
            res = fut.result()
            res.pop("error", None)
            g.apply_file_data(res)
            g.needs_hash = False
            cache.put_file(str(g.path), g.size, g.mtime, res)
            if progress:
                progress.advance(task)
    cache.commit()


# ---------------------------------------------------------------- scan

def _m3u_refs(folder: Path) -> set[str]:
    refs = set()
    for m3u in folder.glob("*.m3u"):
        try:
            for line in m3u.read_text(errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    refs.add(Path(line).name.lower())
        except OSError:
            pass
    return refs


def _ignored(d: Path) -> bool:
    return d.name.startswith(".") or d.name.lower() in SKIP_DIRS or \
        (d / ".skyscraperignore").exists() or (d / ".gsignore").exists()


def find_platform_dirs(cfg) -> list[tuple[P.Platform, Path]]:
    """Trova le cartelle console sotto la radice ROM, anche annidate (fino a MAX_DEPTH livelli)."""
    root: Path = cfg.roms
    if not root.exists():
        raise FileNotFoundError(f"Cartella ROM non trovata: {root} - sceglila con «📁 Cambia…» (o in config.toml, [paths] roms)")
    overrides = {}
    for k, v in cfg.platform_folders.items():
        overrides[k] = v
    found = []

    def walk(d: Path, depth: int):
        try:
            subs = sorted(x for x in d.iterdir() if x.is_dir())
        except OSError:
            return
        for sub in subs:
            if _ignored(sub):
                continue
            rel = sub.relative_to(root).as_posix()
            pid = overrides.get(rel) or overrides.get(rel.replace("/", "\\"))
            p = P.PLATFORMS.get(pid) if pid else P.detect(sub.name, overrides)
            if p and p.virtual:
                p = None
            if p:
                found.append((p, sub))
            elif depth < MAX_DEPTH:
                walk(sub, depth + 1)

    walk(root, 1)
    return found


def list_entries(platform: P.Platform, folder: Path) -> list[tuple[Path, bool]]:
    """(percorso, is_dir). Per le piattaforme dir_games ogni sottocartella diretta e' un gioco."""
    exts = {"." + e for e in platform.all_exts}
    out: list[tuple[Path, bool]] = []
    if platform.dir_games:
        for e in sorted(folder.iterdir()):
            if e.is_dir():
                if not _ignored(e):
                    out.append((e, True))
            elif e.suffix.lower() in exts:
                out.append((e, False))
        return out
    for dirpath, dirnames, filenames in os.walk(folder):
        dp = Path(dirpath)
        if dp != folder and _ignored(dp):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS]
        refs = _m3u_refs(dp) if any(f.lower().endswith(".m3u") for f in filenames) else set()
        for f in filenames:
            if f.lower() in refs:
                continue  # dischi referenziati da un .m3u: si scrapa solo il .m3u
            if Path(f).suffix.lower() in exts:
                out.append((dp / f, False))
    return sorted(out)


def scan(cfg, cache, targets: list[tuple[P.Platform, Path]], progress=None) -> list[Game]:
    """Elenca i giochi e applica gli hash gia' in cache. NON legge il contenuto dei file:
    quelli ancora da hashare restano con needs_hash=True."""
    games: list[Game] = []
    task = progress.add_task("Scansione cartelle", total=len(targets)) if progress else None
    for platform, folder in targets:
        for path, is_dir in list_entries(platform, folder):
            try:
                st = path.stat()
            except OSError:
                continue
            g = Game(path=path, platform=platform, size=0 if is_dir else st.st_size,
                     mtime=st.st_mtime, is_dir=is_dir)
            if not is_dir:
                cached = cache.get_file(str(path), g.size, g.mtime)
                if cached is not None and (cached.get("md5") or cached.get("dat_crc") or not cfg.full_hash):
                    g.apply_file_data(cached)
                else:
                    g.needs_hash = True
            games.append(g)
        if progress:
            progress.advance(task)
    return games
