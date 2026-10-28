"""SteamGridDB: copertine, sfondi e logo mancanti, come fonte di scraping.

Va dopo le altre fonti (le non configurate si accodano da sole) e interroga solo i giochi a cui manca qualcosa di
quello che chiede la configurazione (media: cover, fanart, wheel). Chiave gratuita: steamgriddb.com › Preferenze › API.

POST /plugin-api/steamgriddb/chiave  {"chiave": "..."}   (vuota = rimuovi)
GET  /plugin-api/steamgriddb/stato   -> {"chiave": true|false}
"""

import os

from fastapi import Body

API = "https://www.steamgriddb.com/api/v2"
# tipo di media dell'app -> (percorso dell'API, parametri)
MEDIA = {
    "cover": ("grids", {"dimensions": "600x900", "types": "static"}),   # verticale, come le copertine delle scatole
    "fanart": ("heroes", {"types": "static"}),
    "wheel": ("logos", {"types": "static"}),
}


def best_url(items: list[dict]) -> str | None:
    """Il piu' votato tra i risultati (SteamGridDB li ordina per punteggio, ma non lo garantisce): url https, mai vuoto."""
    ok = [i for i in items if str(i.get("url", "")).startswith("https://")]
    return max(ok, key=lambda i: i.get("score") or 0)["url"] if ok else None


def register(gs):
    from gamescraper import config as C
    from gamescraper import matching
    from gamescraper.net import RateLimiter, Stop, request
    from gamescraper.sources.base import Result, Source

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
