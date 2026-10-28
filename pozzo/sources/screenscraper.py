"""ScreenScraper (api2) - ricerca per checksum/nome file esatto, con fallback per nome.

Differenze rispetto all'originale:
- thread letti da ssuserInfos.php (maxthreads dell'account) e richieste realmente parallele
- l'intervallo minimo di 1,2 s per thread e' rispettato con un rate limiter globale
  (threads / 1.2 richieste al secondo) invece di bloccare ogni thread anche sui media
- i media sono scaricati in parallelo (max `threads` connessioni) e SOLO quelli che
  mancano dopo le sorgenti precedenti
- quota giornaliera monitorata: ci si ferma pulitamente prima di sforarla
- credenziali sviluppatore proprie (config) invece di quelle offuscate di Skyscraper
"""

from __future__ import annotations

from .. import matching
from ..net import RateLimiter, Stop, request
from ..scanner import Game
from .base import Result, Source, pick_region

API = "https://api.screenscraper.fr/api2/"

MEDIA_TYPES = {
    "cover": ["box-2D"],
    "screenshot": ["ss"],
    "title": ["sstitle"],
    "wheel": ["wheel-hd", "wheel", "wheel-carbon"],
    "marquee": ["screenmarquee", "screenmarqueesmall", "marquee"],
    "video": ["video-normalized", "video"],
    "box3d": ["box-3D"],
    "fanart": ["fanart"],
}
ARCADE_COVER = ["flyer", "box-2D"]
FATAL = {
    "API totalement fermé": "API ScreenScraper chiusa",
    "Le logiciel de scrape utilisé a été blacklisté": "software blacklistato da ScreenScraper (controlla devid)",
    "Votre quota de scrape est dépassé": "quota giornaliera ScreenScraper esaurita",
    "Erreur de login": "credenziali ScreenScraper errate",
    "Erreur de identifiants développeur": "credenziali sviluppatore ScreenScraper errate",
}


class ScreenScraper(Source):
    name = "screenscraper"
    provides = {"title", "description", "developer", "publisher", "genres", "release_date", "players",
                "rating", "ages", *(f"media:{m}" for m in MEDIA_TYPES)}

    def __init__(self, ctx):
        super().__init__(ctx)
        c = self.cfg.ss
        self.auth = {
            "devid": c.get("devid", ""), "devpassword": c.get("devpassword", ""),
            "softname": c.get("softname", "PozzoLauncher"), "output": "json",
            "ssid": c.get("user", ""), "sspassword": c.get("password", ""),
        }
        self.remaining = None
        self.reserve = int(c.get("quota_reserve", 50))
        # richieste "KO" (gioco non trovato): ScreenScraper ha un limite giornaliero separato e piu' basso
        self.ko_remaining = None
        self.ko_reserve = int(c.get("ko_reserve", 100))
        # ricerca per nome dopo un checksum fallito: "missing" = solo per i giochi senza alcun dato
        # (ogni ricerca costa una richiesta in piu' e spesso un KO); true = sempre; false = mai
        fb = c.get("search_fallback", "missing")
        self.search_fallback = "missing" if fb == "missing" else bool(fb)
        self.used = 0

    def supports(self, platform) -> bool:
        return platform.ss is not None

    async def setup(self, platforms) -> bool:
        if not (self.auth["devid"] and self.auth["devpassword"]):
            self.log("[yellow]ScreenScraper disattivato: servono 'devid' e 'devpassword' (vedi README).[/]")
            return False
        if not self.auth["ssid"]:
            self.log("[yellow]ScreenScraper: nessun utente configurato, 1 thread e quota ridotta.[/]")
        r = await request(self.client, "GET", API + "ssuserInfos.php", params=self.auth)
        threads = 1
        if r is not None and r.status_code == 200:
            try:
                u = r.json()["response"]["ssuser"]
                threads = int(u.get("maxthreads") or 1)
                self.remaining = int(u.get("maxrequestsperday") or 0) - int(u.get("requeststoday") or 0)
                if u.get("maxrequestskoperday"):
                    self.ko_remaining = int(u["maxrequestskoperday"]) - int(u.get("requestskotoday") or 0)
                self.log(f"ScreenScraper: utente [bold]{u.get('id')}[/], {threads} thread, "
                         f"{self.remaining:,} richieste rimaste oggi"
                         + (f", {self.ko_remaining:,} 'non trovati' consentiti" if self.ko_remaining is not None else ""))
                if self.remaining <= self.reserve:
                    self.log("[yellow]ScreenScraper: quota di oggi gia' esaurita, riprova domani.[/]")
                    return False
                if self.ko_remaining is not None and self.ko_remaining <= self.ko_reserve:
                    self.log("[yellow]ScreenScraper: limite giornaliero dei 'non trovati' raggiunto, riprova domani.[/]")
                    return False
            except (ValueError, KeyError):
                self._check_fatal(r.text)
                self.log(f"[red]ScreenScraper: risposta inattesa: {r.text[:200]}[/]")
                return False
        elif r is not None:
            self._check_fatal(r.text)
            self.log(f"[red]ScreenScraper: HTTP {r.status_code} {r.text[:200]}[/]")
            return False
        threads = min(threads, int(self.cfg.ss.get("threads") or threads))
        self.concurrency = threads
        # 1 richiesta ogni 1,2 s per thread, come chiesto da ScreenScraper
        self.api_limiter = RateLimiter(threads / 1.2, threads)
        # di default i download dei media passano dallo STESSO limitatore delle API: mai piu'
        # connessioni contemporanee dei thread concessi (come lo Skyscraper originale)
        if self.cfg.ss.get("media_parallel", False):
            self.media_limiter = RateLimiter(0, threads)
        else:
            self.media_limiter = self.api_limiter
        return True

    def _check_fatal(self, text: str):
        for needle, msg in FATAL.items():
            if needle in text[:2048]:
                raise Stop(msg)

    def _spend(self):
        # controllo PRIMA di ogni richiesta: ci si ferma con margine, mai oltre il limite
        if self.remaining is not None and self.remaining <= self.reserve:
            raise Stop(f"quota giornaliera ScreenScraper quasi esaurita (tengo {self.reserve} richieste di riserva)")
        if self.ko_remaining is not None and self.ko_remaining <= self.ko_reserve:
            raise Stop(f"limite giornaliero dei 'non trovati' quasi raggiunto (riserva {self.ko_reserve})")
        if self.remaining is not None:
            self.remaining -= 1
        self.used += 1

    async def _call(self, endpoint: str, params: dict) -> dict | None:
        self._spend()
        r = await request(self.client, "GET", API + endpoint, limiter=self.api_limiter,
                          params={**self.auth, **params}, ok404=False)
        if r is None:
            return None
        text = r.text
        self._check_fatal(text)
        if "non trouvée" in text[:300] or r.status_code in (400, 404):
            if self.ko_remaining is not None:
                self.ko_remaining -= 1
            return None
        if r.status_code in (401, 403, 423, 426, 429, 430, 431):
            raise Stop(f"ScreenScraper HTTP {r.status_code}: {text[:150]}")
        try:
            data = r.json()
        except ValueError:
            # JSON malformato occasionale (come gestito nell'originale)
            import json
            try:
                data = json.loads(text.replace("],\n\t\t}", "]\n\t\t}"))
            except ValueError:
                return None
        u = data.get("response", {}).get("ssuser") or {}
        if u.get("requeststoday") and u.get("maxrequestsperday"):
            self.remaining = int(u["maxrequestsperday"]) - int(u["requeststoday"])
        if u.get("maxrequestskoperday"):
            self.ko_remaining = int(u["maxrequestskoperday"]) - int(u.get("requestskotoday") or 0)
        return data.get("response")

    async def lookup(self, game: Game) -> Result | None:
        # gli hash possono venire dal file letto o dal DAT (identificazione per nome, file non letto)
        p = {"systemeid": game.platform.ss, "romtype": "rom", "romnom": game.path.name}
        if game.rom_size:
            p["romtaille"] = game.rom_size
        for k, v in (("crc", game.best_crc), ("md5", game.md5 or game.dat_md5), ("sha1", game.sha1 or game.dat_sha1)):
            if v:
                p[k] = v
        resp = await self._call("jeuInfos.php", p)
        jeu = (resp or {}).get("jeu")
        fallback = self.search_fallback is True or (self.search_fallback == "missing" and "title" not in game.origin)
        if not jeu and fallback:
            resp = await self._call("jeuRecherche.php", {"systemeid": game.platform.ss, "recherche": game.search_name})
            jeux = [j for j in (resp or {}).get("jeux", []) if j.get("id")]
            jeu, _ = matching.best(game.search_name, jeux,
                                   key=lambda j: [n.get("text") for n in j.get("noms", [])],
                                   min_score=self.cfg.min_match)
            if not jeux and self.ko_remaining is not None and resp is not None:
                self.ko_remaining -= 1  # prudenza: una ricerca vuota potrebbe contare come "non trovato"
        if not jeu:
            return None
        return self._parse(jeu, game)

    async def close(self):
        if self.used:
            rest = f"{self.remaining:,} richieste" if self.remaining is not None else "?"
            ko = f", {self.ko_remaining:,} 'non trovati'" if self.ko_remaining is not None else ""
            self.log(f"ScreenScraper: usate {self.used:,} richieste in questa sessione; restano oggi {rest}{ko}")

    def _parse(self, j: dict, game: Game) -> Result:
        regions, langs = self.cfg.regions, self.cfg.languages
        title = pick_region(j.get("noms", []), regions)
        if not title or "notgame" in title.lower() or title.lower() == "zzz":
            return Result()
        f = {"title": title}
        syn = j.get("synopsis") or []
        f["description"] = pick_region(syn, langs + ["en"], key_region="langue")
        f["developer"] = (j.get("developpeur") or {}).get("text")
        f["publisher"] = (j.get("editeur") or {}).get("text")
        f["players"] = (j.get("joueurs") or {}).get("text")
        note = (j.get("note") or {}).get("text")
        if note and note.replace(".", "").isdigit():
            f["rating"] = round(float(note) / 20, 2)
        f["release_date"] = pick_region(j.get("dates", []), regions)
        genres = []
        for g in j.get("genres", []) or []:
            n = pick_region(g.get("noms", []), langs + ["en"], key_region="langue")
            if n and n not in genres:
                genres.append(n)
        f["genres"] = genres
        for c in j.get("classifications", []) or []:
            if c.get("type") in ("PEGI", "ESRB"):
                f["ages"] = f"{c['type']} {c.get('text')}"
                break
        f["ss_id"] = j.get("id")

        res = Result(fields=f)
        medias = j.get("medias", []) or []
        for mtype, ss_types in MEDIA_TYPES.items():
            if mtype == "cover" and game.platform.arcade:
                ss_types = ARCADE_COVER
            for t in ss_types:
                cands = [m for m in medias if m.get("type") == t and m.get("url")]
                if cands:
                    res.media[mtype] = pick_region(cands, regions, key_text="url")
                    break
        return res
