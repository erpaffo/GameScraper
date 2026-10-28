"""SteamGridDB: copertine, sfondi e logo mancanti, come fonte di scraping.

Va dopo le altre fonti (le non configurate si accodano da sole) e interroga solo i giochi a cui manca qualcosa di
quello che chiede la configurazione (media: cover, fanart, wheel). Chiave gratuita: steamgriddb.com › Preferenze › API.

POST /plugin-api/steamgriddb/chiave  {"chiave": "..."}   (vuota = rimuovi)
GET  /plugin-api/steamgriddb/stato   -> {"chiave": true|false}
GET  /plugin-api/steamgriddb/cerca?q=<titolo>&kind=cover|splash|screenshot
     -> {"game": nome trovato, "items": [{url, thumb, w, h, score}]}   (per «Cerca immagini…» nel menu del gioco)
"""

import os
from urllib.parse import quote

import httpx
from fastapi import Body, HTTPException

API = "https://www.steamgriddb.com/api/v2"
# tipo di media dell'app -> (percorso dell'API, parametri)
MEDIA = {
    "cover": ("grids", {"dimensions": "600x900", "types": "static"}),   # verticale, come le copertine delle scatole
    "fanart": ("heroes", {"types": "static"}),
    "wheel": ("logos", {"types": "static"}),
}
# tipo chiesto dalla finestra «Cerca immagini…» -> stesso schema; None = SteamGridDB non lo ha
CERCA = {"cover": MEDIA["cover"], "splash": MEDIA["fanart"], "screenshot": None}


def best_url(items: list[dict]) -> str | None:
    """Il piu' votato tra i risultati (SteamGridDB li ordina per punteggio, ma non lo garantisce): url https, mai vuoto."""
    ok = [i for i in items if str(i.get("url", "")).startswith("https://")]
    return max(ok, key=lambda i: i.get("score") or 0)["url"] if ok else None


def register(gs):
    from pozzo import config as C
    from pozzo import matching
    from pozzo.net import RateLimiter, Stop, request
    from pozzo.sources.base import Result, Source

    def chiave(cfg) -> str:
        return (C.read_settings(cfg).get("plugins", {}).get("steamgriddb", {}).get("chiave")
                or os.environ.get("STEAMGRIDDB_KEY", ""))

    class SteamGridDb(Source):
        name = "steamgriddb"
        provides = {f"media:{m}" for m in MEDIA}
        concurrency = 2

        def __init__(self, ctx):
            super().__init__(ctx)
            self.key = chiave(self.cfg)
            self.limiter = RateLimiter(3, 2)
            self.media_limiter = RateLimiter(0, 4)

        async def setup(self, platforms) -> bool:
            if not self.key:
                self.log("SteamGridDB disattivato: manca la chiave (Impostazioni › SteamGridDB)")
                return False
            return True

        async def _get(self, path: str, **params):
            r = await request(self.client, "GET", f"{API}/{path}", limiter=self.limiter, cache_ttl=14 * 86400, ok404=True,
                              headers={"Authorization": f"Bearer {self.key}"}, params=params or None)
            if r is None:
                return None
            if r.status_code in (401, 403):
                raise Stop("SteamGridDB: chiave non valida")
            if r.status_code != 200:
                raise RuntimeError(f"SteamGridDB HTTP {r.status_code}")
            j = r.json()
            return j.get("data") if j.get("success", True) else None

        async def lookup(self, game) -> Result | None:
            want = [m for m in MEDIA if m in self.cfg.media and game.missing({f"media:{m}"})]
            if not want:
                return None
            from urllib.parse import quote
            found = await self._get(f"search/autocomplete/{quote(game.search_name, safe='')}")
            hit, _ = matching.best(game.search_name, found or [], key=lambda it: it.get("name", ""), min_score=self.cfg.min_match)
            if not hit:
                return None
            res = Result()
            for m in want:
                path, params = MEDIA[m]
                url = best_url(await self._get(f"{path}/game/{hit['id']}", **params) or [])
                if url:
                    res.media[m] = url
            return res if res.media else None

    gs.add_source(SteamGridDb)
    gs.steamgriddb = SteamGridDb   # per i test

    @gs.route("stato")
    def stato():
        return {"chiave": bool(chiave(gs.cfg))}

    @gs.route("chiave", methods=("POST",))
    def imposta(body: dict = Body(...)):
        k = str(body.get("chiave") or "").strip()[:200]
        gs.save_settings({"chiave": k})
        gs.settings["chiave"] = k
        return {"chiave": bool(k)}

    @gs.route("cerca")
    def cerca(q: str = "", kind: str = "cover"):
        """Candidati per «Cerca immagini…»: prima il gioco che SteamGridDB riconosce, poi fino a 24 immagini.
        Vuoto se `kind` non esiste (screenshot) o se il gioco non si trova: la finestra lascia incollare un URL."""
        key = chiave(gs.cfg)
        if not key:
            raise HTTPException(400, "Serve la chiave di SteamGridDB (Impostazioni › SteamGridDB)")
        spec = CERCA.get(str(kind), CERCA["cover"])
        if spec is None or not str(q).strip():
            return {"game": None, "items": []}
        path, params = spec
        try:
            with httpx.Client(timeout=10, headers={"Authorization": "Bearer " + key}) as c:
                found = c.get(f"{API}/search/autocomplete/{quote(str(q).strip(), safe='')}").json().get("data") or []
                if not found:
                    return {"game": None, "items": []}
                hit = found[0]
                r = c.get(f"{API}/{path}/game/{hit['id']}", params=params).json().get("data") or []
        except (httpx.HTTPError, ValueError) as e:  # rete assente o risposta illeggibile
            raise HTTPException(502, f"SteamGridDB non risponde ({type(e).__name__})")
        items = [{"url": i["url"], "thumb": i.get("thumb") or i["url"], "w": i.get("width"), "h": i.get("height"),
                  "score": i.get("score") or 0}
                 for i in r if str(i.get("url", "")).startswith("https://")]
        items.sort(key=lambda i: -i["score"])
        return {"game": hit.get("name"), "items": items[:24]}
