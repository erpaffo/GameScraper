"""Store delle estensioni: elenco da una o piu' fonti, installazione e rimozione nella cartella plugin dell'utente.

Una FONTE e' un `index.json` raggiungibile da un link. Lo stesso formato serve a tre casi:

    {"extensions": [{"id", "name", "version", "description", "author", "min_app",
                     "base": "./",                       # cartella dei file, relativa all'indice (o URL assoluto)
                     "files": {"plugin.toml": "<sha256>", ...}}],
     "repos": [{"repo": "utente/repo", "ref": "v1.0"},  # store: estensioni che vivono in altri repository
               {"url": "https://.../index.json"}]}

  - repository di un'estensione: un solo elemento in "extensions", con base "./";
  - store: un elenco di "repos", ognuno col proprio index.json (al tag indicato, cosi' lo store approva una versione);
  - indice classico: piu' elementi in "extensions" (extensions/index.json del repository dell'app).

Si installano solo file elencati nell'indice e solo se TUTTI hanno lo sha256 giusto: un backend.py gira con i permessi
dell'app, quindi niente file non dichiarati, niente sottocartelle, niente hash sbagliati.

Fonti: la prima e' sempre lo store ufficiale (OFFICIAL); le altre le aggiunge l'utente con un link GitHub o all'index.json
(app.extensions_sources in settings.json). app.extensions_index sostituisce lo store ufficiale, anche con un percorso
locale, per provare le estensioni prima del push.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .. import __version__
from ..updates import parse
from . import extensions as X

STORE_ID = X.STORE_ID  # lo store stesso: incluso nell'app, non si disattiva ne' si rimuove
# lo store sta nel repository dell'app (cartella extensions/): il vecchio repository gamescraper-extensions non esiste piu'
OFFICIAL = "https://raw.githubusercontent.com/erpaffo/Pozzo-Launcher/HEAD/extensions/index.json"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FILE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}$")
_GITHUB = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/tree/([\w./-]+?))?/?$")
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
_REF = re.compile(r"^[\w./-]{1,100}$")
MAX_FILE = 5 * 1024 * 1024
MAX_SOURCES = 20
MAX_REPOS = 200


def raw_index(repo: str, ref: str = "HEAD") -> str:
    return f"https://raw.githubusercontent.com/{repo}/{ref}/index.json"


def index_link(link: str) -> str:
    """Indirizzo dell'index.json di un link: repository GitHub (anche /tree/<ref>) oppure un .json diretto (https)."""
    link = link.strip()
    m = _GITHUB.match(link)
    if m:
        return raw_index(f"{m.group(1)}/{m.group(2)}", m.group(3) or "HEAD")
    if link.startswith("https://") and link.split("?")[0].endswith(".json"):
        return link
    raise ValueError("link non valido: serve un repository GitHub (https://github.com/utente/repo) "
                     "o l'indirizzo https di un index.json")


def sources(cfg) -> list[dict]:
    """Fonti in ordine: lo store ufficiale (o quello di prova), poi quelle aggiunte dall'utente."""
    first = cfg.app.get("extensions_index") or OFFICIAL
    out = [{"link": first, "official": True}]
    out += [{"link": s, "official": False} for s in cfg.app.get("extensions_sources") or [] if s != first]
    return out


def _get(url: str) -> bytes:
    if not url.startswith(("http://", "https://")):
        return Path(url).read_bytes()
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": f"PozzoLauncher/{__version__}"}) as c:
        r = c.get(url)
        r.raise_for_status()
        if len(r.content) > MAX_FILE:
            raise ValueError(f"file troppo grande: {url}")
        return r.content


def _join(base: str, rel: str) -> str:
    """Come urljoin, anche per un indice su disco: `rel` e' relativo alla cartella di `base`."""
    if base.startswith(("http://", "https://")) or rel.startswith(("http://", "https://")):
        return urljoin(base, rel)
    b = Path(base) if base.endswith(("/", "\\")) else Path(base).parent
    return str(b / rel) + ("/" if rel.endswith("/") else "")


def _entries(data: dict, url: str) -> list[dict]:
    out = []
    for e in data.get("extensions") or []:
        if isinstance(e, dict) and _ID.match(str(e.get("id", ""))) and e["id"] != STORE_ID and isinstance(e.get("files"), dict):
            out.append({**e, "base": _join(url, str(e.get("base") or e["id"] + "/"))})
    return out


def fetch_source(link: str) -> tuple[list[dict], list[str]]:
    """(estensioni, errori) di una fonte. Le voci "repos" di uno store si seguono un livello solo: un repository
    irraggiungibile non toglie gli altri, finisce negli errori."""
    url = link if not link.startswith("https://") else index_link(link)
    data = json.loads(_get(url))
    if not isinstance(data, dict):
        raise ValueError("indice non valido")
    out, errors = _entries(data, url), []
    for r in (data.get("repos") or [])[:MAX_REPOS]:
        try:
            if not isinstance(r, dict):
                raise ValueError("voce non valida")
            if r.get("url"):
                sub = str(r["url"])
                sub = sub if not sub.startswith(("http://", "https://")) else index_link(sub)
                if not sub.startswith(("http://", "https://")):
                    sub = _join(url, sub)  # percorso relativo allo store (indice su disco o sullo stesso sito)
            elif _REPO.match(str(r.get("repo", ""))) and _REF.match(str(r.get("ref") or "HEAD")):
                sub = raw_index(r["repo"], r.get("ref") or "HEAD")
            else:
                raise ValueError("voce senza repo o url")
            out += _entries(json.loads(_get(sub)), sub)
        except Exception as e:
            errors.append(f"{r.get('repo') or r.get('url') if isinstance(r, dict) else r}: {e}")
    return out, errors


def fetch_all(cfg) -> tuple[list[dict], list[dict]]:
    """Estensioni di tutte le fonti (a parita' di id vince la prima fonte, cioe' lo store ufficiale) e stato delle fonti."""
    items, seen, report = [], set(), []
    for s in sources(cfg):
        try:
            found, errors = fetch_source(s["link"])
        except Exception as e:
            found, errors = [], [f"{type(e).__name__}: {e}"]
        new = [e for e in found if e["id"] not in seen]
        seen |= {e["id"] for e in new}
        items += [{**e, "source": s["link"], "official": s["official"]} for e in new]
        report.append({**s, "count": len(found), "errors": errors})
    return items, report


def is_linked(d: Path) -> bool:
    """Cartella collegata (symlink o junction) ai sorgenti: e' la copia di sviluppo, lo store non la tocca."""
    return d.is_symlink() or (hasattr(d, "is_junction") and d.is_junction())


def compatible(e: dict) -> bool:
    return parse(str(e.get("min_app") or "0")) <= parse(__version__)


def _ver(v) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))) or (0,)


def catalog(cfg) -> dict:
    """Estensioni delle fonti con lo stato rispetto a quelle installate."""
    installed = {p["id"]: p for p in X.list_plugins(cfg)}
    found, report = fetch_all(cfg)
    items = []
    for e in found:
        p = installed.get(e["id"])
        d = Path(p["dir"]) if p else None
        state = ("incompatible" if not compatible(e) and not p
                 else "available" if not p
                 else "update" if _ver(e.get("version")) > _ver(p["version"]) and compatible(e)
                 else "installed")
        items.append({k: e.get(k) for k in ("id", "name", "version", "description", "author", "min_app", "source", "official")}
                     | {"state": state, "installed_version": p["version"] if p else None,
                        "dev": bool(d and is_linked(d)), "enabled": bool(p and p["enabled"])})
    return {"items": items, "sources": report, "app_version": __version__}


def add_source(cfg, link: str) -> list[str]:
    """Aggiunge una fonte (repository di un'estensione o altro store) dopo aver controllato che risponda."""
    from .. import config as C
    url = index_link(link)
    found, _ = fetch_source(link.strip())
    if not found:
        raise ValueError("a quel link non ci sono estensioni (index.json vuoto o senza estensioni valide)")
    cur = list(cfg.app.get("extensions_sources") or [])
    link = link.strip().rstrip("/")
    if link in cur or url in (index_link(s) for s in cur if s.startswith("https://")) or link == OFFICIAL:
        raise ValueError("fonte gia' presente")
    if len(cur) >= MAX_SOURCES:
        raise ValueError(f"al massimo {MAX_SOURCES} fonti")
    cur.append(link)
    C.save_settings(cfg, app={"extensions_sources": cur})
    cfg.app["extensions_sources"] = cur
    return cur


def remove_source(cfg, link: str) -> list[str]:
    from .. import config as C
    cur = [s for s in cfg.app.get("extensions_sources") or [] if s != link]
    if len(cur) == len(cfg.app.get("extensions_sources") or []):
        raise ValueError("fonte non trovata (lo store ufficiale non si rimuove)")
    C.save_settings(cfg, app={"extensions_sources": cur})
    cfg.app["extensions_sources"] = cur
    return cur


def install(cfg, ext_id: str, source: str | None = None) -> dict:
    if source and source not in (s["link"] for s in sources(cfg)):
        raise ValueError("fonte sconosciuta: aggiungila prima allo store")
    found = fetch_source(source)[0] if source else fetch_all(cfg)[0]
    e = next((x for x in found if x["id"] == ext_id), None)
    if e is None:
        raise ValueError(f"estensione non trovata nelle fonti: {ext_id}")
    if not compatible(e):
        raise ValueError(f"«{e.get('name', ext_id)}» richiede PozzoLauncher {e['min_app']} o successivo (hai {__version__})")
    files = e["files"]
    if "plugin.toml" not in files or not all(_FILE.match(str(n)) for n in files):
        raise ValueError("indice non valido: nomi di file non ammessi o plugin.toml mancante")
    root = X.user_dir(cfg, "plugins")
    dest = root / ext_id
    if is_linked(dest):
        raise ValueError("questa estensione e' collegata alla cartella di sviluppo: aggiornala da li'")
    # tutto in memoria e verificato prima di toccare il disco: un file sbagliato non lascia un'installazione a meta'
    blobs = {}
    for name, sha in files.items():
        b = _get(_join(e["base"], name))
        if hashlib.sha256(b).hexdigest() != str(sha).lower():
            raise ValueError(f"{name}: il contenuto non corrisponde all'indice (sha256 diverso), installazione annullata")
        blobs[name] = b
    tmp, old = root / f".{ext_id}.tmp", root / f".{ext_id}.old"
    for d in (tmp, old):
        shutil.rmtree(d, ignore_errors=True)
    tmp.mkdir()
    for name, b in blobs.items():
        (tmp / name).write_bytes(b)
    if dest.exists():
        dest.rename(old)
    tmp.rename(dest)
    shutil.rmtree(old, ignore_errors=True)
    return {"ok": True, "id": ext_id, "version": e.get("version"), "restart": True}


def remove(cfg, ext_id: str) -> dict:
    if ext_id == STORE_ID or not _ID.match(ext_id):
        raise ValueError("questa estensione non si puo' rimuovere")
    dest = X.user_dir(cfg, "plugins") / ext_id
    if is_linked(dest):
        raise ValueError("questa estensione e' collegata alla cartella di sviluppo: scollegala a mano")
    if not dest.is_dir():
        raise ValueError("estensione non installata")
    shutil.rmtree(dest)
    return {"ok": True, "id": ext_id, "restart": True}
