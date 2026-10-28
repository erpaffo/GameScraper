"""HowLongToBeat: quanto dura un gioco (storia, storia + extra, completista).

Il sito non ha un'API pubblica: si usa la libreria `howlongtobeatpy`, che ricava da sola l'indirizzo
di ricerca del sito. Si interroga solo la versione principale di ogni gioco (le altre versioni e i
duplicati prendono lo stesso dato), a ritmo lento, e ogni risultato resta in cache:
trovato 180 giorni, non trovato 30 giorni. Il risultato entra nella libreria alla ricostruzione.

Velocita': HowLongToBeat accetta circa 50 ricerche al minuto. Per questo si fa prima un passaggio
sull'API ufficiale di IGDB (tempi di completamento, 500 giochi per richiesta) per tutti i giochi gia'
collegati a IGDB, e HowLongToBeat cerca solo i rimanenti: indirizzo e token una sola volta, piu'
ricerche in parallelo con ritmo adattivo (rallenta da solo ai 429), e ogni ricerca riempie anche gli
altri giochi della libreria con lo stesso nome.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from .matching import normalize

FOUND_TTL, MISS_TTL = 180 * 86400, 30 * 86400
# nomi delle piattaforme su HowLongToBeat (per preferire il gioco giusto tra gli omonimi)
HLTB_PLATFORMS = {
    "windows": "pc", "dos": "pc", "nes": "nes", "snes": "super nintendo", "n64": "nintendo 64", "gb": "game boy",
    "gbc": "game boy color", "gba": "game boy advance", "nds": "nintendo ds", "3ds": "nintendo 3ds", "gc": "gamecube",
    "wii": "wii", "wiiu": "wii u", "switch": "nintendo switch", "psx": "playstation", "ps2": "playstation 2",
    "ps3": "playstation 3", "ps4": "playstation 4", "ps5": "playstation 5", "psp": "playstation portable",
    "psvita": "playstation vita",
    "megadrive": "sega mega drive", "mastersystem": "sega master system", "gamegear": "sega game gear",
    "saturn": "sega saturn", "dreamcast": "dreamcast", "segacd": "sega cd", "xbox": "xbox", "xbox360": "xbox 360",
    "pcengine": "turbografx", "neogeo": "neo geo", "arcade": "arcade",
}


def cache_key(title: str, platform: str) -> str:
    return f"hltb:{platform}:{normalize(title)}"


def lookup(title: str, platform: str, search=None) -> dict:
    """Cerca un gioco; {} se non trovato. `search` permette di sostituire la ricerca nei test."""
    if search is None:
        from howlongtobeatpy import HowLongToBeat
        search = HowLongToBeat(0.4).search
    res = search(title) or []
    want = HLTB_PLATFORMS.get(platform, "")
    best, best_s = None, 0.0
    nums = lambda t: {w for w in normalize(t).split() if w.isdigit()}  # noqa: E731 - "VI" diventa "6"
    want_nums = nums(title)
    for r in res:
        sim = float(getattr(r, "similarity", 0) or 0)
        if nums(r.game_name or "") != want_nums:
            continue  # capitolo diverso ("Civilization V" non e' "Civilization VI")
        plats = " ".join(getattr(r, "profile_platforms", None) or []).lower()
        s = sim + (0.15 if want and want in plats else 0) + (0.2 if normalize(r.game_name or "") == normalize(title) else 0)
        if sim >= 0.75 and s > best_s and (r.main_story or r.main_extra or r.completionist):
            best, best_s = r, s
    if not best:
        return {}
    h = lambda v: round(float(v), 1) if v else None  # noqa: E731
    return {"main": h(best.main_story), "extra": h(best.main_extra), "complete": h(best.completionist),
            "name": best.game_name, "url": best.game_web_link, "id": best.game_id}


_UNLIKELY = re.compile(r"\((beta|proto|prototype|demo|sample|kiosk|debug|unl|pirate|hack|aftermarket|test program)[^)]*\)"
                       r"|\[b\]|\[h\]", re.I)
_COMPILATION = re.compile(r"\b\d+\s*(?:games\s*)?in\s*1\b|double pack|\bcompilation\b", re.I)


def targets(cache_folder, with_keys: bool = False) -> list:
    """(titolo, piattaforma) delle versioni principali in libreria, senza i giochi solo online."""
    p = Path(cache_folder) / "library.db"
    if not p.exists():
        return []
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT title, platform, key, canonical, found, rating_count, store_playtime FROM games WHERE is_primary=1 "
                          "AND (store IS NULL OR store != 'online') AND title IS NOT NULL").fetchall()
    finally:
        db.close()
    # niente ricerche per beta/demo/prototipi/non licenziati, raccolte "2 in 1" e giochi che nessuna fonte
    # ha riconosciuto: HowLongToBeat non li ha quasi mai
    rows = [r for r in rows if r[4] and not _UNLIKELY.search(r[3] or "") and not _COMPILATION.search(r[0] or "")]
    # prima i giochi dell'utente (giocati, preferiti, con uno stato), poi i piu' votati
    mine = set()
    u = Path(cache_folder) / "userdata.db"
    if u.exists():
        ud = sqlite3.connect(f"file:{u}?mode=ro", uri=True)
        try:
            cols = {c[1] for c in ud.execute("PRAGMA table_info(user_games)")}
            cond = "playtime > 0 OR favorite = 1" + (" OR (status IS NOT NULL AND status != '')" if "status" in cols else "")
            mine = {k for (k,) in ud.execute(f"SELECT key FROM user_games WHERE {cond}")}
        finally:
            ud.close()
    rows.sort(key=lambda r: (r[2] not in mine and not r[6], -(r[6] or 0), -(r[5] or 0)))
    rows = [(t, pl, k) for t, pl, k, *_ in rows]
    if with_keys:
        seen, out = set(), []
        for t, pl, k in rows:
            if t and (t, pl) not in seen:
                seen.add((t, pl))
                out.append((t, pl, k))
        return out
    return list(dict.fromkeys((t, pl) for t, pl, _ in rows if t))


def igdb_slugs(cache_folder) -> dict[str, str]:
    """Versione principale -> slug IGDB (dagli scraping gia' fatti, anche di un'altra versione del gioco)."""
    lib = Path(cache_folder) / "library.db"
    if not lib.exists():
        return {}
    db = sqlite3.connect(f"file:{lib}?mode=ro", uri=True)
    try:
        grp_primary, key_grp = {}, {}
        for key, grp, prim in db.execute("SELECT key, grp, is_primary FROM games"):
            key_grp[key] = grp
            if prim:
                grp_primary[grp] = key
    finally:
        db.close()
    c = sqlite3.connect(f"file:{Path(cache_folder) / 'cache.db'}?mode=ro", uri=True)
    try:
        out: dict[str, str] = {}
        for key, url in c.execute("SELECT key, value FROM resources WHERE source='igdb' AND field='igdb_url'"):
            prim = grp_primary.get(key_grp.get(key))
            slug = str(url).strip('"').rstrip("/").rsplit("/", 1)[-1]
            if prim and slug and prim not in out:
                out[prim] = slug
    finally:
        c.close()
    return out


def igdb_pass(cfg, cache, todo: list, log=print) -> set:
    """Tempi di completamento dall'API ufficiale di IGDB (game_time_to_beats), 500 giochi per richiesta:
    pochi secondi per tutta la libreria. Ritorna le chiavi cache riempite."""
    import httpx
    cid, secret = (cfg.igdb or {}).get("client_id"), (cfg.igdb or {}).get("client_secret")
    if not (cid and secret):
        log("IGDB non configurato: si usa solo HowLongToBeat")
        return set()
    slugs = igdb_slugs(cfg.cache)
    want = [(t, pid, slugs[k]) for t, pid, k in todo if k in slugs]
    if not want:
        return set()
    tok = cache.kv_get("igdb_token")
    with httpx.Client(timeout=30) as c:
        if not tok or tok.get("exp", 0) < time.time() + 300:
            r = c.post("https://id.twitch.tv/oauth2/token",
                       params={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
            r.raise_for_status()
            tok = {"token": r.json()["access_token"], "exp": time.time() + r.json().get("expires_in", 0)}
            cache.kv_set("igdb_token", tok)
        hdr = {"Client-ID": cid, "Authorization": f"Bearer {tok['token']}"}

        def q(endpoint, body):
            for _ in range(3):
                r = c.post(f"https://api.igdb.com/v4/{endpoint}", headers=hdr, content=body)
                if r.status_code == 429:
                    time.sleep(1)
                    continue
                r.raise_for_status()
                time.sleep(0.3)  # IGDB: al massimo 4 richieste al secondo
                return r.json()
            return []
        ids: dict = {}
        uniq = list(dict.fromkeys(x for _, _, x in want))
        for i in range(0, len(uniq), 500):
            part = ",".join('"' + x.replace('"', "") + '"' for x in uniq[i:i + 500])
            for g in q("games", f"fields id,slug; where slug = ({part}); limit 500;"):
                ids[g["slug"]] = g["id"]
        ttb: dict = {}
        idl = list(set(ids.values()))
        for i in range(0, len(idl), 500):
            body = (f"fields game_id,hastily,normally,completely,count; "
                    f"where game_id = ({','.join(map(str, idl[i:i + 500]))}); limit 500;")
            for t in q("game_time_to_beats", body):
                ttb[t["game_id"]] = t
    filled = set()

    def hours(v):
        return round(v / 3600, 1) if v else None
    for title, pid, slug in want:
        t = ttb.get(ids.get(slug))
        if not t or not (t.get("hastily") or t.get("normally") or t.get("completely")) or (t.get("count") or 0) < 3:
            continue  # meno di 3 voti su IGDB: meglio il dato di HowLongToBeat
        k = cache_key(title, pid)
        cache.kv_set(k, {"main": hours(t.get("hastily") or t.get("normally")), "extra": hours(t.get("normally")),
                         "complete": hours(t.get("completely")), "source": "IGDB", "votes": t.get("count"),
                         "url": f"https://www.igdb.com/games/{slug}", "t": time.time()})
        filled.add(k)
    nreq = (len(uniq) + 499) // 500 + (len(idl) + 499) // 500
    log(f"IGDB: tempi di completamento per {len(filled):,} giochi su {len(want):,} collegati a IGDB ({nreq} richieste)")
    return filled


class RateLimited(RuntimeError):
    def __init__(self, wait: float):
        super().__init__(f"HowLongToBeat: troppe richieste (429), attesa {wait:.0f} s")
        self.wait = wait


class Client:
    """Client HowLongToBeat veloce: indirizzo di ricerca e token si ottengono UNA volta (la libreria
    howlongtobeatpy li riscarica a ogni ricerca: home + script del sito + token = piu' richieste per
    gioco); poi una sola richiesta per gioco sulla stessa connessione, utilizzabile da piu' thread."""

    BASE = "https://howlongtobeat.com/"
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/130.0 Safari/537.36")

    def __init__(self):
        import threading

        import httpx
        self.c = httpx.Client(headers={"User-Agent": self.UA, "Referer": self.BASE, "Origin": self.BASE.rstrip("/")},
                              timeout=30, follow_redirects=True)
        self.lock = threading.Lock()
        self.search_url = None
        self.auth = None
        self.gen = 0  # cambia a ogni nuova autenticazione

    def _init(self, gen: int):
        import re as _re

        from howlongtobeatpy.HTMLRequests import SearchAuthToken, SearchInformations
        with self.lock:
            if self.gen != gen and self.auth is not None:
                return  # un altro thread ha gia' rinnovato il token
            if not self.search_url:
                home = self.c.get(self.BASE)
                home.raise_for_status()
                for src in _re.findall(r'<script[^>]+src="([^"]*/_next/static/chunks/[^"]+)"', home.text):
                    js = self.c.get(src if src.startswith("http") else self.BASE + src.lstrip("/"))
                    if js.status_code == 200:
                        url = SearchInformations(js.text).search_url
                        if url:
                            self.search_url = url.lstrip("/")
                            break
                self.search_url = self.search_url or "api/s"
            tok = SearchAuthToken()
            r = self.c.get(self.BASE + self.search_url + tok.search_url_endpoint, params={"t": int(time.time() * 1000)})
            r.raise_for_status()
            self.auth = tok.extract_auth_token_from_json(r.json())
            self.gen += 1

    def search(self, title: str) -> list:
        from howlongtobeatpy.HTMLRequests import HTMLRequests, SearchModifiers
        from howlongtobeatpy.JSONResultParser import JSONResultParser
        for attempt in range(2):
            gen = self.gen
            if self.auth is None or attempt:
                self._init(gen)
            headers = HTMLRequests.get_search_request_headers(self.auth, self.UA)
            payload = HTMLRequests.get_search_request_data(title, SearchModifiers.NONE, 1, self.auth)
            r = self.c.post(self.BASE + self.search_url, headers=headers, content=payload)
            if r.status_code == 200:
                parser = JSONResultParser(title, HTMLRequests.GAME_URL, 0.4, None, True, False)
                parser.parse_json_result(r.text)
                return parser.results
            if r.status_code == 429:
                try:
                    wait = float(r.headers.get("Retry-After") or 0)
                except ValueError:
                    wait = 0
                raise RateLimited(min(max(wait, 15), 300))
            if r.status_code not in (401, 403, 404):
                raise RuntimeError(f"HowLongToBeat HTTP {r.status_code}")
            if attempt == 0:  # token scaduto o indirizzo cambiato: si ricomincia da capo
                with self.lock:
                    self.auth = None
                    if r.status_code == 404:
                        self.search_url = None
        raise RuntimeError(f"HowLongToBeat HTTP {r.status_code}")

    def close(self):
        self.c.close()


def run(cfg, cache, log=print, progress=None, only_missing=True, cancelled=lambda: False, search=None,
        workers: int = 4, use_igdb: bool = True) -> dict:
    """Piu' ricerche in parallelo (workers) entro il ritmo della policy "hltb" (richieste al secondo)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from .policy import Ledger, policy_for
    todo3 = []
    for title, pid, key in targets(cfg.cache, with_keys=True):
        hit = cache.kv_get(cache_key(title, pid))
        if hit is not None and only_missing:
            age = time.time() - (hit.get("t") or 0)
            if age < (FOUND_TTL if hit.get("main") or hit.get("extra") or hit.get("complete") else MISS_TTL):
                continue
        todo3.append((title, pid, key))
    # 1) IGDB in blocco (secondi); 2) HowLongToBeat solo per i rimanenti
    filled_igdb = set()
    if use_igdb and search is None:
        try:
            filled_igdb = igdb_pass(cfg, cache, todo3, log)
        except Exception as e:
            log(f"IGDB: {type(e).__name__}: {e} - si continua con HowLongToBeat")
    todo = [(t, p) for t, p, _ in todo3 if cache_key(t, p) not in filled_igdb]
    log(f"Giochi da cercare su HowLongToBeat: {len(todo):,}")
    # stessi nomi (normalizzati) in attesa: una ricerca serve a tutti
    pending: dict = {}
    for t, p in todo:
        pending.setdefault(normalize(t), []).append((t, p))
    filled_by = set()
    pol = policy_for(cfg, "hltb")
    ledger = Ledger(Path(cfg.cache) / "cache.db")
    day_left, _ = ledger.remaining(pol)
    if day_left is not None and day_left < len(todo):
        log(f"Oggi se ne cercano {max(day_left, 0):,} (limite giornaliero); il resto domani")
        todo = todo[:max(day_left, 0)]
    task = progress.add_task("HowLongToBeat", total=len(todo)) if progress and todo else None
    client = None
    if search is None:
        client = Client()
        search = client.search
    base_gap = 1.0 / pol.rate if pol.rate else 0.25
    pace = threading.Lock()
    nxt = [time.time()]
    gap = [base_gap]  # adattivo: raddoppia a ogni 429, poi torna piano verso base_gap
    ok_streak = [0]
    state = {"found": 0, "done": 0, "errors": 0, "stop": False}
    results: list[tuple[str, dict]] = []
    rlock = threading.Lock()

    def one(item):
        title, pid = item
        if state["stop"] or cancelled():
            return
        with rlock:
            if (title, pid) in filled_by:  # gia' riempito dalla ricerca di un altro gioco
                if task is not None:
                    progress.advance(task)
                return
        res_holder: list = []

        def _search(t):
            res_holder[:] = search(t) or []
            return res_holder
        for attempt in range(4):
            with pace:  # ritmo complessivo tra tutti i thread
                wait = nxt[0] - time.time()
                nxt[0] = max(nxt[0], time.time()) + gap[0]
            if wait > 0:
                time.sleep(wait)
            if state["stop"]:
                return
            try:
                r = lookup(title, pid, _search)
                with pace:
                    ok_streak[0] += 1
                    if ok_streak[0] >= 50 and gap[0] > base_gap:
                        gap[0] = max(base_gap, gap[0] * 0.8)
                        ok_streak[0] = 0
                break
            except RateLimited as e:
                with pace:
                    if gap[0] < 4:
                        gap[0] = min(4.0, gap[0] * 2)
                        log(f"HowLongToBeat chiede di rallentare: ora {1 / gap[0]:.1f} richieste al secondo")
                    ok_streak[0] = 0
                    nxt[0] = max(nxt[0], time.time() + e.wait)  # tutti i thread aspettano
                continue
            except Exception as e:
                r = e
                break
        else:
            return  # sempre 429: si riprova al prossimo giro
        try:
            if isinstance(r, Exception):
                raise r
            state["errors"] = 0
        except Exception as e:  # sito irraggiungibile o cambiato: dopo 8 errori di fila ci si ferma
            state["errors"] += 1
            log(f"{title}: {type(e).__name__}: {e}")
            if state["errors"] >= 8 and not state["stop"]:
                state["stop"] = True
                log("HowLongToBeat non risponde: interrotto, si riprova piu' tardi")
            return
        with rlock:
            results.append((cache_key(title, pid), {**r, "t": time.time()}))
            state["done"] += 1
            state["found"] += bool(r)
            # gli stessi risultati riempiono gli altri giochi in attesa con un nome identico a un risultato
            for x in list(res_holder):
                for ot, op in pending.get(normalize(x.game_name or ""), []):
                    if (ot, op) != (title, pid) and (ot, op) not in filled_by:
                        rr = lookup(ot, op, lambda _t: res_holder)
                        if rr:
                            filled_by.add((ot, op))
                            results.append((cache_key(ot, op), {**rr, "t": time.time()}))
                            state["done"] += 1
                            state["found"] += 1
        ledger.spend(pol.source, 1)
        if task is not None:
            progress.advance(task)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = [ex.submit(one, it) for it in todo]
            while any(not f.done() for f in futs):
                time.sleep(1)
                with rlock:  # la cache si scrive dal thread principale, a blocchi
                    batch, results[:] = results[:], []
                for k, v in batch:
                    cache.kv_set(k, v)
                if cancelled() and not state["stop"]:
                    state["stop"] = True
                    log("Interrotto")
        for k, v in results:
            cache.kv_set(k, v)
    finally:
        ledger.close()
        if client:
            client.close()
    extra = f" ({len(filled_by):,} senza ricerca, dai risultati di altri giochi)" if filled_by else ""
    log(f"Durate trovate su HowLongToBeat: {state['found']:,} su {state['done']:,} giochi{extra}")
    return {"games": state["done"] + len(filled_igdb), "found": state["found"] + len(filled_igdb),
            "igdb": len(filled_igdb)}
