"""Obiettivi personali: obiettivi a periodo, serie di giorni e distintivi, dal profilo utente attivo.

GET  /plugin-api/obiettivi/stato     -> {"obiettivi": [...con avanzamento], "serie": {attuale, migliore}, "distintivi": [...]}
POST /plugin-api/obiettivi/salva     {"obiettivi": [{"tipo": "finiti"|"ore"|"giorni", "n": 3, "periodo": "settimana"|"mese"|"anno"}]}

«Finiti» conta i giochi (non le versioni) il cui stato e' «Finito», cambiato nel periodo: la data e' l'ultima modifica
dello stato (sync_times), quindi un gioco ritoccato dopo averlo finito conta dal ritocco.
"""

import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Body, HTTPException

TIPI = {"finiti": "giochi finiti", "ore": "ore giocate", "giorni": "giorni di gioco"}
PERIODI = {"settimana": "questa settimana", "mese": "questo mese", "anno": "quest'anno"}
MAX_OBIETTIVI = 20


def inizio(periodo: str, now: float) -> float:
    d = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if periodo == "settimana":
        d -= timedelta(days=d.weekday())
    elif periodo == "mese":
        d = d.replace(day=1)
    else:
        d = d.replace(month=1, day=1)
    return d.timestamp()


def giorni_di_gioco(sessions: list[tuple[float, float]]) -> set[date]:
    """Giorni (ora locale) in cui c'e' stata almeno una partita; una sessione a cavallo della mezzanotte conta per entrambi."""
    out = set()
    for s, d in sessions:
        a, b = datetime.fromtimestamp(s).date(), datetime.fromtimestamp(s + max(d, 0)).date()
        while a <= b:
            out.add(a)
            a += timedelta(days=1)
    return out


def serie(giorni: set[date], oggi: date) -> tuple[int, int]:
    """(serie in corso, serie migliore). Quella in corso vale anche se oggi non si e' ancora giocato ma ieri si'."""
    best = run = 0
    prev = None
    for g in sorted(giorni):
        run = run + 1 if prev and (g - prev).days == 1 else 1
        best, prev = max(best, run), g
    cur, g = 0, oggi if oggi in giorni else oggi - timedelta(days=1)
    while g in giorni:
        cur, g = cur + 1, g - timedelta(days=1)
    return cur, best


def avanzamento(ob: dict, sessions, finiti: list[tuple[str, float]], now: float) -> float:
    da = inizio(ob["periodo"], now)
    if ob["tipo"] == "finiti":
        return len({g for g, t in finiti if t >= da})
    if ob["tipo"] == "ore":
        return sum(max(0.0, min(s + d, now) - max(s, da)) for s, d in sessions) / 3600
    return len({g for g in giorni_di_gioco([x for x in sessions if x[0] + x[1] >= da]) if g >= datetime.fromtimestamp(da).date()})


DISTINTIVI = [  # (id, titolo, descrizione, misura, soglia)
    ("f1", "Primo traguardo", "Finisci il primo gioco", "finiti", 1),
    ("f10", "Decina", "Finisci 10 giochi", "finiti", 10),
    ("f50", "Mezzo centinaio", "Finisci 50 giochi", "finiti", 50),
    ("o100", "Cento ore", "Gioca 100 ore in totale", "ore", 100),
    ("o1000", "Mille ore", "Gioca 1000 ore in totale", "ore", 1000),
    ("s7", "Una settimana di fila", "Gioca 7 giorni consecutivi", "serie", 7),
    ("s30", "Un mese di fila", "Gioca 30 giorni consecutivi", "serie", 30),
]


def register(gs):
    cache = Path(gs.cfg.cache)

    def conn():
        from pozzo.sync import profile_path
        return sqlite3.connect(cache / "library.db"), sqlite3.connect(profile_path(gs.cfg, gs.cfg.app.get("profile") or "default"))

    def dati(lib, user):
        grp = dict(lib.execute("SELECT key, grp FROM games"))
        sessions = user.execute("SELECT start, seconds FROM play_sessions").fetchall()
        # «Finito» e quando: l'ora dell'ultima modifica dello stato per le chiavi che oggi sono «done»
        finiti = [(grp.get(k, k), t) for k, t in user.execute(
            "SELECT s.id, s.t FROM sync_times s JOIN user_games u ON u.key = s.id "
            "WHERE s.tbl='game' AND s.field='status' AND u.status='done'")]
        tot_finiti = len({grp.get(k, k) for (k,) in user.execute("SELECT key FROM user_games WHERE status='done'")})
        return sessions, finiti, tot_finiti

    def lista() -> list[dict]:
        return [o for o in (gs.settings.get("obiettivi") or []) if o.get("tipo") in TIPI and o.get("periodo") in PERIODI]

    @gs.route("stato")
    def stato():
        lib, user = conn()
        try:
            sessions, finiti, tot_finiti = dati(lib, user)
        finally:
            lib.close()
            user.close()
        now = time.time()
        cur, best = serie(giorni_di_gioco(sessions), date.today())
        ore_tot = sum(d for _, d in sessions) / 3600
        misure = {"finiti": tot_finiti, "ore": ore_tot, "serie": best}
        obs = []
        for o in lista():
            v = avanzamento(o, sessions, finiti, now)
            obs.append({**o, "valore": round(v, 1), "fatto": v >= o["n"], "etichetta": f"{TIPI[o['tipo']]} {PERIODI[o['periodo']]}"})
        return {"obiettivi": obs, "serie": {"attuale": cur, "migliore": best},
                "distintivi": [{"id": i, "titolo": t, "descrizione": d, "ottenuto": misure[m] >= n,
                                "valore": round(min(misure[m], n), 1), "soglia": n} for i, t, d, m, n in DISTINTIVI]}

    @gs.route("salva", methods=("POST",))
    def salva(body: dict = Body(...)):
        out = []
        for o in body.get("obiettivi") or []:
            try:
                n = float(o.get("n"))
            except (TypeError, ValueError):
                raise HTTPException(400, "Numero non valido")
            if o.get("tipo") not in TIPI or o.get("periodo") not in PERIODI or not (0 < n <= 10000):
                raise HTTPException(400, "Obiettivo non valido")
            out.append({"id": str(o.get("id") or uuid.uuid4().hex[:8]), "tipo": o["tipo"], "periodo": o["periodo"],
                        "n": int(n) if n == int(n) else n})
        if len(out) > MAX_OBIETTIVI:
            raise HTTPException(400, f"Al massimo {MAX_OBIETTIVI} obiettivi")
        gs.save_settings({"obiettivi": out})
        gs.settings["obiettivi"] = out
        return stato()
