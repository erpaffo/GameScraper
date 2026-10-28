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
        cache = Path(cfg.cache)
        # userdata.db (profilo principale) e gli altri profili utente in profiles/
        names = ["userdata.db", *(f"profiles/{f.name}" for f in sorted((cache / "profiles").glob("*.db")))]
        for n in names:
            if (cache / n).exists():
                (Path(t) / n).parent.mkdir(parents=True, exist_ok=True)
                src, dst = sqlite3.connect(cache / n), sqlite3.connect(Path(t) / n)
                src.backup(dst)
                src.close()
                dst.close()
        with zipfile.ZipFile(out.with_suffix(".part"), "w", zipfile.ZIP_DEFLATED) as z:
            for n in names:
                if (Path(t) / n).exists():
                    z.write(Path(t) / n, n)
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


def restore(cfg, name: str, user=None, profiles: dict | None = None) -> dict:
    """Ripristina una copia. Prima ne crea una dei dati attuali, cosi' il ripristino si puo' annullare.
    `user` (UserData del profilo principale) e `profiles` ({id: UserData} degli altri profili aperti) permettono di
    riscrivere i database aperti senza riavviare."""
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
        for n in sorted(x for x in names if x.startswith("profiles/") and x.endswith(".db") and "/" not in x[9:]):
            tmp = Path(z.extract(n, t))
            src = sqlite3.connect(tmp)
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                src.close()
                continue  # un profilo danneggiato non blocca il ripristino degli altri
            ud = (profiles or {}).get(Path(n).stem)
            if ud is not None:
                with ud.lock:
                    src.backup(ud.db)
            else:
                (Path(cfg.cache) / "profiles").mkdir(parents=True, exist_ok=True)
                dst = sqlite3.connect(Path(cfg.cache) / n)
                src.backup(dst)
                dst.close()
            src.close()
            restored.append(n)
        if "settings.json" in names:
            s = C.settings_path(cfg)
            s.write_bytes(z.read("settings.json"))
            C.apply_settings(cfg)
            restored.append("settings.json")
    return {"restored": restored, "safety_copy": before.name}
