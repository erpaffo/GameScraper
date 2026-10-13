"""Store online per console: pagine web indicate dall'utente con l'elenco dei file disponibili.

Da ogni pagina si leggono nome del file, dimensione, link di download e (se presente) link magnet.
Il lettore e' generico: riconosce
  - elenchi "a schede" come <div class="entry"><a href="/rom?id=1">Gioco.zip</a><span>360.25 KB</span> ... magnet ...</div>
  - indici di directory di Apache/nginx (<a href="Gioco.zip">...</a> ... 1.2 MiB)
Ogni pagina viene scaricata UNA volta per aggiornamento (manuale), con un ritmo lento: il sito non
viene interrogato gioco per gioco. I download non avvengono nell'app: si apre il browser o il
client torrent predefinito.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from pathlib import Path
from urllib.parse import unquote, urljoin

_A = re.compile(r'<a\b[^>]*?href\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)</a>', re.I | re.S)
_SIZE = re.compile(r'(?<![\w.])(\d+(?:[.,]\d+)?)\s*(B|bytes|KB|KiB|K|MB|MiB|M|GB|GiB|G|TB|TiB|T)(?![\w])', re.I)
_MAGNET = re.compile(r'magnet:\?[^\'"\s<>]+', re.I)
_TAG = re.compile(r"<[^>]+>")
UNITS = {"b": 1, "bytes": 1, "k": 1024, "kb": 1024, "kib": 1024, "m": 1024**2, "mb": 1024**2, "mib": 1024**2,
         "g": 1024**3, "gb": 1024**3, "gib": 1024**3, "t": 1024**4, "tb": 1024**4, "tib": 1024**4}
DEFAULT_EXTS = {"zip", "7z", "rar", "chd", "iso", "cue", "bin", "rvz", "wbfs", "nsp", "xci", "pkg", "cso", "gdi", "cdi"}


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


def catalog_key(platform: str, url: str) -> str:
    return f"catalog:{platform}:{hashlib.sha1(url.encode()).hexdigest()[:12]}"


async def fetch(cfg, cache, client, platform, url: str, log=print) -> list[dict]:
    """Scarica e legge una pagina; il risultato resta in cache (kv) fino al prossimo aggiornamento."""
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
    entries = parse_listing(r.text, str(r.url), set(platform.exts))
    cache.kv_set(catalog_key(platform.id, url), {"url": url, "time": time.time(), "entries": entries})
    log(f"{platform.name}: {len(entries):,} giochi letti da {url}")
    return entries
