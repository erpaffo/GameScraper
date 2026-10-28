"""Giochi gratis disponibili ora (da tenere, con abbonamento, weekend gratuiti, DLC gratis).

Fonte principale: il riquadro "Daily game deals" di allkeyshop.com (https://www.allkeyshop.com/blog/daily-game-deals/),
che la pagina carica da widget.allkeyshop.com con typeList=free. Ogni gioco e' un
<div class="splide__slide" data-console=".." data-drm=".."> con il link allo store, la copertina
(img.game-cover, alt = titolo) e il tipo di offerta (span.free-game-type). Le diapositive
"splide__slide--clone" sono copie del carosello e vengono scartate.

Fonti aggiuntive/di riserva:
- LootScraper Atom feed (https://feed.phenx.de/lootscraper_amazon_game.xml), feed pubblico per Prime Gaming;
- Steam Search (giochi a pagamento scontati del 100%);
- GamerPower (https://www.gamerpower.com/api-read), API pubblica dei giochi in regalo con data di fine.
Il risultato resta in cache 3 ore (12 ore per allkeyshop e Prime).
"""

from __future__ import annotations

import html
import re
import time

import httpx

AKS_WIDGET = "https://widget.allkeyshop.com/lib/generate/widget"
AKS_PAGE = "https://www.allkeyshop.com/blog/daily-game-deals/"
PRIME_FEED = "https://feed.phenx.de/lootscraper_amazon_game.xml"
GAMERPOWER = "https://www.gamerpower.com/api/giveaways"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Referer": "https://www.allkeyshop.com/"}
TTL = 3 * 3600
AKS_TTL = 12 * 3600  # allkeyshop scarta le connessioni dopo poche richieste: si interroga di rado
AKS_RETRY = 3 * 3600  # dopo un errore di rete non si riprova prima di questo tempo

DRM_NAMES = {"steam": "Steam", "epic": "Epic Games Store", "amazon": "Prime Gaming", "gog": "GOG", "ubisoft": "Ubisoft",
             "ea": "EA app", "battlenet": "Battle.net", "none": ""}
CONSOLE_NAMES = {"pc": "PC", "playstation": "PlayStation", "xbox": "Xbox", "switch": "Switch"}
# suffissi di piattaforma nei titoli di allkeyshop ("F1 25 PS5", "Wartales Xbox Series")
_PLAT_SUFFIX = re.compile(r"\s+(xbox series( x\|s)?|xbox one|xbox|ps5|ps4|playstation \d|switch|pc)$", re.I)

_SLIDE = re.compile(r'<div class="(splide__slide[^"]*)"([^>]*)>(.*?)</a>\s*</div>', re.S)
_ATTR = lambda name: re.compile(name + r'="([^"]*)"')  # noqa: E731
_HREF, _COVER = re.compile(r'<a[^>]+href="([^"]+)"'), re.compile(r'<img[^>]*class="game-cover"[^>]*>')
_TYPE = re.compile(r'class="free-game-type"[^>]*>\s*([^<]+?)\s*<')


def clean_title(t: str) -> str:
    return _PLAT_SUFFIX.sub("", html.unescape(t or "")).strip()


def parse_aks(text: str) -> list[dict]:
    out, seen = [], set()
    for cls, attrs, body in _SLIDE.findall(text):
        if "splide__slide--clone" in cls:
            continue
        href = _HREF.search(body)
        img = _COVER.search(body)
        if not href or not img:
            continue
        alt = _ATTR("alt").search(img.group(0))
        src = _ATTR("src").search(img.group(0))
        typ = _TYPE.search(body)
        con = _ATTR("data-console").search(attrs)
        drm = _ATTR("data-drm").search(attrs)
        raw = html.unescape(alt.group(1)) if alt else ""
        item = {"title": clean_title(raw), "full_title": raw, "url": html.unescape(href.group(1)),
                "cover": src.group(1) if src else None, "kind": html.unescape(typ.group(1)).strip() if typ else "Free",
                "console": con.group(1) if con else "pc", "drm": drm.group(1) if drm else "none", "source": "allkeyshop"}
        k = (item["title"].lower(), item["url"], item["console"])
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out


def fetch_aks(client: httpx.Client) -> list[dict]:
    params = {"widgetType": "deals", "locale": "it_IT", "currency": "eur", "typeList": "free", "console": "all",
              "apiKey": "aks"}
    r = client.get(AKS_WIDGET, params=params)
    r.raise_for_status()
    return parse_aks(r.text)  # la pagina del blog NON contiene il riquadro (si carica dal widget): una sola richiesta


_PR_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_PR_TITLE = re.compile(r"<title>\s*(?:Amazon Prime\s*\([^)]*\)\s*-\s*)?([^<]+?)\s*</title>", re.I)
_PR_LINK = re.compile(r'<link[^>]+href="([^"]+)"')
_PR_IMG = re.compile(r'<img[^>]+src="([^"]+)"')
_PR_END = re.compile(r"Offer valid to:</b>\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:\s+[0-9]{2}:[0-9]{2})?)", re.I)
_PR_WORTH = re.compile(r"Recommended price[^:]*:</b>\s*([^<]+?)\s*<", re.I)


def parse_prime(text: str) -> list[dict]:
    """Feed Atom pubblico di LootScraper per i giochi PC inclusi in Amazon Prime Gaming."""
    out, seen = [], set()
    for entry in _PR_ENTRY.findall(text):
        tm = _PR_TITLE.search(entry)
        lm = _PR_LINK.search(entry)
        if not tm or not lm:
            continue
        raw = html.unescape(tm.group(1)).strip()
        title = clean_title(raw)
        url = html.unescape(lm.group(1)).strip()
        im = _PR_IMG.search(entry)
        em = _PR_END.search(entry)
        wm = _PR_WORTH.search(entry)
        k = (title.lower(), url)
        if not title or k in seen:
            continue
        seen.add(k)
        item = {"title": title, "full_title": raw, "url": url,
                "cover": html.unescape(im.group(1)) if im else None,
                "kind": "Free with Prime", "console": "pc", "drm": "amazon", "source": "prime"}
        if em:
            item["end"] = em.group(1).strip()
        if wm:
            item["worth"] = html.unescape(wm.group(1)).strip()
        out.append(item)
    return out


def fetch_prime(client: httpx.Client) -> list[dict]:
    """Giochi PC inclusi in Prime Gaming tramite il feed Atom pubblico di LootScraper."""
    r = client.get(PRIME_FEED, timeout=httpx.Timeout(15, connect=8))
    r.raise_for_status()
    return parse_prime(r.text)


_GP_SUFFIX = re.compile(r"\s*\([^()]*\)\s*(?:giveaway|key)$|\s+(?:giveaway|key)$", re.I)
_GP_DRM = [("epic games", "epic"), ("steam", "steam"), ("gog", "gog"), ("ubisoft", "ubisoft"), ("battle.net", "battlenet"),
           ("prime", "amazon"), ("itch.io", "itch"), ("drm-free", "none")]


def fetch_gamerpower(client: httpx.Client) -> list[dict]:
    """GamerPower (API pubblica dei giochi in regalo): giochi da tenere su PC e console, niente mobile."""
    r = client.get(GAMERPOWER, params={"type": "game", "sort-by": "date"})
    r.raise_for_status()
    out = []
    for g in r.json() if isinstance(r.json(), list) else []:
        plats = (g.get("platforms") or "").lower()
        if not plats or all(x.strip() in ("android", "ios") for x in plats.split(",")):
            continue
        console = ("playstation" if "ps4" in plats or "ps5" in plats or "playstation" in plats else
                   "xbox" if "xbox" in plats else "switch" if "switch" in plats else "pc")
        drm = next((d for k, d in _GP_DRM if k in plats), "none")
        end = g.get("end_date") if g.get("end_date") not in (None, "N/A") else None
        out.append({"title": _GP_SUFFIX.sub("", html.unescape(g.get("title", ""))).strip(), "full_title": g.get("title", ""),
                    "url": g.get("open_giveaway_url") or g.get("gamerpower_url"), "cover": g.get("image") or g.get("thumbnail"),
                    "kind": "Free to keep", "console": console, "drm": drm, "source": "gamerpower", "end": end,
                    "worth": g.get("worth") if g.get("worth") not in (None, "N/A") else None})
    return out


STEAM_SEARCH = "https://store.steampowered.com/search/results/"
_ST_ROW = re.compile(r'<a href="([^"]+)"[^>]*data-ds-appid="(\d+)".*?class="title">([^<]*)<.*?data-discount="(\d+)"', re.S)


def fetch_steam(client: httpx.Client) -> list[dict]:
    """Giochi Steam normalmente a pagamento che ora costano 0 (sconto 100%): niente free to play.
    category1=998 = solo giochi (no DLC/colonne sonore); specials=1 = solo in offerta, quindi i free to play non compaiono."""
    r = client.get(STEAM_SEARCH, params={"query": "", "start": 0, "count": 100, "specials": 1, "maxprice": "free",
                                         "category1": 998, "cc": "it", "l": "italian", "infinite": 1, "json": 1})
    r.raise_for_status()
    out = []
    for url, appid, title, disc in _ST_ROW.findall(r.json().get("results_html", "")):
        if int(disc) == 100:
            out.append({"title": html.unescape(title).strip(), "full_title": html.unescape(title).strip(),
                        "url": f"https://store.steampowered.com/app/{appid}/", "kind": "Free to keep (100% off)",
                        "cover": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
                        "console": "pc", "drm": "steam", "source": "steam"})
    return out


def get(cache, force: bool = False, log=print) -> dict:
    hit = cache.kv_get("freegames")
    ttl = TTL if not (hit or {}).get("errors") else 20 * 60  # se una fonte non rispondeva si riprova prima
    if hit and not force and time.time() - hit.get("time", 0) < ttl:
        return hit
    items, errors = [], []
    with httpx.Client(headers=UA, timeout=25, follow_redirects=True) as c:
        aks = cache.kv_get("freegames_aks") or {}
        if aks.get("items") and time.time() < aks.get("next", 0):
            items += aks["items"]  # elenco recente (o sito irraggiungibile da poco): niente nuova richiesta
        else:
            try:
                with httpx.Client(headers=UA, timeout=httpx.Timeout(25, connect=8), follow_redirects=True) as ac:
                    got = fetch_aks(ac)
                cache.kv_set("freegames_aks", {"items": got, "time": time.time(), "next": time.time() + AKS_TTL})
                items += got
            except Exception as e:
                errors.append(f"allkeyshop: {type(e).__name__}: {e}"[:200])
                if aks.get("items"):  # si tiene l'ultimo elenco buono e si riprova piu' tardi
                    aks["next"] = time.time() + AKS_RETRY
                    cache.kv_set("freegames_aks", aks)
                    items += aks["items"]
        try:
            from .matching import normalize
            pr = cache.kv_get("freegames_prime") or {}
            if pr.get("items") and time.time() < pr.get("next", 0):
                prime_items = pr["items"]
            else:
                try:
                    with httpx.Client(headers={"User-Agent": UA["User-Agent"]}, timeout=httpx.Timeout(15, connect=8),
                                      follow_redirects=True) as pc:
                        prime_items = fetch_prime(pc)
                    cache.kv_set("freegames_prime", {"items": prime_items, "time": time.time(), "next": time.time() + AKS_TTL})
                except Exception as e:
                    errors.append(f"Prime: {type(e).__name__}: {e}"[:200])
                    if pr.get("items"):
                        pr["next"] = time.time() + AKS_RETRY
                        cache.kv_set("freegames_prime", pr)
                    prime_items = pr.get("items") or []
            have_prime = {normalize(i["title"]) for i in items}
            for g in prime_items:
                n = normalize(g["title"])
                old = next((i for i in items if normalize(i["title"]) == n), None)
                if old:
                    if g.get("end"):
                        old.setdefault("end", g["end"])
                    if g.get("worth"):
                        old.setdefault("worth", g["worth"])
                elif n and n not in have_prime:
                    items.append(g)
                    have_prime.add(n)
        except Exception as e:
            errors.append(f"Prime: {type(e).__name__}: {e}"[:200])
        try:
            items += fetch_steam(c)
        except Exception as e:
            errors.append(f"Steam: {type(e).__name__}: {e}"[:200])
        try:
            from .matching import normalize
            have = {normalize(i["title"]) for i in items}
            for g in fetch_gamerpower(c):
                n = normalize(g["title"])
                old = next((i for i in items if normalize(i["title"]) == n), None)
                if old:  # gia' presente da allkeyshop: si aggiunge solo la scadenza
                    old.setdefault("end", g.get("end"))
                elif n not in have:
                    items.append(g)
                    have.add(n)
        except Exception as e:
            errors.append(f"GamerPower: {type(e).__name__}: {e}"[:200])
    if not items and hit:  # nessuna fonte raggiungibile: si tiene l'ultimo elenco
        return {**hit, "errors": errors, "stale": True}
    data = {"time": time.time(), "items": items, "errors": errors}
    cache.kv_set("freegames", data)
    return data
