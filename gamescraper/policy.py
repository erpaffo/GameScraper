"""Regole di "buona educazione" verso ogni sito e contabilita' delle richieste.

Per ogni fonte online si definiscono:
  rate      richieste al secondo (massimo)
  daily     richieste al giorno   (0 = nessun limite proprio)
  monthly   richieste al mese     (0 = nessun limite proprio)
  reserve   richieste da non usare mai (margine di sicurezza)
  errors    errori consecutivi (429/5xx/timeout) prima di una pausa
  cooldown  durata della pausa in secondi; dopo 3 pause di fila la fonte si ferma per questa sessione

I contatori sono salvati nella cache (tabella usage): valgono tra un'esecuzione e l'altra,
anche dopo un riavvio. I valori si possono cambiare in config.toml ([limits.<fonte>]) o
dall'app (Impostazioni > Fonti). Le quote dichiarate dai siti (ScreenScraper, TheGamesDB)
vengono comunque lette dalle loro risposte e hanno la precedenza.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime

DEFAULTS = {
    # fonte          rate   daily   monthly reserve errors cooldown
    "screenscraper": (0.8, 0, 0, 0, 5, 300),  # thread e quota letti dall'account
    "igdb": (4.0, 0, 0, 0, 5, 120),
    "thegamesdb": (1.0, 0, 1000, 20, 3, 600),  # API: quota mensile (la copia completa non ne consuma)
    "rawg": (2.0, 700, 19000, 200, 3, 600),  # piano gratuito: 20.000 richieste/mese
    "arcadedb": (1.0, 0, 0, 0, 3, 300),
    "wikipedia": (3.0, 0, 0, 0, 5, 300),
    "hltb": (0.8, 20000, 0, 0, 8, 900),  # HowLongToBeat: ~50 ricerche al minuto (oltre risponde 429)
    "itad": (2.0, 2000, 0, 0, 5, 600),  # IsThereAnyDeal (prezzi della lista dei desideri)
    "steam_store": (1.0, 2000, 0, 0, 5, 600),  # prezzi e lista desideri di Steam
    "freegames": (0.5, 200, 0, 0, 3, 900),  # pagine dei giochi gratis  # gratuita, senza quota: solo un ritmo cortese
    "netmeta": (2.0, 0, 0, 0, 5, 300),  # pagine degli store online (copertine e descrizioni): solo un ritmo cortese
    "steam_reviews": (1.0, 2000, 0, 0, 3, 600),
    "launchbox_images": (4.0, 0, 0, 0, 5, 300),
    "libretro_thumbs": (12.0, 0, 0, 0, 8, 120),
}


@dataclass(frozen=True)
class Policy:
    source: str
    rate: float = 2.0
    daily: int = 0
    monthly: int = 0
    reserve: int = 0
    errors: int = 5
    cooldown: float = 300


def policy_for(cfg, source: str) -> Policy:
    d = DEFAULTS.get(source, (2.0, 0, 0, 0, 5, 300))
    p = Policy(source, *d)
    over = (getattr(cfg, "limits", {}) or {}).get(source, {})
    allowed = {k: over[k] for k in ("rate", "daily", "monthly", "reserve", "errors", "cooldown") if k in over}
    return replace(p, **allowed) if allowed else p


class Ledger:
    """Contatore persistente delle richieste per fonte, per giorno e per mese."""

    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS usage(source TEXT, period TEXT, count INT, "
                        "PRIMARY KEY(source, period))")
        self.db.commit()
        self.lock = threading.Lock()
        self._pending: dict[tuple[str, str], int] = {}
        self._last = time.monotonic()

    @staticmethod
    def periods() -> tuple[str, str]:
        now = datetime.now()
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

    def used(self, source: str) -> tuple[int, int]:
        day, month = self.periods()
        with self.lock:
            self._flush()
            r = dict(self.db.execute("SELECT period, count FROM usage WHERE source=? AND period IN (?,?)",
                                     (source, day, month)).fetchall())
        return r.get(day, 0), r.get(month, 0)

    def spend(self, source: str, n: int = 1):
        day, month = self.periods()
        with self.lock:
            for per in (day, month):
                self._pending[(source, per)] = self._pending.get((source, per), 0) + n
            if time.monotonic() - self._last > 2:
                self._flush()

    def _flush(self):
        if not self._pending:
            return
        for (src, per), n in self._pending.items():
            self.db.execute("INSERT INTO usage VALUES(?,?,?) ON CONFLICT(source, period) "
                            "DO UPDATE SET count = count + excluded.count", (src, per, n))
        self.db.commit()
        self._pending.clear()
        self._last = time.monotonic()

    def flush(self):
        with self.lock:
            self._flush()

    def remaining(self, p: Policy) -> tuple[int | None, int | None]:
        d, m = self.used(p.source)
        return (p.daily - d - p.reserve if p.daily else None, p.monthly - m - p.reserve if p.monthly else None)

    def snapshot(self) -> dict:
        day, month = self.periods()
        with self.lock:
            self._flush()
            rows = self.db.execute("SELECT source, period, count FROM usage WHERE period IN (?,?)", (day, month)).fetchall()
        out: dict = {}
        for src, per, n in rows:
            out.setdefault(src, {"today": 0, "month": 0})["today" if per == day else "month"] = n
        return out

    def close(self):
        self.flush()
        self.db.close()
