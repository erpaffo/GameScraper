"""Peso su disco di un gioco: file singolo, file collegati (.cue -> .bin, .gdi -> tracce, .m3u -> dischi)
oppure cartella intera (giochi PS3, Switch, Xbox 360...). I risultati per le cartelle vengono messi in
cache in base alla data di modifica, per non rileggere ogni volta migliaia di file su un HDD."""

from __future__ import annotations

import os
import re
from pathlib import Path

_CUE_FILE = re.compile(r'^\s*FILE\s+"?(.+?)"?\s+\w+\s*$', re.I | re.M)


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def game_size(path: Path, is_dir: bool, cache=None, _depth: int = 0) -> int:
    path = Path(path)
    if is_dir:
        if cache is None:
            return dir_size(path)
        try:
            mt = path.stat().st_mtime
        except OSError:
            return 0
        key = f"size:{path}"
        hit = cache.kv_get(key)
        if hit and abs(hit.get("mtime", 0) - mt) < 1:
            return hit["size"]
        n = dir_size(path)
        cache.kv_set(key, {"mtime": mt, "size": n})
        return n
    ext = path.suffix.lower()
    total = _size(path)
    if _depth > 2:
        return total
    try:
        if ext == ".cue":
            for ref in _CUE_FILE.findall(path.read_text(errors="ignore")):
                total += _size(path.parent / ref)
        elif ext == ".gdi":
            for line in path.read_text(errors="ignore").splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 5:
                    total += _size(path.parent / parts[4].strip('"'))
        elif ext == ".m3u":
            for line in path.read_text(errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    total += game_size(path.parent / line, False, cache, _depth + 1)
    except OSError:
        pass
    return total


def human(n: int | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""
