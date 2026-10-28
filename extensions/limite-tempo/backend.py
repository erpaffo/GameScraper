"""Limite di tempo: avvisa quando si supera il tempo di gioco al giorno o alla settimana.

GET  /plugin-api/limite-tempo/stato   -> limiti, tempo usato oggi e questa settimana (compresa la partita in corso)
POST /plugin-api/limite-tempo/salva   {"giorno_ore", "settimana_ore", "attivo"}

Conta le sessioni del profilo attivo (play_sessions) piu' la partita in corso. Durante una partita controlla ogni
TICK secondi; avvisa al superamento e poi ogni RIPETI secondi finche' si continua.
shortcut: avvisa soltanto, non chiude il gioco; se serve un blocco vero, chiudere il processo dal launcher.
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, HTTPException

TICK = 30          # secondi tra un controllo e l'altro durante una partita
RIPETI = 30 * 60   # secondi tra un avviso e il successivo oltre il limite


def inizi(now: float) -> tuple[float, float]:
    """Mezzanotte di oggi e lunedi' di questa settimana (ora locale), come timestamp."""
    d = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return d.timestamp(), (d - timedelta(days=d.weekday())).timestamp()


def usato(user: sqlite3.Connection, now: float, corso_da: float | None = None) -> tuple[float, float]:
    """(secondi oggi, secondi settimana). Le sessioni a cavallo della mezzanotte contano solo per la parte dentro il periodo.

    `corso_da` = inizio della partita in corso: non e' ancora in play_sessions (si scrive a fine partita)."""
    oggi, sett = inizi(now)
    ss = user.execute("SELECT start, seconds FROM play_sessions WHERE start + seconds >= ?", (sett,)).fetchall()
    if corso_da is not None:
        ss.append((corso_da, max(0.0, now - corso_da)))

    def somma(da: float) -> float:
        return sum(max(0.0, min(s + d, now) - max(s, da)) for s, d in ss)
    return somma(oggi), somma(sett)


def superati(conf: dict, oggi: float, sett: float) -> list[str]:
    """Quali limiti sono superati, con il tempo gia' passato (testo da mostrare)."""
    out = []
    if conf.get("giorno_ore") and oggi >= conf["giorno_ore"] * 3600:
        out.append(f"oggi hai giocato {oggi / 3600:.1f} h (limite {conf['giorno_ore']:g} h)")
    if conf.get("settimana_ore") and sett >= conf["settimana_ore"] * 3600:
        out.append(f"questa settimana hai giocato {sett / 3600:.1f} h (limite {conf['settimana_ore']:g} h)")
    return out


def register(gs):
    cache = Path(gs.cfg.cache)
    run = {"stop": None, "t0": 0.0}
    lock = threading.Lock()

    def conf() -> dict:
        s = gs.settings
        return {"giorno_ore": float(s.get("giorno_ore") or 0), "settimana_ore": float(s.get("settimana_ore") or 0),
                "attivo": s.get("attivo", True) is not False}

    def db() -> sqlite3.Connection:
        from pozzo.sync import profile_path
        return sqlite3.connect(profile_path(gs.cfg, gs.cfg.app.get("profile") or "default"))

    def avvisa(titolo: str, testo: str):
        gs.log(f"[limite-tempo] {testo}")
        try:
            from pozzo import notify
            notify.send(gs.cfg, titolo, testo)
        except Exception:  # un avviso perso non deve mai fermare niente
            pass

    def controlla(t0: float) -> str | None:
        """Testo dei limiti superati (compresa la partita iniziata a `t0`), o None."""
        c = conf()
        if not c["attivo"]:
            return None
        u = db()
        try:
            oggi, sett = usato(u, time.time(), t0)
        finally:
            u.close()
        return "; ".join(superati(c, oggi, sett)) or None

    def vigila(stop: threading.Event, titolo: str, t0: float):
        ultimo = 0.0
        while not stop.is_set():
            try:
                msg = controlla(t0)
            except sqlite3.Error:  # database occupato un attimo: si riprova al giro dopo
                msg = None
            if msg and time.time() - ultimo >= RIPETI:
                avvisa("Limite di tempo superato", f"{msg}. Stai giocando a {titolo}.")
                ultimo = time.time()
            stop.wait(TICK)

    def avviata(info: dict):
        with lock:
            if run["stop"]:
                run["stop"].set()
            if not conf()["attivo"]:
                run["stop"] = None
                return
            run["stop"], run["t0"] = threading.Event(), time.time()
            threading.Thread(target=vigila, args=(run["stop"], info.get("title") or "questo gioco", run["t0"]),
                             daemon=True).start()

    def finita(info: dict | None = None):
        with lock:
            if run["stop"]:
                run["stop"].set()
                run["stop"] = None

    @gs.route("stato")
    def stato():
        c = conf()
        u = db()
        try:
            oggi, sett = usato(u, time.time(), run["t0"] if run["stop"] else None)
        finally:
            u.close()
        return {**c, "oggi": oggi, "settimana": sett, "superati": superati(c, oggi, sett), "in_corso": bool(run["stop"]),
                "profilo": gs.cfg.app.get("profile") or "default"}

    @gs.route("salva", methods=("POST",))
    def salva(body: dict = Body(...)):
        try:
            g, s = float(body.get("giorno_ore") or 0), float(body.get("settimana_ore") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "Ore non valide")
        if not (0 <= g <= 24) or not (0 <= s <= 168):
            raise HTTPException(400, "Ore fuori limite (max 24 al giorno, 168 a settimana)")
        gs.save_settings({"giorno_ore": g, "settimana_ore": s, "attivo": bool(body.get("attivo", True))})
        gs.settings.update({"giorno_ore": g, "settimana_ore": s, "attivo": bool(body.get("attivo", True))})
        return stato()

    gs.on("game_started", avviata)
    gs.on("game_stopped", finita)
