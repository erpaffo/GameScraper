"""Plugin Minerva: riempie gli "Store online" di ogni console con i link a minerva-archive.org
elencati su https://r-roms.github.io (gli altri siti linkati da r-roms vengono ignorati), più gli
store preimpostati (Switch: NXBrew e Ziperto; PS4 e PS5: superpsx).
Uno store già presente per la console non si aggiunge e non si rilegge.

POST /plugin-api/minerva/importa  -> avvia l'importazione (in background)
GET  /plugin-api/minerva/stato    -> stato e log
"""

import asyncio
import html
import re
import threading
from urllib.parse import unquote, urljoin, urlparse

from pozzo import config as C
from pozzo import platforms as P

RROMS = "https://r-roms.github.io/"
_PAGE = re.compile(r'href="(\./[^"#]+)"')
_MINERVA = re.compile(r'https?://(?:www\.)?minerva-archive\.org/browse/[^"\'<>\s]+', re.I)


def store_key(url: str) -> str:
    """Chiave per riconoscere lo stesso store scritto in modi diversi («…/ps5» e «…/ps5/», http/https, «www.»)."""
    p = urlparse(unquote(str(url)).strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    return host + p.path.rstrip("/") + ("?" + p.query if p.query else "")


def merge_catalogs(cats: dict, found: dict[str, list[str]], read, say=lambda m: None):
    """Aggiunge a `cats` (settings.json › «catalogs») solo gli store non ancora presenti per la console.

    Ritorna (catalogs, store nuovi per console, store da leggere): da leggere sono i nuovi più quelli
    già presenti ma mai letti (`read(console, url)` falso), così un secondo import non rifà il lavoro.
    """
    added: dict[str, list[str]] = {}
    todo: dict[str, list[str]] = {}
    for pid, urls in found.items():
        cur = cats.get(pid, [])
        # forma estesa {"urls": [...], "type": ...}: si aggiornano solo gli URL, il resto resta
        old = list(cur.get("urls") or []) if isinstance(cur, dict) else list(cur)
        old_keys = {store_key(u) for u in old}
        old_by_key = {store_key(u): u for u in old}  # com'è scritto in settings.json
        have = set(old_keys)
        new = []
        for u in urls:  # la stessa pagina scritta in due modi conta una volta sola
            k = store_key(u)
            if k not in have:
                have.add(k)
                new.append(u)
        # già presenti ma mai letti (nessun gioco in cache, es. console aggiunta dopo): si leggono lo stesso
        later, seen = [], set()
        for u in urls:
            k = store_key(u)
            if k in old_by_key and k not in seen and not read(pid, old_by_key[k]):
                seen.add(k)
                later.append(old_by_key[k])
        if new or later:
            todo[pid] = new + later
        if not new:
            continue
        added[pid] = new
        merged = old + new
        if isinstance(cur, dict):
            # piu' elenchi della stessa console possono avere formati diversi: il "type"
            # fissato per la console ne romperebbe alcuni, quindi si lascia il riconoscimento
            if len(merged) > 1 and cur.get("type"):
                say(f"{pid}: tolto il tipo fisso «{cur['type']}» "
                    f"(con piu' elenchi ogni pagina viene riconosciuta da sola)")
                cur = {k: v for k, v in cur.items() if k != "type"}
            cats[pid] = {**cur, "urls": merged}
        else:
            cats[pid] = merged
    return cats, added, todo


# Store preimpostati: per Switch le pagine che elencano TUTTI i giochi, per PS4/PS5 l'archivio di superpsx.
# I formati sono diversi (type_b, type_c, type_d): non si fissa un "type" per la console,
# ogni pagina viene riconosciuta da sola (catalog.detect_kind).
PRESETS: dict[str, list[str]] = {
    "switch": [
        "https://nxbrew.net/game-index/",             # elenco A-Z su una pagina (type_b)
        "https://www.ziperto.com/nintendo-switch-nsp/",  # archivio paginato (type_c)
        "https://www.ziperto.com/nintendo-switch-xci/",  # elenco XCI su una pagina (type_d)
    ],
    "ps4": [
        "https://www.superpsx.com/category/ps4/",
    ],
    "ps5": [
        "https://www.superpsx.com/category/ps5/",
    ],
}


def presets() -> dict[str, list[str]]:
    """Copia degli store preimpostati {console: [url]} (così il chiamante non modifica la costante)."""
    return {pid: list(urls) for pid, urls in PRESETS.items()}



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

        from pozzo import catalog
        from pozzo.cache import Cache
        from pozzo.net import make_client
        say = lambda m: job["log"].append(str(m))  # noqa: E731
        try:
            say("Lettura di r-roms.github.io…")
            with httpx.Client(follow_redirects=True, timeout=30) as h:
                found = collect(lambda u: h.get(u).raise_for_status().text)
            # store preimpostati (Switch, PS4, PS5), aggiunti a quelli trovati
            pre = presets()
            for pid, urls in pre.items():
                cur = found.setdefault(pid, [])
                have = {store_key(u) for u in cur}
                for u in urls:
                    if store_key(u) not in have:
                        cur.append(u)
                        have.add(store_key(u))
            job["found"] = {k: len(v) for k, v in found.items()}
            say(f"{sum(map(len, found.values()))} link Minerva per {len(found)} console")
            if pre.get("switch"):
                say(f"Switch: {len(pre['switch'])} store preimpostati (NXBrew, Ziperto NSP, Ziperto XCI)")
            say("Store PS4 e PS5: superpsx")
            cats = C.read_settings(cfg).get("catalogs", {})
            c = Cache(cfg.cache)
            try:
                # si aggiungono solo gli store che la console non ha già, e si leggono solo quelli
                cats, added, todo = merge_catalogs(
                    cats, found, lambda pid, u: bool(catalog.cached_info(c, pid, u)["entries"]), say=say)
                if added:
                    C.set_setting(cfg, "catalogs", cats)
                n_new = sum(map(len, added.values()))
                n_read = sum(map(len, todo.values()))
                say(f"Store nuovi: {n_new} · da leggere: {n_read} (i già presenti non si rifanno)")
                async def go():
                    async with make_client() as client:
                        for pid, urls in todo.items():
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
