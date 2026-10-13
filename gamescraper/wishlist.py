"""Lista dei desideri con avvisi di sconto, solo store ufficiali.

Prezzi: IsThereAnyDeal (chiave gratuita, https://isthereanydeal.com/apps/my/ - confronta gli store
ufficiali come Steam, GOG, Epic, Humble...) oppure, senza chiave, il prezzo su Steam per i giochi
che hanno un codice Steam. Il controllo avviene al massimo due volte al giorno; un avviso nasce
quando lo sconto supera la soglia scelta, quando il prezzo scende sotto quello desiderato o quando
tocca il minimo storico.
"""

from __future__ import annotations

import json
import time

import httpx

from . import matching

ITAD = "https://api.isthereanydeal.com"
STEAM_STORE = "https://store.steampowered.com"
UA = {"User-Agent": "GameScraper/1.0"}


def settings(cfg) -> dict:
    w = dict(cfg.app.get("wishlist") or {})
    return {"itad_key": w.get("itad_key") or "", "country": (w.get("country") or "IT").upper(),
            "threshold": int(w.get("threshold") or 50)}


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
def _alert(item: dict, price, regular, cut, low, threshold) -> str:
    if price is None:
        return ""
    if price == 0 and regular:
        return "Gratis!"
    if item.get("target") and price <= item["target"]:
        return f"Sotto il tuo prezzo ({item['target']:.2f})"
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
    with httpx.Client(headers=UA, timeout=30) as c:
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
                d = deals[0]
                pr, rg = (d.get("price") or {}).get("amount"), (d.get("regular") or {}).get("amount")
                _save(user, w, pr, rg, d.get("cut") or 0, (d.get("shop") or {}).get("name", ""), d.get("url", ""), low,
                      (d.get("price") or {}).get("currency", "EUR"), s["threshold"], alerts)
        # senza chiave (o giochi senza codice IsThereAnyDeal): prezzo su Steam
        rest = [w for w in stale if w["steam_appid"] and not (s["itad_key"] and w["itad_id"])]
        for i in range(0, len(rest), 40):
            chunk = rest[i:i + 40]
            r = c.get(f"{STEAM_STORE}/api/appdetails", params={"appids": ",".join(w["steam_appid"] for w in chunk),
                                                              "cc": s["country"].lower(), "filters": "price_overview"})
            if r.status_code != 200:
                log(f"Steam: HTTP {r.status_code}")
                break
            data = r.json() or {}
            for w in chunk:
                po = ((data.get(w["steam_appid"]) or {}).get("data") or {})
                po = po.get("price_overview") if isinstance(po, dict) else None
                if not po:  # gratuito o non in vendita
                    user.wish_update(w["id"], checked=time.time())
                    continue
                _save(user, w, po["final"] / 100, po["initial"] / 100, po.get("discount_percent") or 0, "Steam",
                      f"{STEAM_STORE}/app/{w['steam_appid']}", None, po.get("currency", "EUR"), s["threshold"], alerts)
            time.sleep(1)
    return {"checked": len(stale), "alerts": alerts}


def _save(user, w, price, regular, cut, shop, url, low, currency, threshold, alerts):
    a = _alert(w, price, regular, cut, low, threshold)
    new = bool(a) and (a != (w["alert"] or "") or (w["price"] is not None and price is not None and price < w["price"]))
    user.wish_update(w["id"], price=price, regular=regular, cut=cut, shop=shop, url=url, low=low, currency=currency,
                     checked=time.time(), alert=a, **({"seen": 0} if new else {}))
    if new:
        alerts.append(w["title"])


def owned_match(title: str, titles: set[str]) -> bool:
    return matching.normalize(title) in titles
