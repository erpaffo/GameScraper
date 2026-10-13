"""RAWG (rawg.io): valutazioni degli utenti e voto Metacritic della critica, generi, date.

Serve una chiave gratuita (https://rawg.io/apidocs); il piano gratuito ha 20.000 richieste al mese,
quindi la fonte ha un budget giornaliero e mensile (policy "rawg") e ogni risposta resta in cache
30 giorni. Conviene metterla DOPO le fonti offline: interroga solo i giochi ancora senza valutazione.
"""

from __future__ import annotations

from .. import matching
from ..net import RateLimiter, Stop, request
from ..platforms import norm
from ..scanner import Game
from .base import Result, Source

API = "https://api.rawg.io/api/games"
# nomi delle piattaforme su RAWG (normalizzati) per scartare omonimi su altre console
RAWG_NAMES = {
    "nes": ["nes"], "snes": ["snes"], "n64": ["nintendo64"], "gb": ["gameboy"], "gbc": ["gameboycolor"],
    "gba": ["gameboyadvance"], "nds": ["nintendods"], "3ds": ["nintendo3ds"], "gc": ["gamecube"], "wii": ["wii"],
    "wiiu": ["wiiu"], "switch": ["nintendoswitch"], "megadrive": ["genesis"], "mastersystem": ["segamastersystem"],
    "gamegear": ["gamegear"], "segacd": ["segacd"], "sega32x": ["sega32x"], "saturn": ["segasaturn"],
    "dreamcast": ["dreamcast"], "psx": ["playstation"], "ps2": ["playstation2"], "ps3": ["playstation3"],
    "ps4": ["playstation4"], "psp": ["psp"], "psvita": ["psvita"], "xbox": ["xbox"], "xbox360": ["xbox360"],
    "windows": ["pc"], "dos": ["pc"], "atari2600": ["atari2600"], "atari7800": ["atari7800"], "atarilynx": ["atarilynx"],
    "atarijaguar": ["jaguar"], "neogeo": ["neogeo"], "3do": ["3do"], "pcengine": ["turbografx16", "pcengine"],
    "amiga": ["commodoreamiga"], "c64": ["commodore64"],
}


class Rawg(Source):
    name = "rawg"
    provides = {"rating", "genres", "release_date", "media:fanart"}
    concurrency = 2

    def __init__(self, ctx):
        super().__init__(ctx)
        self.key = (getattr(self.cfg, "rawg", {}) or {}).get("api_key", "")
        self.limiter = RateLimiter(2, 2)
        self.media_limiter = RateLimiter(0, 4)

    async def setup(self, platforms) -> bool:
        if not self.key:
            self.log("RAWG disattivato: serve una chiave gratuita (Impostazioni › Fonti)")
            return False
        return True

    def supports(self, platform) -> bool:
        return True

    async def lookup(self, game: Game) -> Result | None:
        r = await request(self.client, "GET", API, limiter=self.limiter, cache_ttl=30 * 86400, ok404=False,
                          params={"key": self.key, "search": game.search_name, "page_size": 8})
        if r is None:
            return None
        if r.status_code in (401, 403):
            raise Stop("RAWG: chiave non valida o quota esaurita")
        if r.status_code != 200:
            raise RuntimeError(f"RAWG HTTP {r.status_code}")
        want = set(RAWG_NAMES.get(game.platform.id, []))
        cands = []
        for it in r.json().get("results", []):
            plats = {norm((p.get("platform") or {}).get("name", "")) for p in it.get("platforms") or []}
            if not want or plats & want:
                cands.append(it)
        hit, _ = matching.best(game.search_name, cands, key=lambda it: it.get("name", ""), min_score=self.cfg.min_match)
        if not hit:
            return None
        f = {"title": hit.get("name"), "release_date": hit.get("released"),
             "genres": [g["name"] for g in hit.get("genres") or [] if g.get("name")], "rawg_slug": hit.get("slug")}
        if hit.get("rating") and hit.get("ratings_count"):
            f["rating"] = round(hit["rating"] / 5, 3)
            f["rating_count"] = hit["ratings_count"]
        if hit.get("metacritic"):
            f["rating_critics"] = round(hit["metacritic"] / 100, 2)
        res = Result(fields={k: v for k, v in f.items() if v not in (None, "", [])})
        if hit.get("background_image"):
            res.media = {"fanart": hit["background_image"]}
        return res
