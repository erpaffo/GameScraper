"""Immagini delle collezioni (franchise): icona e copertina.

Scaricate (se possibile) da Wikipedia in inglese: si prova la pagina "<nome> (video game series)",
"(video game franchise)", "(franchise)", "(series)" e poi "<nome>", saltando le pagine di
disambiguazione; dal riquadro informativo si prende il logo (icona) o l'immagine principale
(copertina se e' una grafica, icona se e' un logo). Quando Wikipedia non ha niente, l'app usa le
immagini dei giochi della collezione (vedi server: /coll-img). Risultati in cache: trovati 180 giorni,
non trovati 30 giorni. Ritmo: una richiesta alla volta, almeno 1,1 s di pausa, User-Agent con contatto (regole Wikimedia).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx

from .sources.wikipedia import UA

API = "https://en.wikipedia.org/w/api.php"
SUFFIXES = ["(video game series)", "(video game franchise)", "(franchise)", "(media franchise)", "(series)", ""]
FOUND_TTL, MISS_TTL = 180 * 86400, 30 * 86400
_PARAM = re.compile(r"^\s*\|\s*(logo|image|image_file|cover)\s*=\s*(.+?)\s*$", re.I | re.M)


_last = [0.0]
GAP = 1.1  # secondi tra due richieste qualsiasi (API e immagini): Wikimedia limita chi va piu' veloce


def _get(c: httpx.Client, url: str, **kw) -> httpx.Response:
    """GET a ritmo lento; su 429/503 aspetta quanto chiede il server (Retry-After) e riprova."""
    for attempt in range(4):
        wait = GAP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        r = c.get(url, follow_redirects=True, **kw)
        _last[0] = time.time()
        if r.status_code not in (429, 503):
            return r
        try:
            ra = float(r.headers.get("Retry-After") or 0)
        except ValueError:
            ra = 0
        time.sleep(min(max(ra, 20 * (attempt + 1)), 300))
    return r


def _api(c: httpx.Client, **params) -> dict:
    r = _get(c, API, params={"action": "query", "format": "json", "formatversion": 2, **params})
    if r.status_code != 200:
        raise RuntimeError(f"Wikipedia HTTP {r.status_code}")
    return r.json()


def _clean_file(v: str) -> str:
    v = re.sub(r"<!--.*?-->|<ref.*?</ref>|<ref[^>]*/>", "", v, flags=re.S).strip()
    v = re.sub(r"^\[\[(?:File|Image|File):", "", v, flags=re.I).split("|")[0].strip(" []")
    v = re.sub(r"^(?:File|Image):", "", v, flags=re.I)
    return v if re.search(r"\.(svg|png|jpe?g|gif|webp)$", v, re.I) else ""


def find(c: httpx.Client, name: str) -> dict:
    """{page, icon_url, cover_url} (url assenti se non trovati)."""
    titles = [f"{name} {s}".strip() for s in SUFFIXES]
    js = _api(c, titles="|".join(titles), redirects=1, prop="pageprops", ppprop="disambiguation")
    q = js.get("query", {})
    redirect = {r["from"]: r["to"] for r in q.get("redirects", [])}
    norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
    pages = {p["title"]: p for p in q.get("pages", []) if not p.get("missing") and "disambiguation" not in (p.get("pageprops") or {})}
    page = None
    for t in titles:
        t2 = redirect.get(norm.get(t, t), norm.get(t, t))
        if t2 in pages:
            page = t2
            break
    if not page:
        return {}
    js = _api(c, titles=page, prop="revisions", rvprop="content", rvslots="main", rvsection=0)
    pg = (js.get("query", {}).get("pages") or [{}])[0]
    text = ((pg.get("revisions") or [{}])[0].get("slots", {}).get("main", {}).get("content")) or ""
    files: dict[str, str] = {}
    for key, val in _PARAM.findall(text):
        f = _clean_file(val)
        if f and key.lower() not in files:
            files[key.lower()] = f
    if not files:  # immagine in [[File:...]] nell'introduzione
        m = re.search(r"\[\[(?:File|Image):([^|\]]+\.(?:svg|png|jpe?g))", text, re.I)
        if m:
            files["image"] = m.group(1).strip()
    out = {"page": f"https://en.wikipedia.org/wiki/{page.replace(' ', '_')}"}
    want = {}
    if files.get("logo"):
        want["icon"] = files["logo"]
    img = files.get("image") or files.get("image_file") or files.get("cover")
    if img:
        if re.search(r"logo|wordmark|emblem|icon|\.svg$", img, re.I):
            want.setdefault("icon", img)
        else:
            want["cover"] = img
    if not want:
        return out
    js = _api(c, titles="|".join(f"File:{f}" for f in want.values()), prop="imageinfo", iiprop="url", iiurlwidth=640)
    info = {}
    q = js.get("query", {})
    nmap = {n["to"]: n["from"] for n in q.get("normalized", [])}
    for p in q.get("pages", []):
        ii = (p.get("imageinfo") or [{}])[0]
        url = ii.get("thumburl") or ii.get("url")
        if url:
            info[nmap.get(p["title"], p["title"])] = url
    for kind, f in want.items():
        url = info.get(f"File:{f}") or next((u for t, u in info.items() if t.lower().endswith(f.lower().replace("_", " "))), None)
        if url:
            out[f"{kind}_url"] = url
    return out


def _download(c: httpx.Client, url: str, dest: Path) -> str | None:
    from .net import sniff
    r = _get(c, url)
    if r.status_code != 200:
        return None
    ext = sniff(r.content[:16]) or ("svg" if b"<svg" in r.content[:400] else None)
    if not ext:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    p = dest.with_suffix("." + ext)
    p.write_bytes(r.content)
    return p.name


def run(cfg, cache, collections: list[tuple[str, str]], log=print, progress=None, only_missing=True,
        cancelled=lambda: False) -> dict:
    """collections: [(slug, nome)]."""
    folder = Path(cfg.cache) / "collart"
    todo = []
    for slug, name in collections:
        hit = cache.kv_get("collart:" + slug)
        if hit is not None and only_missing:
            ttl = FOUND_TTL if (hit.get("icon") or hit.get("cover")) else MISS_TTL
            if time.time() - (hit.get("t") or 0) < ttl:
                continue
        todo.append((slug, name))
    log(f"Collezioni da cercare su Wikipedia: {len(todo):,}")
    task = progress.add_task("Immagini delle collezioni", total=len(todo)) if progress and todo else None
    found = errors = 0
    with httpx.Client(headers={"User-Agent": UA}, timeout=25) as c:
        for slug, name in todo:
            if cancelled():
                log("Interrotto")
                break
            try:
                info = find(c, name)
                res = {"page": info.get("page"), "t": time.time()}
                for kind in ("icon", "cover"):
                    if info.get(f"{kind}_url"):
                        res[kind] = _download(c, info[f"{kind}_url"], folder / f"{slug}_{kind}")
                errors = 0
            except Exception as e:
                errors += 1
                log(f"{name}: {type(e).__name__}: {e}")
                if errors >= 5:
                    log("Wikipedia non risponde: interrotto, si riprova piu' tardi")
                    break
                continue
            cache.kv_set("collart:" + slug, res)
            found += bool(res.get("icon") or res.get("cover"))
            if task is not None:
                progress.advance(task)
    log(f"Collezioni con immagini da Wikipedia: {found:,} su {len(todo):,} cercate")
    return {"collections": len(todo), "found": found}
