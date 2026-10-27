"""Plugin Minerva: riempie gli "Store online" di ogni console con i link a minerva-archive.org
elencati su https://r-roms.github.io (gli altri siti linkati da r-roms vengono ignorati).

POST /plugin-api/minerva/importa  -> avvia l'importazione (in background)
GET  /plugin-api/minerva/stato    -> stato e log
"""

import asyncio
import html
import re
import threading
from urllib.parse import unquote, urljoin

from gamescraper import config as C
from gamescraper import platforms as P

RROMS = "https://r-roms.github.io/"
_PAGE = re.compile(r'href="(\./[^"#]+)"')
_MINERVA = re.compile(r'https?://(?:www\.)?minerva-archive\.org/browse/[^"\'<>\s]+', re.I)


def subpages(index: str) -> list[str]:
    """Pagine delle console/categorie linkate dall'indice di r-roms."""
    out = []
    for h in _PAGE.findall(index):
        h = html.unescape(h)
        if "/_app/" not in h and "." not in h.rsplit("/", 1)[-1] and h not in out:
            out.append(h)
    return out


# nomi di set non riconosciuti da platforms.detect (Redump "Panasonic - 3DO...", cartelle Internet Archive)
_HINTS = (("3do", "3do"), ("jagcd", "atarijaguar"), ("pcecd", "pcenginecd"), ("ngcd", "neogeocd"),
          ("total_dos", "dos"), ("doom", "dos"), ("ibm - pc", "dos"))


def platform_of(url: str, page: str) -> str | None:
    """Console di un link Minerva: dal nome del set nel percorso (dal piu' profondo), poi dalla pagina r-roms."""
    parts = [unquote(s) for s in url.split("/browse/", 1)[1].split("/") if s]
    for name in reversed(parts):
        p = P.detect(name)
        if p:
            return p.id
    low = " / ".join(parts).lower()
    for key, pid in _HINTS:
        if key in low and pid in P.PLATFORMS:
            return pid
    p = P.detect(page.rstrip("/").rsplit("/", 1)[-1])
    return p.id if p else None


def minerva_links(page_html: str) -> list[str]:
    """Solo cartelle (gli store sono elenchi di file): i link a un singolo file sono scartati."""
    links = []
    for u in _MINERVA.findall(page_html):
        u = html.unescape(u)
        if u.endswith("/") and unquote(u) not in map(unquote, links):
            links.append(u)
    return links


def collect(get) -> dict[str, list[str]]:
    """{console: [link minerva]} da r-roms. `get(url) -> html`."""
    found: dict[str, list[str]] = {}
    for rel in subpages(get(RROMS)):
        try:
            page = get(urljoin(RROMS, rel))
        except Exception:
            continue
        for u in minerva_links(page):
            pid = platform_of(u, rel)
            if pid and unquote(u) not in map(unquote, found.setdefault(pid, [])):
                found[pid].append(u)
    return found


def register(gs):
    cfg = gs.cfg
    job = {"status": "idle", "log": [], "found": {}}

    def run():
        import httpx

        from gamescraper import catalog
        from gamescraper.cache import Cache
        from gamescraper.net import make_client
        say = lambda m: job["log"].append(str(m))  # noqa: E731
        try:
            say("Lettura di r-roms.github.io…")
            with httpx.Client(follow_redirects=True, timeout=30) as h:
                found = collect(lambda u: h.get(u).raise_for_status().text)
            job["found"] = {k: len(v) for k, v in found.items()}
            say(f"{sum(map(len, found.values()))} link Minerva per {len(found)} console")
            # aggiunge ai link gia' presenti (quelli inseriti a mano restano)
            cats = C.read_settings(cfg).get("catalogs", {})
            for pid, urls in found.items():
                cur = cats.get(pid, [])
                # forma estesa {"urls": [...], "type": ...}: si aggiornano solo gli URL, il resto resta
                old = list(cur.get("urls") or []) if isinstance(cur, dict) else list(cur)
                merged = old + [u for u in urls if unquote(u) not in map(unquote, old)]
                if isinstance(cur, dict):
                    cats[pid] = {**cur, "urls": merged}
                else:
                    cats[pid] = merged
            C.set_setting(cfg, "catalogs", cats)
            c = Cache(cfg.cache)
            try:
                async def go():
                    async with make_client() as client:
                        for pid, urls in found.items():
                            for u in urls:
                                try:
                                    await catalog.fetch(cfg, c, client, P.PLATFORMS[pid], u, log=say)
                                except Exception as e:
                                    say(f"{u}: {e}")
                asyncio.run(go())
            finally:
                c.close()
            job["status"] = "done"  # il frontend avvia la ricostruzione della libreria
        except Exception as e:
            say(f"Errore: {type(e).__name__}: {e}")
            job["status"] = "error"

    @gs.route("importa", methods=("POST",))
    def importa():
        if job["status"] != "running":
            job.update(status="running", log=[], found={})
            threading.Thread(target=run, daemon=True).start()
        return job

    @gs.route("stato")
    def stato():
        return job
