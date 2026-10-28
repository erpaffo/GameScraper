"""Lista dei desideri con avvisi di sconto: store ufficiali e negozi di key (giochi PC).

Prezzi ufficiali: IsThereAnyDeal (chiave gratuita, https://isthereanydeal.com/apps/my/ - confronta Steam, GOG,
Epic, Humble...) oppure, senza chiave, il prezzo su Steam per i giochi che hanno un codice Steam.
Negozi di key: GG.deals (chiave gratuita, https://gg.deals/api/ - prezzo piu' basso tra i negozi di key e minimo
storico), solo per i giochi con codice Steam. Il controllo avviene al massimo due volte al giorno; un avviso nasce
quando lo sconto supera la soglia scelta, quando il prezzo (ufficiale o dei negozi di key) scende sotto quello
desiderato o quando tocca il minimo storico.
"""

from __future__ import annotations

import json
import time

import httpx

from . import matching

ITAD = "https://api.isthereanydeal.com"
GGDEALS = "https://api.gg.deals/v1/prices/by-steam-app-id/"
STEAM_STORE = "https://store.steampowered.com"
UA = {"User-Agent": "PozzoLauncher/1.0"}


def settings(cfg) -> dict:
    w = dict(cfg.app.get("wishlist") or {})
    return {"itad_key": w.get("itad_key") or "", "gg_key": w.get("gg_key") or "",
            "country": (w.get("country") or "IT").upper(), "threshold": int(w.get("threshold") or 50)}


# ---------------------------------------------------------------- ricerca
def search(cfg, q: str) -> list[dict]:
    s = settings(cfg)
    out: list[dict] = []
    with httpx.Client(headers=UA, timeout=20) as c:
        if s["itad_key"]:
            r = c.get(f"{ITAD}/games/search/v1", params={"key": s["itad_key"], "title": q, "results": 12})
            if r.status_code == 200:
                for g in r.json():
                    if g.get("type") not in (None, "game"):
                        continue
                    out.append({"id": "itad:" + g["id"], "itad_id": g["id"], "title": g.get("title", ""),
                                "cover": ((g.get("assets") or {}).get("boxart") or (g.get("assets") or {}).get("banner300"))})
        if not out:  # senza chiave (o nessun risultato): ricerca nel negozio di Steam
            r = c.get(f"{STEAM_STORE}/api/storesearch/", params={"term": q, "cc": s["country"], "l": "italian"})
            if r.status_code == 200:
                for it in r.json().get("items", [])[:12]:
                    out.append({"id": f"steam:{it['id']}", "steam_appid": str(it["id"]), "title": it.get("name", ""),
                                "cover": it.get("tiny_image")})
    return out


# ---------------------------------------------------------------- lista desideri di Steam
def steam_wishlist(steamid: str) -> list[dict]:
    """Lista desideri pubblica di Steam: [{steam_appid, title}]."""
    with httpx.Client(headers=UA, timeout=30) as c:
        r = c.get("https://api.steampowered.com/IWishlistService/GetWishlist/v1/", params={"steamid": steamid})
        if r.status_code != 200:
            raise RuntimeError(f"Steam ha risposto {r.status_code}: la lista desideri e' privata?")
        ids = [str(x["appid"]) for x in (r.json().get("response") or {}).get("items", [])]
        names: dict[str, str] = {}
        for i in range(0, len(ids), 50):  # nomi a blocchi di 50
            req = {"ids": [{"appid": int(a)} for a in ids[i:i + 50]], "context": {"language": "italian", "country_code": "IT"},
                   "data_request": {"include_basic_info": False}}
            r2 = c.get("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
                       params={"input_json": json.dumps(req)})
            if r2.status_code == 200:
                for it in (r2.json().get("response") or {}).get("store_items", []):
                    if it.get("name"):
                        names[str(it.get("appid") or it.get("id"))] = it["name"]
    return [{"steam_appid": a, "title": names.get(a, f"App {a}")} for a in ids]


# ---------------------------------------------------------------- prezzi
def _num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


GG_REGIONS = {"au", "be", "br", "ca", "ch", "de", "dk", "es", "eu", "fi", "fr", "gb", "ie", "it", "nl", "no", "pl", "ru", "se", "tr", "us"}


def _gg_region(region: str) -> str:
    r = (region or "eu").strip().lower()
    if r == "uk":
        return "gb"
    return r if r in GG_REGIONS else "eu"


def gg_prices(c: httpx.Client, key: str, appids: list[str], region: str, log=print) -> dict[str, dict]:
    """{appid: {"key": prezzo nei negozi di key, "key_low": minimo storico, "retail": prezzo ufficiale, "url": pagina}}."""
    out: dict[str, dict] = {}
    uniq = list(dict.fromkeys(str(a).strip() for a in appids if a and str(a).strip()))
    reg = _gg_region(region)
    for i in range(0, len(uniq), 100):  # limite GG.deals: fino a 100 ID per richiesta, 1000 richieste/ora
        if i > 0:
            time.sleep(0.5)
        r = c.get(GGDEALS, params={"ids": ",".join(uniq[i:i + 100]), "key": key, "region": reg})
        if r.status_code != 200:
            log(f"GG.deals: HTTP {r.status_code} {r.text[:120]}")
            break
        body = r.json() if r.content else {}
        if not isinstance(body, dict) or body.get("success") is False:
            msg = (body.get("data") or {}).get("message") if isinstance(body, dict) and isinstance(body.get("data"), dict) else ""
            log(f"GG.deals: risposta non valida {msg}".strip())
            break
        data = body.get("data")
        if not isinstance(data, dict):
            break
        for appid, g in data.items():
            if not isinstance(g, dict):  # es. null per un appid sconosciuto
                continue
            p = g.get("prices") if isinstance(g.get("prices"), dict) else {}
            if _num(p.get("currentKeyshops")) is None and _num(p.get("currentRetail")) is None:
                continue
            out[str(appid)] = {"key": _num(p.get("currentKeyshops")), "key_low": _num(p.get("historicalKeyshops")),
                               "retail": _num(p.get("currentRetail")), "retail_low": _num(p.get("historicalRetail")),
                               "url": g.get("url"), "currency": p.get("currency")}
        if r.headers.get("x-ratelimit-remaining") == "0":
            break
    return out


def steam_prices(c: httpx.Client, appids: list[str], country: str, log=print) -> dict[str, dict]:
    """{appid: {price, regular, cut, currency, url}} per i giochi in vendita su Steam (gratuiti o ritirati: assenti)."""
    out: dict[str, dict] = {}
    ids = list(dict.fromkeys(a for a in appids if a))
    for i in range(0, len(ids), 40):
        if i:
            time.sleep(1)
        r = c.get(f"{STEAM_STORE}/api/appdetails", params={"appids": ",".join(ids[i:i + 40]), "cc": country.lower(),
                                                          "filters": "price_overview"})
        if r.status_code != 200:
            log(f"Steam: HTTP {r.status_code}")
            break
        data = r.json() or {}
        for a in ids[i:i + 40]:
            po = (data.get(a) or {}).get("data")
            po = po.get("price_overview") if isinstance(po, dict) else None
            if po:
                out[a] = {"price": po["final"] / 100, "regular": po["initial"] / 100, "cut": po.get("discount_percent") or 0,
                          "currency": po.get("currency", "EUR"), "url": f"{STEAM_STORE}/app/{a}"}
    return out


def find_appid(c: httpx.Client, title: str, country: str) -> str:
    """Codice Steam di un gioco aggiunto da IsThereAnyDeal (senza codice): solo se il titolo coincide, altrimenti ''."""
    r = c.get(f"{STEAM_STORE}/api/storesearch/", params={"term": title, "cc": country, "l": "english"})
    if r.status_code != 200:
        return ""
    want = matching.normalize(title)
    return next((str(it["id"]) for it in r.json().get("items", []) if matching.normalize(it.get("name", "")) == want), "")


ITAD_STEAM = 61  # id di Steam fra i negozi di IsThereAnyDeal


def itad_history(c: httpx.Client, key: str, itad_id: str, country: str, days: int = 730) -> list[list]:
    """Prezzi di Steam nel tempo da IsThereAnyDeal: [[data, prezzo]] in ordine di data."""
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))
    r = c.get(f"{ITAD}/games/history/v2", params={"key": key, "id": itad_id, "country": country,
                                                 "shops": ITAD_STEAM, "since": since})
    if r.status_code != 200:
        return []
    out = []
    for e in r.json() or []:
        amount = ((e.get("deal") or {}).get("price") or {}).get("amount")
        sid = (e.get("shop") or {}).get("id")
        if amount is not None and e.get("timestamp") and sid in (None, ITAD_STEAM):
            out.append([e["timestamp"][:10], amount])
    return sorted(out)


def _alert(item: dict, price, regular, cut, low, threshold, key_price=None) -> str:
    target = item.get("target")
    if target and key_price is not None and key_price <= target and (price is None or key_price < price):
        return f"Sotto il tuo prezzo ({target:.2f}) nei negozi di key: {key_price:.2f}"
    if price is None:
        return ""
    if price == 0 and regular:
        return "Gratis!"
    if target and price <= target:
        return f"Sotto il tuo prezzo ({target:.2f})"
    if low is not None and price <= low and cut:
        return "Minimo storico"
    if cut and cut >= threshold:
        return f"Sconto del {cut}%"
    return ""


def check(cfg, user, log=print, force: bool = False) -> dict:
    """Aggiorna i prezzi; ritorna {"checked": n, "alerts": [titoli con avviso nuovo]}."""
    s = settings(cfg)
    items = user.wishlist()
    stale = [w for w in items if force or not w["checked"] or time.time() - w["checked"] > 12 * 3600]
    alerts: list[str] = []
    if not stale:
        return {"checked": 0, "alerts": []}
    saved: set[str] = set()
    detail: dict[str, dict] = {}  # per gioco: Steam, key piu' economica, offerte (colonna aks, nome storico)
    with httpx.Client(headers=UA, timeout=30) as c:
        for w in stale:  # giochi aggiunti da IsThereAnyDeal: senza codice Steam GG.deals non li trova
            if not w["steam_appid"]:
                try:
                    w["steam_appid"] = find_appid(c, w["title"], s["country"])
                except (httpx.HTTPError, ValueError) as e:
                    log(f"Steam: {type(e).__name__}: {e}")
                if w["steam_appid"]:
                    user.wish_update(w["id"], steam_appid=w["steam_appid"])
        steam = steam_prices(c, [w["steam_appid"] for w in stale], s["country"], log)
        keys: dict[str, dict] = {}
        if s["gg_key"]:
            try:
                keys = gg_prices(c, s["gg_key"], [w["steam_appid"] for w in stale if w["steam_appid"]], s["country"], log)
            except (httpx.HTTPError, ValueError) as e:  # i prezzi ufficiali si controllano comunque
                log(f"GG.deals: {type(e).__name__}: {e}")

        def save(w, *a):
            _save(user, w, *a, key=keys.get(w["steam_appid"]))
            saved.add(w["id"])

        if s["itad_key"]:
            # codici IsThereAnyDeal mancanti: dal codice Steam o dal titolo
            for w in stale:
                if w["itad_id"]:
                    continue
                p = {"key": s["itad_key"]}
                p.update({"appid": w["steam_appid"]} if w["steam_appid"] else {"title": w["title"]})
                r = c.get(f"{ITAD}/games/lookup/v1", params=p)
                if r.status_code == 200 and (r.json() or {}).get("found"):
                    w["itad_id"] = r.json()["game"]["id"]
                    user.wish_update(w["id"], itad_id=w["itad_id"])
            ids = [w["itad_id"] for w in stale if w["itad_id"]]
            prices: dict[str, dict] = {}
            for i in range(0, len(ids), 100):
                r = c.post(f"{ITAD}/games/prices/v3", params={"key": s["itad_key"], "country": s["country"]},
                           content=json.dumps(ids[i:i + 100]), headers={"Content-Type": "application/json"})
                if r.status_code != 200:
                    log(f"IsThereAnyDeal: HTTP {r.status_code}")
                    break
                for g in r.json():
                    prices[g["id"]] = g
            for w in stale:
                g = prices.get(w["itad_id"])
                if not g:
                    continue
                deals = sorted(g.get("deals") or [], key=lambda d: (d.get("price") or {}).get("amount", 1e9))
                low = (((g.get("historyLow") or {}).get("all")) or {}).get("amount")
                if not deals:
                    user.wish_update(w["id"], checked=time.time())
                    continue
                detail[w["id"]] = {"offers": [{"shop": (x.get("shop") or {}).get("name", ""), "price": (x.get("price") or {}).get("amount"),
                                               "regular": (x.get("regular") or {}).get("amount"), "cut": x.get("cut") or 0,
                                               "url": x.get("url", "")} for x in deals[:25]], "low": low}
                d = deals[0]
                pr, rg = (d.get("price") or {}).get("amount"), (d.get("regular") or {}).get("amount")
                save(w, pr, rg, d.get("cut") or 0, (d.get("shop") or {}).get("name", ""), d.get("url", ""), low,
                     (d.get("price") or {}).get("currency", "EUR"), s["threshold"], alerts)
        # senza chiave (o giochi senza codice IsThereAnyDeal): prezzo su Steam
        for w in stale:
            st = steam.get(w["steam_appid"])
            if w["steam_appid"] and not (s["itad_key"] and w["itad_id"]):
                if st:
                    save(w, st["price"], st["regular"], st["cut"], "Steam", st["url"], None, st["currency"], s["threshold"], alerts)
                else:  # gratuito o non in vendita
                    user.wish_update(w["id"], checked=time.time())
        # nessun prezzo ufficiale trovato ma prezzo nei negozi di key (es. gioco non piu' in vendita su Steam)
        for w in stale:
            k = keys.get(w["steam_appid"])
            if w["id"] not in saved and k:
                save(w, w["price"], w["regular"], w["cut"] or 0, w["shop"] or "", w["url"] or "", w["low"],
                     w["currency"] or k.get("currency") or "EUR", s["threshold"], alerts)
        for w in stale:  # dettaglio per il dialog: i dati scaricati ora, piu' lo storico di ITAD gia' in cache
            old = json.loads(w["aks"] or "{}")
            d = {**{k: old[k] for k in ("hist", "hist_at") if k in old}, **detail.get(w["id"], {}),
                 "steam": steam.get(w["steam_appid"]) or old.get("steam"), "key": keys.get(w["steam_appid"]) or old.get("key")}
            user.wish_update(w["id"], aks=json.dumps(d))
    return {"checked": len(stale), "alerts": alerts}


def _cheaper(new, old) -> bool:
    return new is not None and old is not None and new < old


def own_low(points, today: str) -> float | None:
    """Prezzo piu' basso (store o key) visto nei giorni precedenti a oggi."""
    vals = [v for d, p, k in points if d < today for v in (p, k) if v is not None]
    return min(vals) if vals else None


def _save(user, w, price, regular, cut, shop, url, low, currency, threshold, alerts, key=None):
    kp = (key or {}).get("key")
    a = _alert(w, price, regular, cut, low, threshold, kp)
    today = time.strftime("%Y-%m-%d")
    best = min((v for v in (price, kp) if v is not None), default=None)
    prev = own_low(user.wish_history().get(w["id"], []), today)
    if not a and best is not None and prev is not None and best < prev:
        a = f"Il prezzo più basso da quando lo segui: {best:.2f} (prima {prev:.2f})"
    user.wish_price_point(w["id"], price, kp, today)
    new = bool(a) and (a != (w["alert"] or "") or _cheaper(price, w["price"]) or _cheaper(kp, w.get("key_price")))
    extra = {"key_price": kp, "key_low": key.get("key_low"), "key_url": key.get("url")} if key else {}
    user.wish_update(w["id"], price=price, regular=regular, cut=cut, shop=shop, url=url, low=low, currency=currency,
                     checked=time.time(), alert=a, **({"seen": 0} if new else {}), **extra)
    if new:
        alerts.append(w["title"])


def owned_match(title: str, titles: set[str]) -> bool:
    return matching.normalize(title) in titles
