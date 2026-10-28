"""Elenchi di link: da una pagina che raccoglie altri indirizzi alle voci raggruppate per intestazione.

Usato dall'estensione «Link → Store»: si legge una pagina, si tiene l'ordine del documento, si
raggruppano i link sotto l'ultimo <h2>/<h3> incontrato e si ignorano menu, intestazione, piè di
pagina e script. Nessuna conoscenza dei siti: vale per qualunque pagina di link.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from .catalog import BeautifulSoup, _clean_text, _require_soup

SKIP_ANCESTORS = ("nav", "header", "footer", "aside", "script", "style", "noscript")
MAX_LINKS = 3000
NO_GROUP = "Senza intestazione"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def scan_page(page: str, base_url: str, external_only: bool = True,
              limit: int = MAX_LINKS) -> dict:
    """Link della pagina raggruppati per intestazione.

    `external_only` tiene solo i link verso altri siti (default: una pagina che raccoglie altri siti).
    Ritorna {"url", "title", "groups": [{"name", "level", "links": [{"text", "url", "host"}]}], "total"}.
    """
    _require_soup()
    soup = BeautifulSoup(page, "html.parser")
    root = soup.body or soup
    base_host = _host(base_url)
    groups: list[dict] = []
    seen: set[str] = set()
    current = {"name": NO_GROUP, "level": 0, "links": []}
    total = 0

    for node in root.find_all(["h2", "h3", "a"]):
        if node.name in ("h2", "h3"):
            name = _clean_text(node)
            if not name:
                continue
            if current["links"]:
                groups.append(current)
            current = {"name": name, "level": 2 if node.name == "h2" else 3, "links": []}
            continue
        if any(anc.name in SKIP_ANCESTORS for anc in node.parents):
            continue
        href = (node.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "magnet:", "tel:", "data:")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if not url.startswith(("http://", "https://")):
            continue
        if url.rstrip("/") == base_url.split("#")[0].rstrip("/"):
            continue  # rimando alla pagina stessa
        host = _host(url)
        if external_only and host == base_host:
            continue
        if url in seen:
            continue
        text = _clean_text(node)
        if len(text) < 2:
            continue
        seen.add(url)
        current["links"].append({"text": text[:200], "url": url, "host": host})
        total += 1
        if total >= limit:
            break

    if current["links"]:
        groups.append(current)
    return {"url": base_url, "title": _clean_text(soup.title) if soup.title is not None else "",
            "groups": [g for g in groups if g["links"]], "total": total}


def platform_id(platform: str) -> str:
    """«pc» e «windows» sono la stessa cosa: gli store PC hanno una sezione propria."""
    p = (platform or "").strip().lower()
    return "windows" if p in ("", "pc", "windows") else p


def add_store(cfg, url: str, platform: str = "windows", kind: str = "direct") -> dict:
    """Aggiunge un indirizzo come store: sezione PC (repack/direct) oppure elenco di una console.

    Scrive settings.json con le stesse funzioni usate dall'interfaccia, quindi il config in memoria
    resta allineato. Non avvia letture: quelle si fanno dalla pagina Store (o dal pulsante «Leggi»).
    """
    from . import config as C
    from . import platforms as P

    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("Indirizzo non valido (serve http:// o https://)")
    pid = platform_id(platform)
    settings = C.read_settings(cfg)

    if pid == "windows":
        kind = kind if kind in ("repack", "direct") else "direct"
        rows = [it for it in (settings.get("pc_stores") or []) if isinstance(it, dict)]
        if any(str(it.get("url") or "").strip() == url for it in rows):
            return {"added": False, "platform": pid, "kind": kind, "reason": "già presente"}
        rows.append({"url": url, "kind": kind})
        C.set_setting(cfg, "pc_stores", rows)
        return {"added": True, "platform": pid, "kind": kind}

    if pid not in P.PLATFORMS:
        raise ValueError(f"Console sconosciuta: {platform}")
    catalogs = dict(settings.get("catalogs") or {})
    current = catalogs.get(pid)
    if isinstance(current, dict):
        opts = {k: v for k, v in current.items() if k != "urls"}
        urls = [str(u) for u in (current.get("urls") or [])]
    else:
        opts = {}
        urls = [str(u) for u in (current or [])]
    if url in urls:
        return {"added": False, "platform": pid, "kind": None, "reason": "già presente"}
    urls.append(url)
    catalogs[pid] = {**opts, "urls": urls} if opts else urls
    C.set_setting(cfg, "catalogs", catalogs)
    return {"added": True, "platform": pid, "kind": None}
