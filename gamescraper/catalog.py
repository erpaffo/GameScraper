"""Store online per console: pagine web indicate dall'utente con l'elenco dei file disponibili.

Da ogni pagina si leggono nome, dimensione, link di download e (se presente) link magnet.
Il tipo di pagina si sceglie con il campo "type" della sezione `catalogs` di settings.json:

  filelist (default)  elenchi di file e indici di directory Apache/nginx (formato storico, invariato)
  type_a              elenco paginato: <ul class="lcp_catlist"> + <ul class="lcp_paginator"> (?lcp_page0=N)
  type_b              elenco A-Z statico: contenitore con <h2 id="letter-X"> e un div per gioco

Ogni pagina viene scaricata UNA volta per aggiornamento (manuale), con un ritmo lento: il sito non
viene interrogato gioco per gioco. I download non avvengono nell'app: si apre il browser o il
client torrent predefinito.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import random
import re
import time
from pathlib import Path
from typing import Any, Callable, NotRequired, TypedDict
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

try:  # beautifulsoup4 serve solo a type_a e type_b; "filelist" continua a usare le espressioni regolari
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - installazione senza la dipendenza opzionale
    BeautifulSoup = None  # type: ignore[assignment]

_A = re.compile(r'<a\b[^>]*?href\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)</a>', re.I | re.S)
_SIZE = re.compile(r'(?<![\w.])(\d+(?:[.,]\d+)?)\s*(B|bytes|KB|KiB|K|MB|MiB|M|GB|GiB|G|TB|TiB|T)(?![\w])', re.I)
_MAGNET = re.compile(r'magnet:\?[^\'"\s<>]+', re.I)
_TAG = re.compile(r"<[^>]+>")
UNITS = {"b": 1, "bytes": 1, "k": 1024, "kb": 1024, "kib": 1024, "m": 1024**2, "mb": 1024**2, "mib": 1024**2,
         "g": 1024**3, "gb": 1024**3, "gib": 1024**3, "t": 1024**4, "tb": 1024**4, "tib": 1024**4}
DEFAULT_EXTS = {"zip", "7z", "rar", "chd", "iso", "cue", "bin", "rvz", "wbfs", "nsp", "xci", "pkg", "cso", "gdi", "cdi"}

# Pausa fra una pagina e la successiva di un elenco paginato (secondi). Nei test si azzera.
PAGE_DELAY = (0.2, 0.8)


class CatalogItem(TypedDict):
    """Una voce letta da una pagina di catalogo."""

    name: str
    size: int | None
    url: str
    magnet: str | None
    group: NotRequired[str | None]  # lettera del raggruppamento (solo type_b)


ParserFunc = Callable[..., Any]  # (html, base_url, exts, config) -> list[CatalogItem] oppure (items, pagine)


def parse_size(text: str) -> int | None:
    m = _SIZE.search(text or "")
    if not m:
        return None
    return int(float(m.group(1).replace(",", ".")) * UNITS[m.group(2).lower()])


def parse_listing(page: str, base_url: str, exts: set[str] | None = None) -> list[dict]:
    """Voci {name, size, url, magnet} trovate in una pagina HTML."""
    exts = {e.lower().lstrip(".") for e in (exts or set())} | DEFAULT_EXTS
    found = list(_A.finditer(page))
    out, seen = [], set()
    for i, m in enumerate(found):
        href = html.unescape(m.group(2)).strip()
        text = html.unescape(_TAG.sub("", m.group(3))).strip()
        name = text if "." in text[-6:] else unquote(href.rsplit("/", 1)[-1].split("?")[0])
        if not name or "." not in name or name.rsplit(".", 1)[-1].lower() not in exts:
            continue
        if href.startswith(("javascript:", "#", "magnet:")) or name in seen:
            continue
        # dimensione e magnet: nel tratto di pagina dopo il link, fino al link del file successivo
        end = len(page)
        for nxt in found[i + 1:]:
            t2 = _TAG.sub("", nxt.group(3)).strip()
            if "." in t2[-6:] and t2.rsplit(".", 1)[-1].lower() in exts:
                end = nxt.start()
                break
        tail = page[m.end():min(end, m.end() + 1500)]
        size = parse_size(_TAG.sub(" ", tail.split("magnet:")[0]))
        mag = _MAGNET.search(tail)
        seen.add(name)
        out.append({"name": name, "size": size, "url": urljoin(base_url, href),
                    "magnet": html.unescape(mag.group(0)) if mag else None})
    return out


# --------------------------------------------------------------------------- type_a: elenco paginato

def _require_soup() -> None:
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 richiesto dai parser type_a/type_b (pip install beautifulsoup4)")


def _clean_text(node) -> str:
    """Testo di un nodo su una sola riga (i titoli possono essere spezzati su piu' righe)."""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _classes(node) -> list[str]:
    return [c for c in (node.get("class") or []) if c]


def parse_type_a_index(page: str, base_url: str, exts: set[str] | None = None,
                       config: dict | None = None) -> tuple[list[CatalogItem], int]:
    """Legge una pagina di elenco paginato.

    Restituisce (voci, pagine_totali): `voci` sono i giochi della pagina, `pagine_totali` il massimo
    ?lcp_page0=N del paginatore (1 se non c'e' paginatore). Le voci di questo formato puntano alla
    scheda del gioco e non al file, quindi non si filtrano per estensione.
    """
    _require_soup()
    soup = BeautifulSoup(page, "html.parser")
    items: list[CatalogItem] = []
    seen: set[str] = set()

    for ul in soup.select("ul.lcp_catlist"):
        for a in ul.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "#", "magnet:")):
                continue
            name = _clean_text(a)
            if not name or name in seen:
                continue
            seen.add(name)
            items.append({"name": name, "size": None, "url": urljoin(base_url, href), "magnet": None})

    total_pages = 1
    for ul in soup.select("ul.lcp_paginator"):
        for a in ul.select("a[href]"):
            values = parse_qs(urlparse(a.get("href") or "").query).get("lcp_page0")
            if not values:
                continue
            try:
                total_pages = max(total_pages, int(values[0]))
            except (TypeError, ValueError):
                continue
    return items, total_pages


def parse_type_a_page(page: str, base_url: str, exts: set[str] | None = None,
                      config: dict | None = None) -> list[CatalogItem]:
    """Voci di una pagina successiva alla prima (non serve il conteggio delle pagine)."""
    return parse_type_a_index(page, base_url, exts, config)[0]


# --------------------------------------------------------------------------- type_c: archivio paginato (Ziperto)

_ARCHIVE_PAGE = re.compile(r"/page/(\d+)/?$")


def parse_type_c_index(page: str, base_url: str, exts: set[str] | None = None,
                       config: dict | None = None) -> tuple[list[CatalogItem], int]:
    """Legge un archivio WordPress paginato (es. ziperto.com/nintendo-switch-nsp/).

    Le voci sono i titoli degli articoli, la paginazione e' `/categoria/page/N/` (il massimo N dei
    link del paginatore). Se il paginatore non mostra numeri ma solo «Next», si restituisce 1 e
    `fetch()` seguira' i link «next» fino in fondo.
    """
    _require_soup()
    soup = BeautifulSoup(page, "html.parser")
    items: list[CatalogItem] = []
    seen: set[str] = set()
    for article in soup.select("article"):
        a = article.select_one("h2.post-title a[href], h2.entry-title a[href], h1.entry-title a[href], "
                               "a[rel='bookmark'][href]")
        if a is None:
            continue
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "magnet:")):
            continue
        name = _clean_text(a)
        if not name or name in seen:
            continue
        seen.add(name)
        items.append({"name": name, "size": None, "url": urljoin(base_url, href), "magnet": None})

    total = 1
    for a in soup.select("a[href]"):
        m = _ARCHIVE_PAGE.search(urlparse(a.get("href") or "").path)
        if m:
            try:
                total = max(total, int(m.group(1)))
            except ValueError:
                continue
    return items, total


def parse_type_c_page(page: str, base_url: str, exts: set[str] | None = None,
                      config: dict | None = None) -> list[CatalogItem]:
    """Voci di una pagina successiva alla prima di un archivio paginato."""
    return parse_type_c_index(page, base_url, exts, config)[0]


def next_page_url(page: str, base_url: str) -> str | None:
    """Indirizzo della pagina successiva, dai link «next» della pagina (None se e' l'ultima)."""
    _require_soup()
    soup = BeautifulSoup(page, "html.parser")
    a = (soup.select_one("a[rel='next'][href]") or soup.select_one("link[rel='next'][href]")
         or soup.select_one("a.nextpostslink[href]") or soup.select_one("a.next.page-numbers[href]"))
    href = (a.get("href") or "").strip() if a is not None else ""
    return urljoin(base_url, href) if href else None


# --------------------------------------------------------------------------- type_b: elenco A-Z
def parse_type_b(page: str, base_url: str, exts: set[str] | None = None,
                 config: dict | None = None) -> list[CatalogItem]:
    """Legge un elenco A-Z statico; ogni voce porta in `group` la lettera del separatore.

    Le classi CSS si possono cambiare con `config["css"]` (chiavi "az_list" e "game"). Se le
    classi indicate non compaiono nella pagina, il contenitore e gli item si riconoscono dal
    suffisso (`*-az-list`, `*-game`), cosi' lo stesso parser vale per siti con nomi diversi.
    """
    _require_soup()
    css = (config or {}).get("css") or {}
    az_list = css.get("az_list") or "PLACEHOLDER_CSS_B-az-list"
    game_css = css.get("game") or "PLACEHOLDER_CSS_B-game"
    soup = BeautifulSoup(page, "html.parser")
    items: list[CatalogItem] = []
    seen: set[str] = set()
    group: str | None = None

    containers = soup.select(f"div.{az_list}")
    if not containers:  # classi diverse: il contenitore si riconosce dal suffisso
        containers = [div for div in soup.find_all("div")
                      if any(cls.endswith("-az-list") for cls in _classes(div))]
    for container in containers:
        children = container.find_all(recursive=False)
        # item riconosciuti per classe esatta; se in questo contenitore non c'e' nessuno
        # con quella classe, si usa il suffisso "-game"
        exact_items = any(node.name == "div" and game_css in _classes(node) for node in children)
        for node in children:
            if node.name == "h2" and (node.get("id") or "").startswith("letter-"):
                group = _clean_text(node) or None
                continue
            if node.name != "div":
                continue
            classes = _classes(node)
            is_item = (game_css in classes) if exact_items else any(cls.endswith("-game") for cls in classes)
            if not is_item:
                continue
            a = node.select_one("a[href]")
            if a is None:
                continue
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "#", "magnet:")):
                continue
            name = _clean_text(a)
            if not name or name in seen:
                continue
            seen.add(name)
            items.append({"name": name, "size": None, "url": urljoin(base_url, href),
                          "magnet": None, "group": group})
    return items


# --------------------------------------------------------------------------- registro dei parser

def _parse_filelist(page: str, base_url: str, exts: set[str] | None = None,
                    config: dict | None = None) -> list[CatalogItem]:
    """Adapter con la firma comune: il comportamento resta quello di parse_listing()."""
    return parse_listing(page, base_url, exts)


PARSERS: dict[str, ParserFunc] = {
    "filelist": _parse_filelist,
    "type_a": parse_type_a_index,
    "type_b": parse_type_b,
    "type_c": parse_type_c_index,
}


def parser_type(cfg, pid: str, options: dict | None = None) -> str:
    """Tipo di parser per una console: argomento esplicito, poi settings.json, poi "filelist"."""
    return configured_type(cfg, pid, options) or "filelist"


def configured_type(cfg, pid: str, options: dict | None = None) -> str | None:
    """Tipo scelto esplicitamente (None = non indicato: si usa filelist con rilevamento del formato)."""
    if options and options.get("type"):
        return str(options["type"]).strip().lower()
    opts = (getattr(cfg, "catalog_options", None) or {}).get(pid, {}) if cfg is not None else {}
    chosen = str((opts or {}).get("type") or "").strip().lower()
    return chosen or None


def detect_kind(page: str) -> str | None:
    """Formato della pagina dedotto dalla struttura HTML (type_a / type_b / type_c), None se non riconosciuto.

    Serve quando in settings.json non e' indicato un "type": una pagina senza file (per esempio un
    elenco A-Z o una lista paginata) verrebbe letta come "filelist" e restituirebbe zero giochi.
    """
    if BeautifulSoup is None:
        return None
    soup = BeautifulSoup(page, "html.parser")
    if soup.select_one("ul.lcp_catlist"):
        return "type_a"
    for div in soup.find_all("div"):
        if any(cls.endswith("-az-list") for cls in _classes(div)):
            if div.find("h2", id=re.compile(r"^letter-")) or div.select_one("[class$='-game']"):
                return "type_b"
    if soup.select_one("article h2.post-title a[href], article h2.entry-title a[href], article h1.entry-title a[href]"):
        return "type_c"
    return None


# --------------------------------------------------------------------------- URL e chiavi di cache

def page_url(base_url: str, page: int, kind: str = "type_a") -> str:
    """URL della pagina N: `/categoria/page/N/` per gli archivi (type_c), `?lcp_page0=N` per type_a."""
    if page <= 1:
        return base_url
    parts = urlparse(base_url)
    if kind == "type_c":
        path = parts.path.rstrip("/")
        return urlunparse((parts.scheme, parts.netloc, f"{path}/page/{page}/", parts.params, parts.query, ""))
    qs = parse_qs(parts.query)
    qs["lcp_page0"] = [str(page)]
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, urlencode(qs, doseq=True),
                       parts.fragment))


def catalog_key(platform: str, url: str) -> str:
    return f"catalog:{platform}:{hashlib.sha1(url.encode()).hexdigest()[:12]}"


def entry_stem(item: CatalogItem, exts: set[str] | None = None) -> str:
    """Nome della voce senza estensione di file, SOLO se l'ultimo pezzo e' un'estensione nota.

    Per i cataloghi di file ("Gioco (USA).zip" -> "Gioco (USA)") si comporta come prima; per le
    voci type_a/type_b, che sono titoli, non taglia i numeri di versione ("Gioco v1.3.0" resta intero).
    """
    name = item.get("name") or ""
    base, dot, ext = name.rpartition(".")
    known = DEFAULT_EXTS | {e.lower().lstrip(".") for e in (exts or set())}
    return base if dot and ext and ext.lower() in known else name


def cache_key_url(kind: str, page_url_: str) -> str:
    """Chiave kv di una pagina gia' nota: "catalog:<tipo>:<url della pagina>"."""
    return f"catalog:{kind}:{page_url_}"


def cache_key(kind: str, url: str, page: int = 1) -> str:
    """Chiave kv della pagina `page` dell'indice `url`."""
    if kind == "type_b":
        return cache_key_url("type_b", url)
    return cache_key_url(kind, page_url(url, page, kind))


def cached_info(cache, pid: str, url: str) -> dict:
    """Voci, tipo rilevato, istante dell'aggiornamento e pagine totali gia' in cache per l'URL."""
    data = cache.kv_get(catalog_key(pid, url))
    if data and "entries" in data:  # filelist: una sola chiave, forma storica
        return {"entries": data["entries"], "time": data.get("time", 0), "total_pages": None, "type": "filelist"}
    entries: list[CatalogItem] = []
    times: list[float] = []
    total = None
    kind = None
    for (k,) in cache.db.execute("SELECT k FROM kv WHERE k LIKE 'catalog:type_%'").fetchall():
        d = cache.kv_get(k) or {}
        if d.get("pid") == pid and d.get("index_url") == url:
            entries.extend(d.get("entries") or [])
            times.append(d.get("time") or 0)
            total = d.get("total_pages") or total
            kind = k.split(":")[1]  # type_a / type_b / type_c
    return {"entries": entries, "time": max(times) if times else 0, "total_pages": total, "type": kind}


def cached_entries(cache, pid: str, url: str) -> list[CatalogItem]:
    """Voci gia' in cache per l'URL indicato, qualunque sia il tipo di parser."""
    return cached_info(cache, pid, url)["entries"]


def _prune(cache, pid: str, url: str, keep: dict[str, int]) -> None:
    """Toglie dalla cache le voci di altri tipi (o oltre l'ultima pagina valida) per lo stesso indice.

    Serve quando si cambia "type" in settings.json o quando un elenco paginato si accorcia:
    senza questa potatura le voci vecchie continuerebbero a comparire nella libreria.
    `keep` dice, per il tipo appena scritto, l'ultima pagina valida (1 se non paginato).
    """
    stale: list[str] = []
    legacy = catalog_key(pid, url)
    if "filelist" not in keep and cache.kv_get(legacy) is not None:
        stale.append(legacy)
    for (k,) in cache.db.execute("SELECT k FROM kv WHERE k LIKE 'catalog:type_%'").fetchall():
        d = cache.kv_get(k) or {}
        if d.get("pid") != pid or d.get("index_url") != url:
            continue
        kind = k.split(":")[1]  # type_a / type_b / type_c
        last = keep.get(kind)
        if last is None or (kind != "type_b" and (d.get("page") or 1) > last):
            stale.append(k)
    for k in stale:
        cache.db.execute("DELETE FROM kv WHERE k=?", (k,))
    if stale:
        cache.commit()


# --------------------------------------------------------------------------- scaricamento

def _sleep_between_pages() -> float:
    lo, hi = PAGE_DELAY
    return random.uniform(lo, hi) if hi > 0 else 0.0


async def _get_page(cfg, cache, client, pid: str, url: str, log=print) -> str:
    """Scarica una pagina HTML con la policy "catalog" (ritmo lento, contabilita' nel ledger)."""
    from .net import RateLimiter, request
    from .policy import Ledger, policy_for

    ledger = Ledger(Path(cache.folder) / "cache.db")
    try:
        lim = RateLimiter(0.5, 1).guard(policy_for(cfg, "catalog"), ledger, log=log)
        r = await request(client, "GET", url, limiter=lim, ok404=False,
                          headers={"Accept": "text/html,application/xhtml+xml"})
    finally:
        ledger.close()
    if r is None or r.status_code != 200:
        raise RuntimeError(f"pagina non raggiungibile (HTTP {r.status_code if r is not None else '?'})")
    return r.text


async def fetch(cfg, cache, client, platform, url: str, log=print, options: dict | None = None) -> list[CatalogItem]:
    """Scarica e legge le pagine di un catalogo; il risultato resta in cache (kv) fino al refresh."""
    chosen = configured_type(cfg, platform.id, options)
    kind = chosen or "filelist"
    if kind not in PARSERS:
        raise ValueError(f"tipo di catalogo sconosciuto: {kind!r} (disponibili: {', '.join(PARSERS)})")
    exts = set(platform.exts)
    log(f"{platform.name}: catalogo «{kind}» da {url}")

    first = await _get_page(cfg, cache, client, platform.id, url, log)

    if kind == "filelist":  # comportamento storico
        entries = parse_listing(first, url, exts)
        if not entries and chosen is None:
            # nessun tipo indicato e nessun file nella pagina: forse e' un elenco type_a/type_b
            detected = detect_kind(first)
            if detected:
                kind = detected
                log(f"{platform.name}: formato «{kind}» riconosciuto nella pagina")
        if kind == "filelist":
            cache.kv_set(catalog_key(platform.id, url), {"url": url, "pid": platform.id, "time": time.time(),
                                                        "entries": entries})
            _prune(cache, platform.id, url, {"filelist": 1})
            log(f"{platform.name}: {len(entries):,} giochi letti da {url}")
            return entries

    parser = PARSERS[kind]
    if kind == "type_b":  # indice unico, nessuna paginazione
        entries = parser(first, url, exts, options)
        cache.kv_set(cache_key("type_b", url), {"url": url, "index_url": url, "pid": platform.id,
                                                "time": time.time(), "entries": entries})
        _prune(cache, platform.id, url, {"type_b": 1})
        log(f"{platform.name}: {len(entries):,} giochi da {url}")
        return entries

    # type_a / type_c: la prima pagina dichiara quante sono, poi si leggono le successive
    entries, total = parser(first, url, exts, options)
    page_parser = parse_type_c_page if kind == "type_c" else parse_type_a_page
    cache.kv_set(cache_key(kind, url, 1), {"url": url, "index_url": url, "pid": platform.id, "page": 1,
                                           "total_pages": total, "time": time.time(), "entries": entries})
    log(f"{platform.name}: pagina 1/{total} — {len(entries):,} giochi")
    seen = {e["name"] for e in entries}
    page, cur_html, cur_url = 1, first, url
    while True:
        if kind == "type_c" and total <= 1:
            purl = next_page_url(cur_html, cur_url)  # paginatore senza numeri: si segue «next»
            if not purl:
                break
        else:
            if page + 1 > total:
                break
            purl = page_url(url, page + 1, kind)
        wait = _sleep_between_pages()
        if wait:
            await asyncio.sleep(wait)
        page += 1
        cur_html = await _get_page(cfg, cache, client, platform.id, purl, log)
        cur_url = purl
        total = max(total, page)
        fresh = [e for e in page_parser(cur_html, purl, exts, options) if e["name"] not in seen]
        seen.update(e["name"] for e in fresh)
        cache.kv_set(cache_key_url(kind, purl), {"url": purl, "index_url": url, "pid": platform.id,
                                                "page": page, "total_pages": total, "time": time.time(),
                                                "entries": fresh})
        entries.extend(fresh)
        log(f"{platform.name}: pagina {page}/{total} — {len(fresh):,} giochi")
    _prune(cache, platform.id, url, {kind: total})  # via le pagine di un elenco piu' lungo
    log(f"{platform.name}: {len(entries):,} giochi letti da {url}")
    return entries
