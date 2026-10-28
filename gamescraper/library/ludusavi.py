"""Posizioni dei salvataggi dei giochi PC dal manifest di Ludusavi (https://github.com/mtkennerly/ludusavi-manifest).

Il manifest (~17 MB di YAML, ~50.000 giochi, ~25 s per leggerlo) si scarica al massimo una volta a settimana, in
sottofondo, e si tiene ridotto a quello che serve qui: i percorsi Windows con i salvataggi (tag «save» o senza tag),
gli ID Steam/GOG e le cartelle di installazione. I segnaposto (<base>, <winAppData>, ...) diventano percorsi veri.
Le chiavi di registro (salvataggi nel registro di Windows) non sono gestite.
"""

from __future__ import annotations

import glob
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx

from ..matching import normalize

URL = "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/master/data/manifest.yaml"
TTL = 7 * 86400
_MEM: dict = {}
_LOCK = threading.Lock()


def path(cfg) -> Path:
    return Path(cfg.cache) / "ludusavi-manifest.json"


def reduce(data: dict) -> dict:
    """Manifest completo -> {games: {nome: {files: [[percorso, [store]]], dirs: [...]}}, steam: {id: nome}, gog: {id: nome}}."""
    games, steam, gog = {}, {}, {}
    for name, g in (data or {}).items():
        if not isinstance(g, dict):
            continue
        files = []
        for p, info in (g.get("files") or {}).items():
            info = info or {}
            tags = info.get("tags") or []
            if tags and "save" not in tags:
                continue
            when = info.get("when") or [{}]
            if not any((w or {}).get("os") in (None, "windows") for w in when):
                continue
            stores = sorted({w["store"] for w in when if (w or {}).get("store")} if all((w or {}).get("store") for w in when) else set())
            files.append([p, stores])
        if not files:
            continue
        games[name] = {"files": files, "dirs": list(g.get("installDir") or {})}
        ids = g.get("id") or {}
        for i in [(g.get("steam") or {}).get("id"), *(ids.get("steamExtra") or [])]:
            if i:
                steam[str(i)] = name
        for i in [(g.get("gog") or {}).get("id"), *(ids.get("gogExtra") or [])]:
            if i:
                gog[str(i)] = name
    return {"games": games, "steam": steam, "gog": gog}


def update(cfg, force: bool = False) -> bool:
    """Scarica e riduce il manifest se manca o ha piu' di una settimana. True se aggiornato."""
    out = path(cfg)
    if not force and out.is_file() and time.time() - out.stat().st_mtime < TTL:
        return False
    if not _LOCK.acquire(blocking=False):  # gia' in corso in un altro thread
        return False
    try:
        import yaml
        r = httpx.get(URL, timeout=httpx.Timeout(120, connect=15), follow_redirects=True)
        r.raise_for_status()
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        small = reduce(yaml.load(r.text, Loader=loader))
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        tmp.write_text(json.dumps(small, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(out)
        _MEM.clear()
        return True
    except Exception:  # rete assente o manifest cambiato: si riprova al prossimo avvio
        if out.is_file():
            os.utime(out)  # non riprovare a ogni richiesta
        return False
    finally:
        _LOCK.release()


def load(cfg) -> dict | None:
    """Manifest ridotto (in memoria dopo la prima lettura); se manca o e' vecchio lo aggiorna in sottofondo."""
    p = path(cfg)
    if not p.is_file() or time.time() - p.stat().st_mtime >= TTL:
        threading.Thread(target=update, args=(cfg,), daemon=True).start()
    if not p.is_file():
        return None
    key = (str(p), p.stat().st_mtime)
    if _MEM.get("key") != key:
        try:
            man = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        man["norm"] = {normalize(n): n for n in man["games"]}
        man["bydir"] = {d.lower(): n for n, g in man["games"].items() for d in g["dirs"]}
        _MEM.update(key=key, man=man)
    return _MEM["man"]


def find(man: dict, store: str, gid: str, title: str, install_dir: str = "") -> str | None:
    """Nome del gioco nel manifest: per ID Steam/GOG, poi per titolo, poi per cartella di installazione."""
    if store in ("steam", "gog") and man[store].get(str(gid)):
        return man[store][str(gid)]
    return man["norm"].get(normalize(title)) or (man["bydir"].get(Path(install_dir).name.lower()) if install_dir else None)


def _known_dirs() -> dict[str, str]:
    from ..remote.emusetup import documents_dir
    env = os.environ.get
    local = env("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return {"<home>": str(Path.home()), "<winAppData>": env("APPDATA") or str(Path.home() / "AppData" / "Roaming"),
            "<winLocalAppData>": local, "<winLocalAppDataLow>": str(Path(local).parent / "LocalLow"),
            "<winDocuments>": str(documents_dir()), "<winPublic>": env("PUBLIC") or "C:/Users/Public",
            "<winProgramData>": env("PROGRAMDATA") or "C:/ProgramData", "<winDir>": env("WINDIR") or "C:/Windows",
            "<osUserName>": env("USERNAME") or ""}


def save_paths(man: dict, name: str, store: str = "", gid: str = "", install_dir: str = "",
               store_root: str = "") -> list[Path]:
    """File e cartelle di salvataggio del gioco che esistono su questo PC."""
    vals = {k: glob.escape(v) for k, v in _known_dirs().items() if v}
    if install_dir:
        vals["<base>"] = glob.escape(install_dir)
        vals["<game>"] = glob.escape(Path(install_dir).name)
    if store_root:
        vals["<root>"] = glob.escape(store_root)
    vals["<storeUserId>"] = "*"
    vals["<storeGameId>"] = glob.escape(str(gid)) if gid else "*"
    out: list[Path] = []
    for raw, stores in (man["games"].get(name) or {}).get("files", []):
        if stores and store not in stores:
            continue
        pat = re.sub(r"<\w+>", lambda m: vals.get(m.group(0), "\0"), raw)
        if "\0" in pat:  # segnaposto che qui non si puo' risolvere (es. <xdgData>, <root> sconosciuto)
            continue
        out += [Path(p) for p in glob.glob(pat, recursive=True)]
    res = {os.path.normcase(str(p.resolve())): p for p in out}
    dirs = [k for k, p in res.items() if p.is_dir()]
    return [p for k, p in res.items() if not any(k.startswith(d + os.sep) for d in dirs)]  # niente doppioni dentro una cartella trovata
