"""Link di download elencati nella pagina di un gioco degli store online (nxbrew, ziperto e simili).

La pagina di un gioco elenca, per ogni file (gioco base, aggiornamento, DLC, versione in un'altra lingua,
parte di un archivio grande), un link per provider di hosting. Le regole per sito:

  nxbrew   <p><strong>Base Game NSP (996 MB)</strong></p> seguito da un <p> per provider
           («<strong>DataNodes</strong><br><a href=…>Download</a>»)
  ziperto  «Download (Base Game) Links: NSP (168MB)» con i provider nella stessa riga
           («<a>MegaUp</a> | <a>Send</a> | …»)

Alcuni provider passano da un accorciatore con pubblicita' (ouo.io): la pagina vera e' dietro un
controllo che solo un browser supera, quindi la risolve l'app desktop (finestra WebView nascosta,
`set_resolver`) e il risultato resta in cache.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

try:  # come catalog.py: beautifulsoup4 serve solo a questi due formati
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - installazione senza la dipendenza opzionale
    BeautifulSoup = None  # type: ignore[assignment]

from .catalog import _clean_text, _require_soup

CACHE_PREFIX = "storelinks:"
RESOLVED_PREFIX = "resolved:"
MAX_AGE = 7 * 86400  # le pagine cambiano quando esce un aggiornamento
RESOLVED_TTL = 30 * 86400
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36"),
           "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9,it;q=0.8"}
SHORTENERS = {"ouo.io", "ouo.press"}
# link della pagina che non sono file: strumenti consigliati e la pagina stessa
_SKIP_HOSTS = {"nxbrew.net", "ziperto.com", "tinfoil.io", "github.com", "7-zip.org", "winrar.com", "rarlab.com"}

KIND_ORDER = ("game", "update", "dlc", "extra")
KIND_LABELS = {"game": "Gioco", "update": "Aggiornamento", "dlc": "DLC", "extra": "Altro"}

_KIND_RULES = (
    (re.compile(r"\bdlc\b|\badd-?ons?\b|\bexpansion\b|\bseason pass\b", re.I), "dlc"),
    (re.compile(r"\bupdate\b|\bpatch\b", re.I), "update"),
    (re.compile(r"\bbase game\b|\bmain game\b|\bgame\b", re.I), "game"),
)
_PART = re.compile(r"\b(?:part|parte)\s*(\d+)\b", re.I)
_JUNK = re.compile(r"\s*[-–—_]{3,}.*$")  # separatori e testo pubblicitario in coda alla riga
_LINK_WORDS = ("download", "download now", "here", "link", "mirror", "scarica")


def _label(text: str) -> str:
    return _JUNK.sub("", text or "").strip(" -–—:")[:120]


def host(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def is_shortener(url: str) -> bool:
    return host(url) in SHORTENERS


def kind_of(text: str) -> str:
    """Tipo del file a cui si riferisce una riga («Update NSP», «Download (DLC) Links», «Base Game»)."""
    for regex, kind in _KIND_RULES:
        if regex.search(text or ""):
            return kind
    return ""


def part_of(text: str) -> int:
    m = _PART.search(text or "")
    return int(m.group(1)) if m else 0


def parse_page(page: str, url: str) -> dict:
    """Link {kind, label, provider, url, host, part} letti dalla pagina di un gioco."""
    _require_soup()
    soup = BeautifulSoup(page, "html.parser")
    root = None
    for sel in ("article .entry", ".entry-content", "article"):
        root = soup.select_one(sel)
        if root:
            break
    root = root or soup.body or soup
    links: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    section = {"kind": "game", "label": ""}
    for p in root.find_all("p"):
        text = _clean_text(p)
        anchors = []
        for a in p.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href.startswith(("http://", "https://")) or host(href) in _SKIP_HOSTS:
                continue
            anchors.append((a, href))
        kind = kind_of(text)
        if not anchors:
            if kind:  # riga di intestazione: «Base Game NSP (996 MB)», «Update NSP (v1.2)», «Part 1»
                section = {"kind": kind, "label": _label(text)}
            continue
        if kind:  # ziperto: intestazione e provider nella stessa riga
            section = {"kind": kind, "label": _label(text)}
        strong = _clean_text(p.find("strong")) if p.find("strong") else ""
        for a, href in anchors:
            name = _clean_text(a)
            provider = strong if (not name or name.lower() in _LINK_WORDS) and strong and strong.lower() not in _LINK_WORDS else name
            provider = (provider or host(href)).strip()[:60]
            label = section["label"]
            key = (section["kind"], provider.lower(), href)
            if key in seen:
                continue
            seen.add(key)
            links.append({"kind": section["kind"], "label": label, "provider": provider, "url": href,
                          "host": host(href), "part": part_of(label), "short": is_shortener(href)})
    return {"url": url, "site": host(url), "links": links}


# ---------------------------------------------------------------- cache

def cached(cache, url: str) -> dict | None:
    """Link gia' letti per quella pagina (None se mancano o sono vecchi)."""
    return cache.kv_get(CACHE_PREFIX + url, MAX_AGE)


def put(cache, url: str, data: dict) -> dict:
    cache.kv_set(CACHE_PREFIX + url, data)
    return data


# ---------------------------------------------------------------- risoluzione degli accorciatori

RESOLVER: dict = {"fn": None}


def set_resolver(fn) -> None:
    """fn(url) -> link finale | None: impostata dall'app desktop, che ha un browser vero da usare."""
    RESOLVER["fn"] = fn


def resolve(cache, url: str) -> str:
    """Link vero dietro un accorciatore pubblicitario (o lo stesso link, se non lo e')."""
    if not is_shortener(url):
        return url
    hit = cache.kv_get(RESOLVED_PREFIX + url, RESOLVED_TTL)
    if hit:
        return hit
    final = ""
    try:  # alcuni accorciatori non chiedono nulla: un semplice GET basta
        r = httpx.get(url, headers=HEADERS, timeout=httpx.Timeout(15, connect=8), follow_redirects=True)
        if r.status_code == 200 and not is_shortener(str(r.url)):
            final = str(r.url)
    except httpx.HTTPError:
        pass
    if not final and RESOLVER["fn"]:
        try:
            final = RESOLVER["fn"](url) or ""
        except Exception:  # finestra del browser non disponibile: si apre il link dell'accorciatore
            final = ""
    if final and not is_shortener(final):
        cache.kv_set(RESOLVED_PREFIX + url, final)
        return final
    return url


def fetch(url: str, timeout: float = 20.0) -> dict:
    """Scarica la pagina del gioco e ne legge i link."""
    r = httpx.get(url, headers=HEADERS, timeout=httpx.Timeout(timeout, connect=8), follow_redirects=True)
    r.raise_for_status()
    return parse_page(r.text, str(r.url))
