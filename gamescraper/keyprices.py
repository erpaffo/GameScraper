"""Prezzi delle key da allkeyshop.com per la lista desideri (giochi PC).

Per ogni gioco: la ricerca del sito (solo la prima volta, poi l'indirizzo resta salvato), la pagina del gioco
(tutte le offerte: negozio, prezzo, regione, edizione, coupon, key o account) e lo storico dei prezzi del sito
(minimo storico delle key, minimo di Steam).

allkeyshop blocca l'IP di chi fa troppe richieste (verificato: tre richieste in 40 secondi e la terza connessione
viene scartata). Qui si fa una richiesta per volta, almeno GAP secondi (+ JITTER a caso) fra due richieste, ogni gioco
al massimo una volta al giorno (una sola richiesta: la pagina del gioco), lo storico solo la prima volta e poi una
volta al mese (nel frattempo i minimi si aggiornano con i prezzi di ogni giorno) e, al primo rifiuto (403/429,
connessione scartata), ci si ferma per BLOCK_PAUSE. Il ritmo vale per tutto il processo.
"""

from __future__ import annotations

import html
import json
import random
import re
import threading
import time

import httpx

from . import matching

BASE = "https://www.allkeyshop.com"
SEARCH = BASE + "/blog/wp-admin/admin-ajax.php"
HISTORY = BASE + "/api/price_history_api.php"
STEAM_STORE = "https://store.steampowered.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
      "Referer": BASE + "/blog/"}
GAP, JITTER = 60, 30  # secondi fra due richieste ad allkeyshop: almeno GAP, piu' fino a JITTER a caso
TTL = 24 * 3600  # ogni gioco al massimo una volta al giorno
HISTORY_TTL = 30 * 86400  # lo storico (pesante) una volta al mese
MISS_TTL = 7 * 86400  # gioco non trovato: si riprova dopo una settimana
BLOCK_PAUSE = 6 * 3600  # dopo un rifiuto del sito
STEAM_MERCHANT = 1  # id di Steam fra i negozi di allkeyshop


class Blocked(Exception):
    """allkeyshop ha rifiutato la richiesta: fermarsi per BLOCK_PAUSE."""


class Throttle:
    """Una richiesta per volta, distanziate di almeno GAP secondi (+ un po' di caso), per tutto il processo."""

    def __init__(self, gap: float = GAP, jitter: float = JITTER, sleep=time.sleep):
        self.gap, self.jitter, self.sleep = gap, jitter, sleep
        self.lock = threading.Lock()
        self.last = 0.0

    def get(self, client: httpx.Client, url: str, **kw) -> httpx.Response:
        with self.lock:
            wait = self.last + self.gap + random.uniform(0, self.jitter) - time.monotonic()
            if wait > 0 and self.last:
                self.sleep(wait)
            try:
                r = client.get(url, **kw)
            except httpx.TransportError as e:  # connessione chiusa o rifiutata: e' il modo in cui il sito blocca
                raise Blocked(f"{type(e).__name__}: {e}") from e
            finally:
                self.last = time.monotonic()
        if r.status_code in (403, 429, 503):
            raise Blocked(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r


THROTTLE = Throttle()


# ---------------------------------------------------------------- ricerca
_ROW = re.compile(r'<li class="ls-results-row" data-platforms="([^"]*)">(.*?)</li>', re.S)
_HREF = re.compile(r'href="([^"]+)"')
_TITLE = re.compile(r'ls-results-row-game-title">([^<]*)<')


def parse_search(text: str) -> list[dict]:
    """Risposta della ricerca del sito ({"results": "<li ...>"}) -> [{url, title, platforms}]."""
    try:
        body = json.loads(text).get("results") or ""
    except (ValueError, AttributeError):
        body = text
    out = []
    for plats, row in _ROW.findall(body):
        h, t = _HREF.search(row), _TITLE.search(row)
        if h and t:
            out.append({"url": html.unescape(h.group(1)), "title": html.unescape(t.group(1)).strip(),
                        "platforms": plats.split()})
    return out


def pick(results: list[dict], title: str) -> dict | None:
    """La pagina delle key PC del gioco: niente console, niente pagine di soli account, titolo uguale."""
    pc = [r for r in results if "pc" in r["platforms"] and r["url"].rstrip("/").endswith("-cd-key-compare-prices")]
    want = matching.normalize(title)
    exact = next((r for r in pc if matching.normalize(r["title"]) == want), None)
    if exact:
        return exact
    best = max(pc, key=lambda r: matching.score(r["title"], title), default=None)
    return best if best and matching.score(best["title"], title) >= 90 else None


# ---------------------------------------------------------------- pagina del gioco
_PRODUCT_ID = re.compile(r'data-product-id="(\d+)"')
_OFFERS_VAR = re.compile(r"var gamePageTrans\s*=\s*")


def parse_product(text: str) -> dict:
    """Offerte della pagina del gioco: {"product_id", "offers": [...]} (le piu' economiche prima)."""
    pid = _PRODUCT_ID.search(text)
    m = _OFFERS_VAR.search(text)
    if not pid or not m:
        raise ValueError("pagina di allkeyshop senza offerte (struttura cambiata?)")
    data, _ = json.JSONDecoder().raw_decode(text, m.end())
    regions, editions = data.get("regions") or {}, data.get("editions") or {}
    offers = []
    for p in data.get("prices") or []:
        if not p.get("dispo") or p.get("price") is None:  # non disponibile
            continue
        reg = regions.get(str(p.get("region"))) or {}
        offers.append({"shop": p.get("merchantName") or "", "price": float(p["price"]),
                       "original": float(p.get("originalPrice") or p["price"]),
                       "coupon": p.get("voucher_code") or "", "official": bool(p.get("isOfficial")),
                       "account": bool(p.get("account")),
                       "region": reg.get("filter_name") or reg.get("region_name") or "",
                       "edition": (editions.get(str(p.get("edition"))) or {}).get("name") or "",
                       "platform": p.get("activationPlatform") or ""})
    offers.sort(key=lambda o: o["price"])
    return {"product_id": pid.group(1), "offers": offers}


# ---------------------------------------------------------------- storico
def parse_history(text: str) -> dict:
    """Minimo storico delle key e di Steam dallo storico di allkeyshop."""
    d = json.loads(text)
    out: dict = {"key_low": None, "key_low_date": None, "steam_low": None, "steam_low_date": None}
    k = d.get("lower_keyshops_price") or {}
    if k.get("price") not in (None, ""):
        out["key_low"], out["key_low_date"] = float(k["price"]), (k.get("last_update") or "")[:10] or None
    steam = [r for r in d.get("history") or [] if r and int(r.get("merchant_id") or 0) == STEAM_MERCHANT
             and r.get("min_discount_price") is not None]
    if steam:
        low = min(steam, key=lambda r: float(r["min_discount_price"]))
        out["steam_low"], out["steam_low_date"] = round(float(low["min_discount_price"]), 2), (low.get("start") or "")[:10]
    return out


def summary(url: str, product: dict, hist: dict) -> dict:
    """Dati salvati per il gioco: la key piu' economica, le altre offerte e i minimi storici."""
    keys = [o for o in product["offers"] if not o["official"] and not o["account"]]
    steam = next((o for o in product["offers"] if o["official"] and o["shop"].lower() == "steam"), None)
    return {"url": url, "product_id": product["product_id"], "offers": product["offers"],
            "key": keys[0] if keys else None, "aks_steam": steam["price"] if steam else None, **hist}


# ---------------------------------------------------------------- Steam (prezzo e sconto attuali)
def steam_price(client: httpx.Client, appid: str, country: str) -> dict | None:
    r = client.get(f"{STEAM_STORE}/api/appdetails", params={"appids": appid, "cc": country.lower(),
                                                            "filters": "price_overview"}, timeout=20)
    if r.status_code != 200:
        return None
    po = (((r.json() or {}).get(str(appid)) or {}).get("data") or {})
    po = po.get("price_overview") if isinstance(po, dict) else None
    if not po:
        return None
    return {"price": po["final"] / 100, "regular": po["initial"] / 100, "cut": po.get("discount_percent") or 0,
            "currency": po.get("currency", "EUR"), "url": f"{STEAM_STORE}/app/{appid}"}


# ---------------------------------------------------------------- aggiornamento
def due(w: dict, now: float | None = None) -> bool:
    if not w.get("aks_checked"):
        return True
    old = (now or time.time()) - w["aks_checked"]
    missing = (json.loads(w["aks"]) if w.get("aks") else {}).get("missing")
    return old > (MISS_TTL if missing else TTL)


def fetch(client: httpx.Client, w: dict, country: str = "IT", throttle: Throttle = THROTTLE) -> dict:
    """Prezzi di un gioco della lista. Blocked se il sito rifiuta (chi chiama si ferma)."""
    old = json.loads(w["aks"]) if w.get("aks") else {}
    url = old.get("url")
    if not url:  # prima volta: ricerca (poi l'indirizzo resta salvato)
        r = throttle.get(client, SEARCH, params={"action": "quicksearch", "search_name": w["title"], "currency": "eur",
                                                 "locale": "en", "platform": "all"},
                         headers={"X-Requested-With": "XMLHttpRequest"})
        hit = pick(parse_search(r.text), w["title"])
        if not hit:
            return {"missing": True}
        url = hit["url"]
    product = parse_product(throttle.get(client, url).text)
    lows = ("key_low", "key_low_date", "steam_low", "steam_low_date")
    if old.get("product_id") == product["product_id"] and time.time() - (old.get("history_at") or 0) < HISTORY_TTL:
        hist, hist_at = {k: old.get(k) for k in lows}, old["history_at"]
    else:
        hist = parse_history(throttle.get(client, HISTORY, params={"normalised_name": product["product_id"], "currency": "EUR",
                                                                   "database": "allkeyshop.com", "v2": "1"}).text)
        hist_at = time.time()
    out = {**summary(url, product, hist), "history_at": hist_at}
    if w.get("steam_appid"):
        try:
            out["steam"] = steam_price(client, w["steam_appid"], country)
        except (httpx.HTTPError, ValueError):
            pass
    # i minimi seguono i prezzi di ogni giorno: lo storico completo si scarica solo una volta al mese
    today = time.strftime("%Y-%m-%d")
    for low, now in (("key_low", (out["key"] or {}).get("price")),
                     ("steam_low", (out.get("steam") or {}).get("price", out["aks_steam"]))):
        if now is not None and (out[low] is None or now < out[low]):
            out[low], out[low + "_date"] = now, today
    return out


def refresh(user, country: str = "IT", log=print, block_until=lambda: 0.0, set_block=lambda t: None,
            throttle: Throttle = THROTTLE, client: httpx.Client | None = None) -> int:
    """Aggiorna i giochi da controllare uno alla volta (prima quelli mai controllati). Ritorna quanti."""
    n = 0
    own = client is None
    client = client or httpx.Client(headers=UA, timeout=30, follow_redirects=True)
    try:
        while True:
            if block_until() > time.time():
                return n
            todo = [w for w in user.wishlist() if due(w)]
            if not todo:
                return n
            w = min(todo, key=lambda x: (x.get("aks_checked") or 0, -(x.get("added") or 0)))  # nuovi prima
            try:
                d = fetch(client, w, country, throttle)
            except Blocked as e:
                set_block(time.time() + BLOCK_PAUSE)
                log(f"allkeyshop ha rifiutato la richiesta ({e}): riprovo fra {BLOCK_PAUSE // 3600} ore")
                return n
            except (httpx.HTTPError, ValueError) as e:  # pagina cambiata o errore del sito: il gioco si riprova domani
                log(f"allkeyshop, {w['title']}: {type(e).__name__}: {e}")
                d = {"error": str(e)[:200], "url": (json.loads(w["aks"]) if w.get("aks") else {}).get("url")}
            prices = {}
            if d.get("product_id"):  # la riga della lista mostra la key piu' economica e il suo minimo storico
                prices = {"key_price": (d.get("key") or {}).get("price"), "key_low": d.get("key_low"), "key_url": d["url"]}
            user.wish_update(w["id"], aks=json.dumps(d), aks_checked=time.time(), **prices)
            n += 1
    finally:
        if own:
            client.close()
