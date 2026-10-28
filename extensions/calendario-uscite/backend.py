"""Calendario uscite: prossime uscite da RAWG (chiave in Impostazioni › Fonti), per la lista dei desideri e per i tuoi generi.

GET /plugin-api/calendario-uscite/uscite[?giorni=180]
    -> {"desideri": [...], "prossime": [...], "generi": ["Azione", ...]}   ogni voce: {titolo, data, giorni, piattaforme, copertina, generi}

Le risposte restano in memoria 6 ore: la lista dei desideri costa una richiesta a titolo, e il piano gratuito di RAWG
ha 20.000 richieste al mese.
"""

import re
import sqlite3
import time
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import httpx
from fastapi import HTTPException

API = "https://api.rawg.io/api/games"
TTL = 6 * 3600
MAX_DESIDERI = 40
# nome del genere (senza maiuscole/accenti, in inglese e in italiano) -> slug di RAWG
GENERI = {
    "action": "action", "azione": "action", "adventure": "adventure", "avventura": "adventure",
    "rpg": "role-playing-games-rpg", "role-playing": "role-playing-games-rpg", "role playing": "role-playing-games-rpg",
    "gioco di ruolo": "role-playing-games-rpg", "strategy": "strategy", "strategia": "strategy",
    "shooter": "shooter", "sparatutto": "shooter", "puzzle": "puzzle", "rompicapo": "puzzle",
    "platformer": "platformer", "platform": "platformer", "piattaforme": "platformer", "racing": "racing", "corse": "racing",
    "sports": "sports", "sport": "sports", "simulation": "simulation", "simulazione": "simulation",
    "fighting": "fighting", "picchiaduro": "fighting", "arcade": "arcade", "indie": "indie",
}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def generi_preferiti(db: sqlite3.Connection, quanti: int = 3) -> list[str]:
    """Slug RAWG dei generi piu' presenti tra i giochi posseduti."""
    n = Counter()
    for (gen,) in db.execute("SELECT genres FROM games WHERE is_primary=1 AND COALESCE(store,'') != 'online' AND genres != ''"):
        for g in {GENERI.get(fold(x)) for x in re.split(r"[,/;]", gen)}:
            if g:
                n[g] += 1
    return [g for g, _ in n.most_common(quanti)]


def voce(it: dict, oggi: date) -> dict | None:
    try:
        d = date.fromisoformat(it.get("released") or "")
    except ValueError:
        return None  # senza data precisa non e' una «uscita» da mettere in calendario
    return {"titolo": it.get("name") or "?", "data": d.isoformat(), "giorni": (d - oggi).days,
            "piattaforme": [p["platform"]["name"] for p in (it.get("platforms") or []) if p.get("platform")][:6],
            "copertina": it.get("background_image"), "generi": [g["name"] for g in it.get("genres") or [] if g.get("name")]}


def prossime(get, key: str, oggi: date, giorni: int, slugs: list[str]) -> list[dict]:
    params = {"key": key, "dates": f"{oggi.isoformat()},{(oggi + timedelta(days=giorni)).isoformat()}",
              "ordering": "-added", "page_size": 40}
    if slugs:
        params["genres"] = ",".join(slugs)
    out = [v for it in get(API, params).get("results", []) if (v := voce(it, oggi))]
    return sorted(out, key=lambda v: v["data"])


def desideri(get, key: str, titoli: list[str], oggi: date) -> list[dict]:
    """Per ogni titolo della lista dei desideri cerca l'uscita: vale solo se il nome coincide e la data e' futura."""
    out = []
    for t in titoli[:MAX_DESIDERI]:
        res = get(API, {"key": key, "search": t, "search_precise": "true", "page_size": 5}).get("results", [])
        hit = next((it for it in res if fold(it.get("name", "")) == fold(t)), None)
        v = hit and voce(hit, oggi)
        if v and v["giorni"] >= 0:
            out.append({**v, "titolo": t})
    return sorted(out, key=lambda v: v["data"])


def register(gs):
    cache = Path(gs.cfg.cache)
    memo: dict = {}

    def get(url, params):
        r = httpx.get(url, params=params, timeout=20, headers={"User-Agent": "GameScraper"})
        if r.status_code in (401, 403):
            raise HTTPException(400, "Chiave RAWG non valida o quota esaurita (Impostazioni › Fonti)")
        if r.status_code != 200:
            raise HTTPException(502, f"RAWG: HTTP {r.status_code}")
        return r.json()

    @gs.route("uscite")
    def uscite(giorni: int = 180):
        key = (getattr(gs.cfg, "rawg", {}) or {}).get("api_key", "")
        if not key:
            raise HTTPException(400, "Serve la chiave gratuita di RAWG: Impostazioni › Fonti (rawg.io/apidocs)")
        if not 7 <= giorni <= 730:
            raise HTTPException(400, "Intervallo non valido (7-730 giorni)")
        from gamescraper.sync import profile_path
        lib = sqlite3.connect(cache / "library.db") if (cache / "library.db").exists() else None
        user = sqlite3.connect(profile_path(gs.cfg, gs.cfg.app.get("profile") or "default"))
        try:
            slugs = generi_preferiti(lib) if lib else []
            try:
                titoli = [t for (t,) in user.execute("SELECT title FROM wishlist ORDER BY added DESC") if t]
            except sqlite3.Error:
                titoli = []
        finally:
            user.close()
            if lib:
                lib.close()
        ck = (giorni, tuple(slugs), tuple(titoli), date.today())
        hit = memo.get("r")
        if hit and hit[0] == ck and time.time() - hit[1] < TTL:
            return hit[2]
        oggi = date.today()
        r = {"desideri": desideri(get, key, titoli, oggi), "prossime": prossime(get, key, oggi, giorni, slugs),
             "generi": slugs}
        memo["r"] = (ck, time.time(), r)
        return r
