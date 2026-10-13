"""Caricamento configurazione (TOML) con valori di default sensati."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import sys

FROZEN = getattr(sys, "frozen", False)  # eseguibile creato con PyInstaller


def app_data_dir() -> Path:
    """Cartella dei dati dell'utente (config, cache, impostazioni) per l'eseguibile."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "GameScraper"


DEFAULT_CONFIG = """# GameScraper - configurazione (creata al primo avvio).
# La cartella delle ROM si sceglie dall'app: Impostazioni > Nuovi giochi > Cambia.
# Tutte le opzioni sono descritte in config.example.toml (nel repository).

[paths]
cache = "cache"
output = "output"
"""


def _default_config_path() -> Path | None:
    """config.toml: cartella corrente, cartella del progetto (sviluppo), cartella dell'eseguibile
    (versione portatile) e infine la cartella dei dati dell'utente, creata al primo avvio."""
    cands = [Path("config.toml")]
    if FROZEN:
        cands.append(Path(sys.executable).resolve().parent / "config.toml")
    else:
        cands.append(Path(__file__).resolve().parent.parent / "config.toml")
    for c in cands:
        if c.exists():
            return c
    if FROZEN:
        d = app_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / "config.toml"
        if not p.exists():
            p.write_text(DEFAULT_CONFIG, encoding="utf-8")
        return p
    return None


DEFAULT_FIELDS = ["title", "description", "developer", "publisher", "genres", "release_date", "players", "rating"]
ALL_MEDIA = ["cover", "screenshot", "title", "wheel", "marquee", "video", "box3d", "fanart"]


@dataclass
class Config:
    roms: Path = Path("roms")
    cache: Path = Path("cache")
    output: Path = Path("output")
    platform_folders: dict[str, str] = field(default_factory=dict)

    sources: list[str] = field(default_factory=lambda: ["libretro", "launchbox", "tgdb", "arcadedb", "igdb", "screenscraper",
                                                       "rawg", "thegamesdb", "wikipedia"])
    fields: list[str] = field(default_factory=lambda: list(DEFAULT_FIELDS))
    media: list[str] = field(default_factory=lambda: ["cover", "screenshot", "wheel"])
    regions: list[str] = field(default_factory=lambda: ["eu", "us", "wor", "jp"])
    languages: list[str] = field(default_factory=lambda: ["it", "en"])
    min_match: int = 70
    max_hash_mb: int = 512
    full_hash: bool = False  # anche MD5/SHA1 (piu' lento, utile solo per ScreenScraper su ROM non-ZIP)
    hash_workers: int = 0  # 0 = numero di CPU
    disk: str = "auto"  # auto | hdd | ssd  (strategia di lettura per l'hash)
    io_workers: int = 0  # file grandi letti in parallelo; 0 = 1 su HDD, tutti i core su SSD
    field_priority: dict[str, list[str]] = field(default_factory=dict)  # campo -> ordine sorgenti
    link_media: bool = True  # hardlink dalla cache all'output (istantaneo, nessuno spazio extra)

    ss: dict = field(default_factory=dict)
    igdb: dict = field(default_factory=dict)
    tgdb: dict = field(default_factory=dict)
    libretro: dict = field(default_factory=dict)
    arcadedb: dict = field(default_factory=dict)
    collections: dict = field(default_factory=dict)  # min_games, exclude, aliases
    emulators: dict = field(default_factory=dict)  # nome -> percorso eseguibile
    launch: dict = field(default_factory=dict)  # piattaforma -> {emulator, args, core}
    rawg: dict = field(default_factory=dict)  # {"api_key": ...}
    limits: dict = field(default_factory=dict)  # regole per fonte (vedi policy.py)
    catalogs: dict = field(default_factory=dict)  # store online per console: {"nds": ["https://..."]}
    stores: dict = field(default_factory=dict)  # store PC: {"steam": {"api_key":..., "steamid":...}, ...}
    app: dict = field(default_factory=dict)  # tema, plugin disattivati, modalita' di avvio
    config_path: Path | None = None

    @property
    def wanted(self) -> set[str]:
        return set(self.fields) | {f"media:{m}" for m in self.media}


SETTINGS_FILE = "settings.json"


def settings_path(cfg: Config) -> Path:
    """Impostazioni scritte dall'app (emulatori, cartelle associate): file separato, cosi'
    config.toml e i suoi commenti non vengono mai riscritti."""
    base = cfg.config_path.parent if cfg.config_path else Path.cwd()
    return base / SETTINGS_FILE


def read_settings(cfg: Config) -> dict:
    p = settings_path(cfg)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def apply_settings(cfg: Config):
    s = read_settings(cfg)
    cfg.emulators.update(s.get("emulators", {}))
    for pid, v in s.get("launch", {}).items():
        if v:
            cfg.launch[pid] = v
        else:
            cfg.launch.pop(pid, None)
    cfg.platform_folders.update(s.get("platform_folders", {}))
    if (s.get("paths") or {}).get("roms"):  # cartella ROM scelta dall'app
        cfg.roms = Path(os.path.expandvars(os.path.expanduser(s["paths"]["roms"])))
    from . import platforms as _P
    _P.register_custom(s.get("custom_platforms", []))
    for pid, v in s.get("stores", {}).items():
        cfg.stores.setdefault(pid, {}).update(v or {})
    cfg.app.update(s.get("app", {}))
    for pid, urls in s.get("catalogs", {}).items():
        if urls:
            cfg.catalogs[pid] = list(urls)
        else:
            cfg.catalogs.pop(pid, None)
    cfg.rawg.update(s.get("rawg", {}))
    cfg.ss.update(s.get("screenscraper", {}))
    cfg.igdb.update(s.get("igdb", {}))
    cfg.tgdb.update(s.get("thegamesdb", {}))
    for src, v in s.get("limits", {}).items():
        cfg.limits.setdefault(src, {}).update(v or {})
    sc = s.get("scrape", {})
    if sc.get("sources"):
        cfg.sources = list(sc["sources"])


def set_setting(cfg: Config, key: str, value):
    """Scrive una voce intera di settings.json (es. una lista) e riapplica le impostazioni."""
    s = read_settings(cfg)
    if value in (None, [], {}):
        s.pop(key, None)
    else:
        s[key] = value
    p = settings_path(cfg)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    apply_settings(cfg)


def save_settings(cfg: Config, **sections):
    """Aggiorna settings.json (solo le sezioni passate) e riapplica al config in memoria."""
    s = read_settings(cfg)
    for k, v in sections.items():
        target = getattr(cfg, k, None)
        for kk, vv in v.items():  # valore vuoto = rimuovi (anche dal config in memoria)
            if vv in (None, "", {}) and isinstance(target, dict):
                target.pop(kk, None)
        s.setdefault(k, {}).update(v)
        s[k] = {kk: vv for kk, vv in s[k].items() if vv not in (None, "", {})}
    p = settings_path(cfg)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    apply_settings(cfg)


def load(path: str | os.PathLike | None) -> Config:
    cfg = Config()
    if path is None:
        path = _default_config_path()
    if path is None:
        apply_settings(cfg)
        return cfg
    path = Path(path)
    with open(path, "rb") as f:
        d = tomllib.load(f)
    base = path.resolve().parent

    def p(v: str) -> Path:
        q = Path(os.path.expandvars(os.path.expanduser(v)))
        return q if q.is_absolute() else base / q

    paths = d.get("paths", {})
    if "roms" in paths:
        cfg.roms = p(paths["roms"])
    cfg.cache = p(paths.get("cache", "cache"))
    cfg.output = p(paths.get("output", "output"))
    cfg.platform_folders = d.get("platform_folders", {})

    s = d.get("scrape", {})
    for k in ("sources", "fields", "media", "regions", "languages", "min_match", "max_hash_mb",
              "full_hash", "hash_workers", "disk", "io_workers", "link_media"):
        if k in s:
            setattr(cfg, k, s[k])
    bad = set(cfg.media) - set(ALL_MEDIA)
    if bad:
        raise ValueError(f"Tipi media sconosciuti: {sorted(bad)}. Validi: {ALL_MEDIA}")

    cfg.field_priority = d.get("field_priority", {})
    cfg.ss = d.get("screenscraper", {})
    cfg.igdb = d.get("igdb", {})
    cfg.tgdb = d.get("thegamesdb", {})
    cfg.libretro = d.get("libretro", {})
    cfg.arcadedb = d.get("arcadedb", {})
    cfg.collections = d.get("collections", {})
    cfg.emulators = d.get("emulators", {})
    cfg.launch = d.get("launch", {})
    cfg.stores = d.get("stores", {})
    cfg.rawg = d.get("rawg", {})
    cfg.limits = d.get("limits", {})
    cfg.app = d.get("app", {})
    cfg.config_path = path.resolve()
    apply_settings(cfg)
    # Le credenziali possono arrivare anche da variabili d'ambiente
    env = {
        ("ss", "user"): "SS_USER", ("ss", "password"): "SS_PASSWORD",
        ("ss", "devid"): "SS_DEVID", ("ss", "devpassword"): "SS_DEVPASSWORD",
        ("igdb", "client_id"): "IGDB_CLIENT_ID", ("igdb", "client_secret"): "IGDB_CLIENT_SECRET",
        ("tgdb", "api_key"): "TGDB_API_KEY",
    }
    for (sect, key), var in env.items():
        if os.environ.get(var):
            getattr(cfg, sect)[key] = os.environ[var]
    return cfg
