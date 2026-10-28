"""Copertina e descrizione dalla pagina del gioco (store online: nxbrew, ziperto e simili).

Gli indici degli store online danno solo titolo e link alla scheda; i dati veri (copertina,
descrizione, data, editore, generi, peso, Title ID) stanno nella pagina del gioco. Le regole per
pagina sono le stesse messe a punto nel progetto switch-library:

  - copertina: `og:image` (sui siti WordPress e' quasi sempre giusta) e, in ordine, i selettori del sito
    (`figure.wp-block-media-text__media img` per nxbrew, `img.aligncenter` per ziperto), scartando i
    segnaposto (le miniature 200x200, i logo, le icone);
  - descrizione: il testo dopo `Description:` quando c'e' (nxbrew), altrimenti i primi paragrafi
    dell'articolo, saltando le righe di metadati e i link di download.

I risultati stanno in cache (`netmeta:<url>`), quindi la pagina si scarica una volta sola; il ritmo
delle richieste usa la policy "netmeta" (2 richieste al secondo con pausa casuale).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import quote_plus, urljoin, urlparse

from .catalog import CatalogItem, cached_info, parse_size, _require_soup, _clean_text

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]

UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
CACHE_PREFIX = "netmeta:"
MAX_AGE = 60 * 86400  # la pagina di un gioco cambia raramente: un dato vecchio va benissimo
MAX_DESC = 2000

# Le regole per sito: come nel progetto switch-library, il primo selettore valido vince.
SITES: dict[str, dict] = {
    "nxbrew.net": {
        "covers": ["figure.wp-block-media-text__media img", "article .entry img", "img.wp-post-image",
                   "img.attachment-post-thumbnail"],
        "content": [".entry", "article"],
        "search": "https://nxbrew.net/?s={title}",
        "results": "h2.post-title a, .post-title a, .entry-title a",
    },
    "ziperto.com": {
        "covers": ["img.aligncenter", "p[style*='text-align: center'] img", "p strong img",
                   "img.wp-post-image", "img.attachment-post-thumbnail"],
        "content": [".entry-content", "article"],
        "search": "https://www.ziperto.com/?s={title}",
        "results": "h2.post-title a, .post-title a, .entry-title a",
    },
}
GENERIC = {"covers": ["img.aligncenter", "img.wp-post-image", "article img"], "content": [".entry-content", "article"]}

_PLACEHOLDER = re.compile(r"(200x200\.png|placeholder|no-?image|blank\.gif|spinner|/logo|avatar|favicon|"
                          r"gravatar|pixel\.(?:gif|png))", re.I)
_META_LINE = re.compile(r"^\s*(title|release date|category|publisher|developer|type|format|title id|"
                        r"title size|size|language[s]?|version|players|genres?)\s*:", re.I)
_NOISE = re.compile(r"\b(nsp|xci|eshop|switch|update|dlc|repack|multi\d*|usa|eur|jpn|jap|europe|asia)\b", re.I)

_DESC_AFTER = re.compile(r"\bdescription\s*:\s*(.{40,}?)(?:\s+(?:switch\s|download links?|download now|"
                         r"password|credits|how to|install|note)\b|$)", re.I | re.S)
_TITLE_ID = re.compile(r"title\s*id\s*:\s*([0-9a-f]{16})", re.I)
_DATE = re.compile(r"release\s*date\s*:\s*(\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})",
                   re.I)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun",
                                       "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_PUBLISHER = re.compile(r"publisher\s*:\s*(.{2,90}?)(?=\s+(?:type|format|title|category|language|size|version)\s*:|$)",
                        re.I | re.S)
_CATEGORY = re.compile(r"(?:categor(?:y|ies)|genres?)\s*:\s*(.{2,90}?)(?=\s+(?:publisher|type|format|title|language|size|"
                       r"mode|release)\s*:|$)", re.I | re.S)
_SIZE = re.compile(r"(?:title\s*)?size\s*:\s*([\d.,]+\s*[kmgt]i?b)", re.I)
_LANGS = re.compile(r"languages?\s*:\s*([a-zA-Z ,/&]{2,90})", re.I)
_VERSION = re.compile(r"\bversion\s*:\s*(v?[\d.]+)", re.I)
_SITE_SUFFIX = re.compile(r"\s+[-–|]\s+(?:[\w-]{2,30}\.(?:net|com|org|to|io)|nxbrew(?:\.net)?|ziperto(?:\.com)?)\s*$", re.I)


# --------------------------------------------------------------------------- utilità per i titoli

def clean_title(text: str) -> str:
    """Titolo senza rumore da sito di ROM ("- Zelda Switch NSP (eShop)" -> "Zelda")."""
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text or "")
    s = re.sub(r"\bv\d+(?:\.\d+)*\b", " ", s, flags=re.I)
    s = _NOISE.sub(" ", s)
    s = s.replace("™", " ").replace("®", " ")
    return re.sub(r"\s+", " ", s).strip(" -–—:")


def _iso_date(text: str) -> str:
    """«April 20, 2021» -> «2021-04-20»; le date gia' ISO restano come sono, quelle illeggibili spariscono."""
    t = (text or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return t
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t)
    month = _MONTHS.get(m.group(1)[:3].lower()) if m else None
    return f"{m.group(3)}-{month:02d}-{int(m.group(2)):02d}" if month else ""


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", clean_title(text).lower()) if len(t) > 1}


def similarity(expected: str, found: str) -> float:
    """Quanto il titolo trovato somiglia a quello cercato (0..1).

    Si divide per il maggiore dei due insiemi di parole: cosi' «Silk» contro «Silk Road» vale 0,5
    (e viene scartato), mentre un titolo ufficiale piu' lungo del cercato resta un buon riscontro.
    """
    want, got = _tokens(expected), _tokens(found)
    if not want or not got:
        return 0.0
    return len(want & got) / max(len(want), len(got))


def site_rules(url: str) -> dict:
    host = (urlparse(url).hostname or "").lower()
    for domain, rules in SITES.items():
        if host == domain or host.endswith("." + domain):
            return rules
    return GENERIC


def search_url(site: str, title: str) -> str:
    """Indirizzo di ricerca per titolo su un sito noto (per ora ziperto)."""
    return SITES[site]["search"].format(title=quote_plus(clean_title(title)))


def best_match(html: str, title: str, base_url: str, site: str = "ziperto.com") -> str | None:
    """Primo risultato di ricerca abbastanza simile al titolo (altrimenti None)."""
    _require_soup()
    rules = SITES.get(site) or GENERIC
    soup = BeautifulSoup(html, "html.parser")
    best, best_score = None, 0.0
    for a in soup.select(rules.get("results", "h2 a, .entry-title a")):
        text = _clean_text(a)
        href = (a.get("href") or "").strip()
        if not text or not href:
            continue
        score = similarity(title, text)
        if score > best_score:
            best, best_score = urljoin(base_url, href), score
    return best if best_score >= 0.6 else None


# --------------------------------------------------------------------------- lettura della pagina

def _absolutize(src: str, base_url: str) -> str:
    src = (src or "").strip()
    if not src or src.startswith(("data:", "javascript:")):
        return ""
    return urljoin(base_url, src)


def _usable_image(src: str) -> bool:
    return bool(src) and not _PLACEHOLDER.search(src)


def _cover(soup, rules: dict, base_url: str) -> str:
    """Copertina: og:image se valida, poi i selettori del sito (il primo utile)."""
    og = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "og:image"})
    candidates = [og.get("content") if og else None]
    for sel in rules["covers"]:
        for img in soup.select(sel):
            candidates.append(img.get("src") or img.get("data-src") or img.get("data-lazy-src"))
    for c in candidates:
        url = _absolutize(c or "", base_url)
        if _usable_image(url):
            return url
    return ""


def _content_text(soup, rules: dict) -> tuple[str, object]:
    """(testo compatto, nodo) del contenuto dell'articolo."""
    for sel in rules["content"]:
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 80:
            return _clean_text(node), node
    node = soup.body or soup
    return _clean_text(node), node


def _description(text: str, node, soup) -> str:
    """Descrizione: dopo "Description:" oppure i primi paragrafi utili dell'articolo."""
    m = _DESC_AFTER.search(text)
    if m:
        desc = re.sub(r"\s+", " ", m.group(1)).strip(" -–—:")
        if len(desc) > 40:
            return desc[:MAX_DESC]
    paras = []
    for p in (node.select("p") if hasattr(node, "select") else []):
        t = _clean_text(p)
        if len(t) < 60 or _META_LINE.match(t) or "download" in t.lower()[:40]:
            continue
        paras.append(t)
        if sum(len(x) for x in paras) > 600 or len(paras) == 3:
            break
    if paras:
        return re.sub(r"\s+", " ", " ".join(paras))[:MAX_DESC]
    meta = soup.find("meta", attrs={"name": "description"})
    fallback = (meta.get("content") if meta else "") or ""
    if len(fallback) > 80 and "title id" not in fallback.lower() and not _META_LINE.match(fallback):
        return re.sub(r"\s+", " ", fallback).strip()[:MAX_DESC]
    return ""


def _specs_text(soup) -> str:
    """Elenchi <dt>/<dd> di una scheda (es. superpsx: Genre, Release Date, Size) come righe «Chiave: valore»,
    così li leggono le stesse espressioni usate per i siti che scrivono «Genre: …» nel testo."""
    out = []
    for dl in soup.find_all("dl"):
        for key, val in zip(dl.find_all("dt"), dl.find_all("dd")):
            out.append(f"{_clean_text(key)}: {_clean_text(val)}")
    return "\n".join(out)


def _screenshots(node, cover: str, base_url: str, limit: int = 2) -> list[str]:
    out: list[str] = []
    if not hasattr(node, "select"):
        return out
    for img in node.select("img"):
        src = _absolutize(img.get("src") or img.get("data-src") or "", base_url)
        if not _usable_image(src) or src == cover or src in out:
            continue
        out.append(src)
        if len(out) >= limit:
            break
    return out


def parse_detail(page: str, url: str) -> dict:
    """Copertina, descrizione e metadati leggibili da una pagina di gioco."""
    _require_soup()
    rules = site_rules(url)
    soup = BeautifulSoup(page, "html.parser")
    text, node = _content_text(soup, rules)
    text = "\n".join(x for x in (text, _specs_text(soup)) if x)

    title = ""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = _SITE_SUFFIX.sub("", og_title["content"].strip())  # toglie solo "- NXBrew.net", non "- Parte del titolo"
    if not title:
        h1 = soup.select_one("h1.entry-title, h1.post-title, h1")
        title = _clean_text(h1) if h1 else ""
    if not title and soup.title:
        title = _SITE_SUFFIX.sub("", soup.title.get_text(strip=True))
    title = clean_title(title)

    cover = _cover(soup, rules, url)
    data: dict = {"url": url, "title": title, "cover": cover,
                  "screenshots": _screenshots(node, cover, url),
                  "description": _description(text, node, soup)}
    for key, regex in (("title_id", _TITLE_ID), ("release_date", _DATE), ("publisher", _PUBLISHER),
                       ("category", _CATEGORY), ("size_text", _SIZE), ("languages_text", _LANGS),
                       ("version", _VERSION)):
        m = regex.search(text)
        if m:
            data[key] = re.sub(r"\s+", " ", m.group(1)).strip(" ,;")
    if data.get("release_date"):
        data["release_date"] = _iso_date(data["release_date"])
    if data.get("size_text"):
        data["size"] = parse_size(data["size_text"])
        data.pop("size_text", None)
    if data.get("category"):
        data["genres"] = [g.strip() for g in re.split(r"[,/]", data["category"]) if g.strip()]
        data.pop("category", None)
    if data.get("languages_text"):
        # solo codici di lingua ("En, Ja, Pt"): il testo dopo l'elenco non ci interessa
        langs = [x.strip() for x in re.split(r"[,/]", data["languages_text"]) if re.fullmatch(r"[A-Za-z]{2,3}", x.strip())]
        if langs:
            data["languages"] = langs
        data.pop("languages_text", None)
    return {k: v for k, v in data.items() if v not in ("", None, [], {})}


# --------------------------------------------------------------------------- cache

def cache_key(url: str) -> str:
    return CACHE_PREFIX + url


def cached(cache, url: str, max_age: float = MAX_AGE) -> dict:
    """Dati gia' letti per quella pagina ({} se mancano o sono vecchi)."""
    hit = cache.kv_get(cache_key(url), max_age)
    return (hit or {}).get("data") or {}


def put(cache, url: str, data: dict, source: str, commit: bool = True):
    """Aggiunge/aggiorna i dati di una pagina. Con commit=False il chiamante decide quando salvare.

    Le scritture del giro GameTDB sono migliaia: un commit per voce bloccherebbe il database per
    decine di secondi e l'interfaccia (che legge la cache) resterebbe in attesa.
    """
    merged = dict(cached(cache, url))
    merged.update({k: v for k, v in data.items() if v not in ("", None, [], {})})
    merged["source"] = source
    now = time.time()
    payload = json.dumps({"time": now, "data": merged}, ensure_ascii=False)
    cache.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?,?)", (cache_key(url), payload, now))
    if commit:
        cache.commit()
    return merged


# --------------------------------------------------------------------------- scaricamento

async def fetch_detail(cfg, cache, client, url: str, log=print, limiter=None) -> dict:
    """Scarica una pagina di gioco e ne salva copertina/descrizione/metadati in cache.

    `limiter` permette di riusare lo stesso ritmo/ledger per molte pagine (meno connessioni SQLite).
    """
    from .net import RateLimiter, request
    from .policy import Ledger, policy_for

    if limiter is None:
        ledger = Ledger(cache.folder / "cache.db")
        try:
            limiter = RateLimiter(2.0, 1).guard(policy_for(cfg, "netmeta"), ledger, log=log)
            r = await request(client, "GET", url, limiter=limiter, ok404=True, headers=_headers())
        finally:
            ledger.close()
    else:
        r = await request(client, "GET", url, limiter=limiter, ok404=True, headers=_headers())
    if r is None or r.status_code != 200:
        return {}
    return put(cache, url, parse_detail(r.text, str(r.url)), "page")


def _headers() -> dict:
    return {"User-Agent": UA_BROWSER, "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9,it;q=0.8"}


async def fetch_search(cfg, cache, client, title: str, site: str = "ziperto.com", log=print,
                       limiter=None) -> dict:
    """Cerca il gioco sul sito indicato e legge la prima scheda abbastanza simile."""
    from .net import RateLimiter, request
    from .policy import Ledger, policy_for

    if limiter is None:
        ledger = Ledger(cache.folder / "cache.db")
        try:
            limiter = RateLimiter(2.0, 1).guard(policy_for(cfg, "netmeta"), ledger, log=log)
            r = await request(client, "GET", search_url(site, title), limiter=limiter, ok404=True, headers=_headers())
        finally:
            ledger.close()
    else:
        r = await request(client, "GET", search_url(site, title), limiter=limiter, ok404=True, headers=_headers())
    if r is None or r.status_code != 200:
        return {}
    hit = best_match(r.text, title, str(r.url), site)
    if not hit:
        return {}
    log(f"{site}: trovata una scheda per «{title}»")
    return await fetch_detail(cfg, cache, client, hit, log=log, limiter=limiter)


def coverage(cache) -> dict:
    """Copertine/descrizioni contate durante l'ultimo giro (visibili anche dopo un riavvio)."""
    return cache.kv_get("netmeta:coverage") or {}


async def fill(cfg, cache, tdb=None, log=print, limit: int = 0, cancelled=None,
               search_site: str | None = "ziperto.com", stats: dict | None = None, only: list[str] | None = None) -> dict:
    """Completa copertine e descrizioni delle voci degli store online.

    1. GameTDB (offline): per ogni voce senza dati, copertina/descrizione/giocatori/editore;
    2. pagina del gioco: fino a `limit` richieste (0 = tutte), per le voci ancora senza copertina o descrizione;
    3. ricerca su ziperto per le voci rimaste senza copertina.

    `stats` e' un dizionario aggiornato in corso d'opera: l'interfaccia lo legge per mostrare
    l'avanzamento senza aspettare la fine.
    """
    from . import platforms as P

    entries: dict[str, tuple[str, CatalogItem]] = {}
    for pid, urls in (getattr(cfg, "catalogs", {}) or {}).items():
        if pid not in P.PLATFORMS or (only and pid not in only):  # only: solo le console scelte
            continue
        for url in urls or []:
            info = cached_info(cache, pid, url)
            if info["type"] == "filelist":
                continue  # indici di file (Minerva e simili): i metadati arrivano dallo scraper normale
            for it in info["entries"]:
                entries.setdefault(it["url"], (pid, it))
    total = len(entries)
    stats = stats if stats is not None else {}
    stats.update({"games": total, "gametdb": 0, "pages": 0, "searches": 0, "complete": 0, "requests": 0,
                  "stopped": False})
    limit = limit or total
    if not total:
        log("Store online: nessuna scheda di gioco da completare")
        return stats

    # via i dati rimasti da voci che non sono piu' schede di gioco (nomi di file, vecchi esperimenti)
    # (solo a giro completo: con `only` le voci delle altre console non sono in `entries` ma restano valide)
    stale = [] if only else [k for (k,) in cache.db.execute("SELECT k FROM kv WHERE k LIKE ?", (CACHE_PREFIX + "%",))
                             if k[len(CACHE_PREFIX):] not in entries]
    for k in stale:
        cache.db.execute("DELETE FROM kv WHERE k=?", (k,))
    if stale:
        cache.commit()

    data_by_url = {url: cached(cache, url) for url in entries}

    def is_complete(url: str) -> bool:
        d = data_by_url.get(url) or {}
        return bool(d.get("cover") and d.get("description"))

    def recount():
        """Quante voci hanno copertina e quante hanno descrizione (per le due barre)."""
        covers = sum(1 for d in data_by_url.values() if d.get("cover"))
        descs = sum(1 for d in data_by_url.values() if d.get("description"))
        stats["covers"], stats["descriptions"] = covers, descs
        stats["complete"] = sum(1 for u in entries if is_complete(u))
        return covers, descs

    recount()  # punto di partenza (esecuzioni precedenti)

    # 1) GameTDB offline: nessuna richiesta, riempie subito la maggior parte dei giochi Switch
    if tdb is not None:
        for url, (pid, it) in entries.items():
            if cancelled and cancelled():
                stats["stopped"] = True
                break
            if pid != "switch" or is_complete(url):
                continue  # il database GameTDB contiene solo giochi Switch
            if (data_by_url.get(url) or {}).get("source") == "gametdb":
                continue  # gia' passato da GameTDB in un'esecuzione precedente
            rec = tdb.lookup(it["name"])
            if not rec:
                continue
            got = put(cache, url, {"title": rec.get("title"), "description": rec.get("synopsis"),
                                   "cover": (rec.get("covers") or [""])[0], "covers": rec.get("covers") or [],
                                   "players": rec.get("players"), "publisher": rec.get("publisher"),
                                   "developer": rec.get("developer"), "release_date": rec.get("release_date"),
                                   "genres": rec.get("genres") or []}, "gametdb", commit=False)
            data_by_url[url] = got
            stats["gametdb"] += 1
            if stats["gametdb"] % 250 == 0:  # salvataggio a blocchi: l'interfaccia resta reattiva
                cache.commit()
                recount()
                log(f"GameTDB: {stats['gametdb']:,} giochi")
        cache.commit()
        recount()
        log(f"GameTDB: dati per {stats['gametdb']:,} giochi su {total:,}")

    # 2) pagina del gioco, a richieste contate (piu' richieste in parallelo, stesso ritmo)
    from .net import RateLimiter, make_client
    from .policy import Ledger, policy_for
    todo = [u for u in entries if not is_complete(u) and (data_by_url.get(u) or {}).get("source") != "page"]
    log(f"Pagine da leggere: {min(len(todo), limit):,} (su {len(todo):,} incomplete)")
    if todo:
        ledger = Ledger(cache.folder / "cache.db")
        limiter = RateLimiter(2.0, 6).guard(policy_for(cfg, "netmeta"), ledger, log=log)
        try:
            async with make_client() as client:
                batch = 6  # quante pagine in volo: il ritmo resta quello della policy
                for i in range(0, min(len(todo), limit), batch):
                    if cancelled and cancelled():
                        stats["stopped"] = True
                        break
                    chunk = todo[i:i + batch]
                    results = await asyncio.gather(
                        *(fetch_detail(cfg, cache, client, url, log=log, limiter=limiter) for url in chunk),
                        return_exceptions=True)
                    for url, got in zip(chunk, results):
                        stats["requests"] += 1
                        stats["pages"] += 1
                        if isinstance(got, Exception):
                            log(f"{url}: {type(got).__name__}: {got}")
                        elif got:
                            data_by_url[url] = got
                    if stats["pages"] % 50 < batch:
                        log(f"Pagine lette: {stats['pages']:,}")
                    recount()  # le due barre (copertine / descrizioni) avanzano a ogni blocco

                # 3) ricerca su ziperto per i giochi rimasti senza copertina
                if search_site and not stats["stopped"]:
                    # ziperto ha solo giochi Switch: cercare lì le altre console darebbe copertine sbagliate
                    ziperto = "ziperto" in search_site
                    missing = [u for u in entries if not (data_by_url.get(u) or {}).get("cover")
                               and (not ziperto or entries[u][0] == "switch")]
                    if missing:
                        log(f"Ricerca su {search_site}: {len(missing):,} giochi senza copertina")
                    for i in range(0, len(missing), batch):
                        if cancelled and cancelled() or stats["requests"] >= limit * 2:
                            break
                        chunk = missing[i:i + batch]
                        results = await asyncio.gather(
                            *(fetch_search(cfg, cache, client, entries[u][1]["name"], search_site,
                                           log=log, limiter=limiter) for u in chunk),
                            return_exceptions=True)
                        for url, got in zip(chunk, results):
                            stats["requests"] += 1
                            stats["searches"] += 1
                            if isinstance(got, Exception):
                                log(f"{search_site}: {type(got).__name__}: {got}")
                            elif got:
                                data_by_url[url] = put(cache, url, got, "search")
        finally:
            ledger.close()

    stats["complete"] = sum(1 for u in entries if is_complete(u))
    covers, descs = recount()
    # numeri salvati: restano visibili anche dopo un riavvio dell'app, prima di rilanciare il lavoro
    cache.kv_set("netmeta:coverage", {"time": time.time(), "total": total, "covers": covers,
                                      "descriptions": descs, "complete": stats["complete"]})
    log(f"Copertine e descrizioni: {stats['complete']:,} giochi completi su {total:,} "
        f"({covers:,} copertine, {descs:,} descrizioni)")
    return stats
