"""Riassocia i dati dei giochi le cui cartelle/file sono stati rinominati o spostati.

La chiave di un gioco senza CRC (cartelle PS3/Switch/Xbox...) deriva dal NOME + dimensione: rinominando la
cartella la chiave cambia e metadati, preferiti, tempo di gioco e schermate sembrano persi. Qui si cercano i
giochi della libreria i cui file non esistono piu' e si abbinano alle voci non ancora note trovate sul disco
(stessa console, stessa dimensione, nome piu' simile); poi si trasferiscono i dati dalla vecchia chiave alla nuova.

Senza --apply mostra solo cosa farebbe.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import matching
from .library.sizes import game_size
from .scanner import Game, find_platform_dirs, list_entries

# (database, tabella) con una colonna `key`
TABLES = [("cache.db", "resources"), ("cache.db", "lookups"), ("userdata.db", "user_games"),
          ("userdata.db", "collection_edits"), ("userdata.db", "play_sessions"),
          ("userdata.db", "game_profiles"), ("userdata.db", "screenshots")]


def plan(cfg, log=print) -> list[tuple[str, str, str, str]]:
    """[(chiave vecchia, chiave nuova, titolo, nuovo percorso)]"""
    lib = sqlite3.connect(Path(cfg.cache) / "library.db")
    rows = lib.execute("SELECT key, platform, title, files, size FROM games WHERE store IS NULL OR store=''").fetchall()
    lib.close()
    known = {r[0] for r in rows}
    missing = []
    for key, plat, title, files, size in rows:
        fl = json.loads(files or "[]")
        if fl and not any(Path(f).exists() for f in fl):
            missing.append((key, plat, title, Path(fl[0]).stem if len(fl) == 1 else Path(fl[0]).name, size or 0))

    new: dict[str, list[tuple[Game, int]]] = {}
    for p, folder in find_platform_dirs(cfg):
        for path, is_dir in list_entries(p, folder):
            g = Game(path=path, platform=p, size=0 if is_dir else path.stat().st_size, mtime=0, is_dir=is_dir)
            if g.key not in known:
                new.setdefault(p.id, []).append((g, game_size(path, is_dir)))

    out, used = [], set()
    for key, plat, title, oldname, size in missing:
        cands = [(g, s) for g, s in new.get(plat, []) if g.key not in used and (not size or s == size)]
        # stessa dimensione = quasi certo; se e' ignota (0) serve anche un nome abbastanza simile
        hit, _ = matching.best(oldname, cands, key=lambda c: c[0].stem, min_score=0 if size else 60)
        if hit:
            g = hit[0]
            used.add(g.key)
            out.append((key, g.key, title, str(g.path)))
        else:
            log(f"  [non abbinato] {title}")
    return out


def apply(cfg, pairs, log=print):
    cache = Path(cfg.cache)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for name in ("cache.db", "userdata.db"):
        # backup di sqlite e non copia del file: userdata.db e' in WAL e puo' essere aperto dall'app
        src, dst = sqlite3.connect(cache / name), sqlite3.connect(cache / f"{name}.{stamp}.bak")
        src.backup(dst)
        src.close()
        dst.close()
    dbs = {n: sqlite3.connect(cache / n) for n in ("cache.db", "userdata.db")}
    for db_name, table in TABLES:
        db = dbs[db_name]
        try:
            for old, new, _, _ in pairs:
                db.execute(f"UPDATE OR REPLACE {table} SET key=? WHERE key=?", (new, old))
        except sqlite3.OperationalError:
            pass  # tabella non ancora creata
    for db in dbs.values():
        db.commit()
        db.close()
    log(f"Copie di sicurezza: *.{stamp}.bak in {cache}")
