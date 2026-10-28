"""Diario di gioco: voci datate per gioco e cronologia delle sessioni (dal profilo utente attivo).

GET  /plugin-api/diario/gioco?id=<id>   -> {"voci": [{t, testo}], "sessioni": [{inizio, secondi}], "totale": s, "partite": n}
POST /plugin-api/diario/aggiungi        {"id", "testo"}
POST /plugin-api/diario/elimina         {"id", "t"}

Le voci stanno nelle impostazioni dell'estensione, per gioco (tutte le versioni dello stesso gioco condividono il diario).
"""

import sqlite3
import threading
import time
from pathlib import Path

from fastapi import Body, HTTPException

MAX_TESTO = 4000
MAX_SESSIONI = 60


def _group(lib: sqlite3.Connection, gid: int):
    row = lib.execute("SELECT grp FROM games WHERE id=?", (gid,)).fetchone()
    if not row:
        raise HTTPException(404, "Gioco non trovato")
    keys = [k for (k,) in lib.execute("SELECT key FROM games WHERE grp=?", (row[0],))]
    return row[0], keys


def sessioni(user: sqlite3.Connection, keys: list[str]) -> list[tuple[float, float]]:
    q = ",".join("?" * len(keys))
    return user.execute(f"SELECT start, seconds FROM play_sessions WHERE key IN ({q}) ORDER BY start DESC", keys).fetchall()


def register(gs):
    cache = Path(gs.cfg.cache)
    lock = threading.Lock()

    def open_dbs():
        from gamescraper.sync import profile_path
        return sqlite3.connect(cache / "library.db"), sqlite3.connect(profile_path(gs.cfg, gs.cfg.app.get("profile") or "default"))

    def voci(grp: str) -> list[dict]:
        return list((gs.settings.get("voci") or {}).get(grp) or [])

    def salva(grp: str, lista: list[dict]):
        todo = dict(gs.settings.get("voci") or {})
        if lista:
            todo[grp] = lista
        else:
            todo.pop(grp, None)
        gs.save_settings({"voci": todo})
        gs.settings["voci"] = todo

    @gs.route("gioco")
    def gioco(id: int):
        lib, user = open_dbs()
        try:
            grp, keys = _group(lib, id)
            ss = sessioni(user, keys)
        finally:
            lib.close()
            user.close()
        return {"voci": sorted(voci(grp), key=lambda v: -v["t"]),
                "sessioni": [{"inizio": s, "secondi": d} for s, d in ss[:MAX_SESSIONI]],
                "totale": sum(d for _, d in ss), "partite": len(ss)}

    @gs.route("aggiungi", methods=("POST",))
    def aggiungi(body: dict = Body(...)):
        testo = str(body.get("testo") or "").strip()[:MAX_TESTO]
        if not testo:
            raise HTTPException(400, "Scrivi qualcosa")
        lib = sqlite3.connect(cache / "library.db")
        try:
            grp, _ = _group(lib, int(body.get("id") or 0))
        finally:
            lib.close()
        with lock:
            lista = voci(grp)
            t = time.time()
            while any(v["t"] == t for v in lista):  # t identifica la voce: due voci nello stesso istante non devono coincidere
                t += 1e-3
            lista.append({"t": t, "testo": testo})
            salva(grp, lista)
        return {"ok": True, "t": t}

    @gs.route("elimina", methods=("POST",))
    def elimina(body: dict = Body(...)):
        lib = sqlite3.connect(cache / "library.db")
        try:
            grp, _ = _group(lib, int(body.get("id") or 0))
        finally:
            lib.close()
        with lock:
            lista = voci(grp)
            resto = [v for v in lista if v["t"] != body.get("t")]
            if len(resto) == len(lista):
                raise HTTPException(404, "Voce non trovata")
            salva(grp, resto)
        return {"ok": True}
