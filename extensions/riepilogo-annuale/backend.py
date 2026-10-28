"""Riepilogo annuale («Wrapped»): statistiche di un anno dal profilo utente attivo.

GET /plugin-api/riepilogo-annuale/anno?anno=2026  -> {"anno", "anni": [..], "ore", "partite", "giorni", ...}
"""

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def riepilogo(sessions: list[tuple[str, float, float]], info: dict[str, tuple[str, str, str]], finiti: int, anno: int) -> dict:
    """`sessions` = (key, start, seconds); `info` = key -> (gruppo, titolo, console/store). Le sessioni sono del solo `anno`
    (secondo la data di inizio, ora locale)."""
    per_gioco: dict[str, dict] = {}
    per_console: dict[str, float] = defaultdict(float)
    per_mese = [0.0] * 12
    per_giorno_sett = [0.0] * 7
    giorni = set()
    lunga = (0.0, "")
    for key, start, sec in sessions:
        grp, titolo, console = info.get(key, (key, key, "?"))
        d = datetime.fromtimestamp(start)
        g = per_gioco.setdefault(grp, {"titolo": titolo, "console": console, "secondi": 0.0, "partite": 0})
        g["secondi"] += sec
        g["partite"] += 1
        per_console[console] += sec
        per_mese[d.month - 1] += sec
        per_giorno_sett[d.weekday()] += sec
        giorni.add(d.date())
        if sec > lunga[0]:
            lunga = (sec, titolo)
    top = sorted(per_gioco.values(), key=lambda g: -g["secondi"])
    tot = sum(g["secondi"] for g in top)
    mese = max(range(12), key=lambda i: per_mese[i]) if tot else None
    gio = max(range(7), key=lambda i: per_giorno_sett[i]) if tot else None
    cons = max(per_console.items(), key=lambda kv: kv[1])[0] if per_console else None
    return {"anno": anno, "ore": round(tot / 3600, 1), "partite": len(sessions), "giorni": len(giorni),
            "giochi": len(top), "finiti": finiti,
            "top": [{**g, "ore": round(g["secondi"] / 3600, 1)} for g in top[:5]],
            "console": cons, "console_ore": round(per_console[cons] / 3600, 1) if cons else 0,
            "mese": MESI[mese] if mese is not None else None, "mese_ore": round(per_mese[mese] / 3600, 1) if mese is not None else 0,
            "giorno_settimana": GIORNI[gio] if gio is not None else None,
            "sessione_piu_lunga": {"ore": round(lunga[0] / 3600, 1), "titolo": lunga[1]} if lunga[0] else None,
            "per_mese": [round(x / 3600, 1) for x in per_mese]}


def register(gs):
    cache = Path(gs.cfg.cache)

    @gs.route("anno")
    def anno(anno: int = 0):
        from pozzo.sync import profile_path
        lib = sqlite3.connect(cache / "library.db")
        user = sqlite3.connect(profile_path(gs.cfg, gs.cfg.app.get("profile") or "default"))
        try:
            tutte = user.execute("SELECT start FROM play_sessions").fetchall()
            anni = sorted({datetime.fromtimestamp(s).year for (s,) in tutte}, reverse=True)
            a = anno or (anni[0] if anni else datetime.now().year)
            if not 1990 <= a <= 2100:
                raise HTTPException(400, "Anno non valido")
            da, a_ = datetime(a, 1, 1).timestamp(), datetime(a + 1, 1, 1).timestamp()
            sessions = user.execute("SELECT key, start, seconds FROM play_sessions WHERE start >= ? AND start < ?", (da, a_)).fetchall()
            info = {k: (grp, title, store or plat) for k, grp, title, store, plat in
                    lib.execute("SELECT key, grp, title, store, platform FROM games")}
            names = {}
            try:
                from pozzo import platforms as P
                names = {pid: p.name for pid, p in P.PLATFORMS.items()}
            except Exception:
                pass
            info = {k: (g, t, names.get(c, c)) for k, (g, t, c) in info.items()}
            grp = {k: v[0] for k, v in info.items()}
            finiti = len({grp.get(k, k) for (k,) in user.execute(
                "SELECT u.key FROM user_games u JOIN sync_times s ON s.id = u.key AND s.tbl='game' AND s.field='status' "
                "WHERE u.status='done' AND s.t >= ? AND s.t < ?", (da, a_))})
        finally:
            lib.close()
            user.close()
        return {**riepilogo(sessions, info, finiti, a), "anni": anni}
