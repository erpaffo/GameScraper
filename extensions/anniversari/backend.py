"""Anniversari retro: i giochi della libreria usciti nello stesso giorno e mese di oggi.

GET /plugin-api/anniversari/oggi[?giorno=MM-GG]  -> {"giorno": "06-23", "anno": 2026, "giochi": [{id, titolo, piattaforma, anni}]}
"""

import sqlite3
from datetime import date
from pathlib import Path

from fastapi import HTTPException


def anniversari(db: sqlite3.Connection, mese_giorno: str, anno: int) -> list[dict]:
    """Giochi con release_date AAAA-MM-GG uguale a oggi (mese-giorno), usciti in un anno precedente."""
    out = []
    for gid, title, plat, store, rel in db.execute(
            "SELECT id, title, platform, store, release_date FROM games "
            "WHERE is_primary=1 AND release_date LIKE ? AND COALESCE(store,'') != 'online'", (f"____-{mese_giorno}",)):
        anni = anno - int(rel[:4])
        if anni > 0:
            out.append({"id": gid, "titolo": title, "piattaforma": store or plat, "anni": anni})
    out.sort(key=lambda g: (-g["anni"], g["titolo"].lower()))
    return out


def register(gs):
    db_path = Path(gs.cfg.cache) / "library.db"

    @gs.route("oggi")
    def oggi(giorno: str = ""):
        today = date.today()
        md = giorno or today.strftime("%m-%d")
        if len(md) != 5 or md[2] != "-" or not (md[:2] + md[3:]).isdigit():
            raise HTTPException(400, "giorno non valido (MM-GG)")
        if not db_path.exists():
            return {"giorno": md, "anno": today.year, "giochi": []}
        db = sqlite3.connect(db_path)
        try:
            return {"giorno": md, "anno": today.year, "giochi": anniversari(db, md, today.year)}
        finally:
            db.close()
