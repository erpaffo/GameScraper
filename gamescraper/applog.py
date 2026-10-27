"""Log su file, da leggere quando qualcosa non va (aggiornamento store, avvio, pianificazione).

Attivazione:
    python -m gamescraper app --log                 -> <cache>/logs/diagnostica.log
    python -m gamescraper app --log C:\\percorso.log  -> file indicato
    GAMESCRAPER_LOG=1 (oppure un percorso)           -> equivalente, utile per la scorciatoia

Il file è in append e viene troncato quando supera ~8 MB. Ogni riga ha ora, categoria e messaggio:
    [17:42:03] store        fitgirl-repacks.site: pagina 51/147 — 50 giochi
    [17:42:04] piano        modalita'=startup, avvio rimandato: libreria in ricostruzione
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_path: Path | None = None
MAX_BYTES = 8 * 1024 * 1024


def start(target: str | os.PathLike | None, cache_dir: str | os.PathLike | None = None) -> Path | None:
    """Apre il file di log. `target` può essere None (spento), '1'/'true'/'on' o un percorso."""
    global _path
    raw = str(target or "").strip()
    if raw in ("", "0", "false", "off", "no"):
        return None
    if raw.lower() in ("1", "true", "on", "yes"):
        base = Path(cache_dir) if cache_dir else Path.cwd()
        path = base / "logs" / "diagnostica.log"
    else:
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_suffix(path.suffix + ".old"))
        _path = path
        write("avvio", f"--- avvio (pid {os.getpid()}) ---")
        return path
    except OSError:
        _path = None
        return None


def enabled() -> bool:
    return _path is not None


def current() -> Path | None:
    return _path


def write(kind: str, message: str):
    """Aggiunge una riga. Senza file aperto non fa nulla (nessun costo quando il log è spento)."""
    if _path is None:
        return
    line = f"[{time.strftime('%H:%M:%S')}] {kind:<10} {message}\n"
    with _lock:
        try:
            with open(_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
