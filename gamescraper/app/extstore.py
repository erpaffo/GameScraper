"""Store delle estensioni: elenco da un indice online, installazione e rimozione nella cartella plugin dell'utente.

L'indice (extensions/index.json nel repository, generato da tools/extensions.py) elenca per ogni estensione i file
con il loro sha256. Si scaricano i file uno per uno e si installano solo se TUTTI corrispondono: un backend.py gira
con i permessi dell'app, quindi niente file non dichiarati, niente sottocartelle, niente hash sbagliati.

    {"extensions": [{"id", "name", "version", "description", "author", "min_app",
                     "base": "statistiche/",            # relativo all'indice, oppure un URL assoluto (repo a parte)
                     "files": {"plugin.toml": "<sha256>", ...}}]}

L'indirizzo dell'indice si puo' cambiare con app.extensions_index in settings.json (anche un percorso locale,
per provare le estensioni prima del push).
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .. import __version__
from ..updates import REPO, parse
from . import extensions as X

STORE_ID = X.STORE_ID  # lo store stesso: incluso nell'app, non si disattiva ne' si rimuove
INDEX_URL = f"https://raw.githubusercontent.com/{REPO}/main/extensions/index.json"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FILE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}$")
MAX_FILE = 5 * 1024 * 1024


def index_url(cfg) -> str:
    return cfg.app.get("extensions_index") or INDEX_URL


def _get(url: str) -> bytes:
    if not url.startswith(("http://", "https://")):
        return Path(url).read_bytes()
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": f"GameScraper/{__version__}"}) as c:
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


def fetch_index(cfg) -> list[dict]:
    import json
    url = index_url(cfg)
    data = json.loads(_get(url))
    out = []
    for e in data.get("extensions") or []:
        if isinstance(e, dict) and _ID.match(str(e.get("id", ""))) and e["id"] != STORE_ID and isinstance(e.get("files"), dict):
            out.append({**e, "base": _join(url, str(e.get("base") or e["id"] + "/"))})
    return out


def is_linked(d: Path) -> bool:
    """Cartella collegata (symlink o junction) ai sorgenti: e' la copia di sviluppo, lo store non la tocca."""
    return d.is_symlink() or (hasattr(d, "is_junction") and d.is_junction())


def compatible(e: dict) -> bool:
    return parse(str(e.get("min_app") or "0")) <= parse(__version__)


def _ver(v) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))) or (0,)


def catalog(cfg) -> dict:
    """Estensioni dell'indice con lo stato rispetto a quelle installate."""
    installed = {p["id"]: p for p in X.list_plugins(cfg)}
    items = []
    for e in fetch_index(cfg):
        p = installed.get(e["id"])
        d = Path(p["dir"]) if p else None
        state = ("incompatible" if not compatible(e) and not p
                 else "available" if not p
                 else "update" if _ver(e.get("version")) > _ver(p["version"]) and compatible(e)
                 else "installed")
        items.append({k: e.get(k) for k in ("id", "name", "version", "description", "author", "min_app")}
                     | {"state": state, "installed_version": p["version"] if p else None,
                        "dev": bool(d and is_linked(d)), "enabled": bool(p and p["enabled"])})
    return {"items": items, "app_version": __version__, "index": index_url(cfg)}


def install(cfg, ext_id: str) -> dict:
    e = next((x for x in fetch_index(cfg) if x["id"] == ext_id), None)
    if e is None:
        raise ValueError(f"estensione non trovata nell'indice: {ext_id}")
    if not compatible(e):
        raise ValueError(f"«{e.get('name', ext_id)}» richiede GameScraper {e['min_app']} o successivo (hai {__version__})")
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
