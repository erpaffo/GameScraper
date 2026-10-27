"""Copie di sicurezza dei dati dell'utente: preferiti, tempi di gioco, stati, collezioni personali (userdata.db)
e impostazioni dell'app (settings.json: emulatori, cartelle associate).

Non si salvano cache, metadati e immagini: si possono riscaricare. Ogni copia e' uno zip in <cache>/backups.
userdata.db si copia con l'API di backup di sqlite, quindi la copia e' coerente anche con l'app aperta (database in WAL).
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

from . import config as C

KEEP = 10  # copie conservate; le piu' vecchie si cancellano
AUTO_EVERY = 7 * 86400  # copia automatica settimanale all'avvio dell'app


def folder(cfg) -> Path:
    return Path(cfg.cache) / "backups"


def list_backups(cfg) -> list[dict]:
    d = folder(cfg)
    if not d.exists():
        return []
    out = [{"name": p.name, "size": p.stat().st_size, "time": p.stat().st_mtime} for p in d.glob("userdata-*.zip")]
    return sorted(out, key=lambda x: -x["time"])


def create(cfg, reason: str = "manuale") -> Path:
    d = folder(cfg)
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = d / f"userdata-{stamp}-{reason}.zip"
    with tempfile.TemporaryDirectory() as t:
        db = Path(cfg.cache) / "userdata.db"
        tmp = Path(t) / "userdata.db"
        if db.exists():
            src, dst = sqlite3.connect(db), sqlite3.connect(tmp)
            src.backup(dst)
            src.close()
            dst.close()
        with zipfile.ZipFile(out.with_suffix(".part"), "w", zipfile.ZIP_DEFLATED) as z:
            if tmp.exists():
                z.write(tmp, "userdata.db")
            s = C.settings_path(cfg)
            if s.exists():
                z.write(s, "settings.json")
    out.with_suffix(".part").replace(out)
    for old in list_backups(cfg)[KEEP:]:
        (d / old["name"]).unlink(missing_ok=True)
    return out


def auto(cfg) -> Path | None:
    """All'avvio: una copia se l'ultima ha piu' di una settimana (o non ce n'e' nessuna)."""
    last = list_backups(cfg)
    if last and time.time() - last[0]["time"] < AUTO_EVERY:
        return None
    if not (Path(cfg.cache) / "userdata.db").exists():
        return None
    return create(cfg, "auto")


def restore(cfg, name: str, user=None) -> dict:
    """Ripristina una copia. Prima ne crea una dei dati attuali, cosi' il ripristino si puo' annullare.
    `user` (UserData dell'app) permette di riscrivere il database aperto senza riavviare."""
    p = folder(cfg) / Path(name).name  # solo nomi della cartella delle copie, niente percorsi arbitrari
    if not p.is_file() or not zipfile.is_zipfile(p):
        raise FileNotFoundError(f"Copia non trovata: {name}")
    before = create(cfg, "prima-del-ripristino")
    restored = []
    with tempfile.TemporaryDirectory() as t, zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        if "userdata.db" in names:
            tmp = Path(z.extract("userdata.db", t))
            src = sqlite3.connect(tmp)
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                src.close()
                raise ValueError("La copia di userdata.db e' danneggiata")
            if user is not None:
                with user.lock:
                    src.backup(user.db)
            else:
                dst = sqlite3.connect(Path(cfg.cache) / "userdata.db")
                src.backup(dst)
                dst.close()
            src.close()
            restored.append("userdata.db")
        if "settings.json" in names:
            s = C.settings_path(cfg)
            s.write_bytes(z.read("settings.json"))
            C.apply_settings(cfg)
            restored.append("settings.json")
    return {"restored": restored, "safety_copy": before.name}
