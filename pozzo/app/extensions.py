"""Temi e plugin.

TEMI  - cartella <config>/themes/<id>/ (oppure quelli inclusi in pozzo/app/themes/):
    theme.json      {"name", "description", "author", "preview": "#colore", "desktop": "theme.css",
                     "bigpicture": "bigpicture.css"}
    theme.css       sovrascrive le variabili CSS (--bg, --accent, ...) e qualsiasi regola
    bigpicture.css  idem per la modalita' Big Picture

PLUGIN - cartella <config>/plugins/<id>/:
    plugin.toml     [plugin] name, version, author, description, api = 1
    frontend.js     (facoltativo) modulo ES: export default function (gs, ctx) { ... }
    style.css       (facoltativo)
    backend.py      (facoltativo) def register(gs): ...   con gs = PluginAPI (vedi sotto)

Un plugin backend puo': aggiungere endpoint (gs.route), fornitori di libreria/store (gs.add_store),
fonti di metadati per lo scraping (gs.add_source), reagire agli eventi (gs.on) e salvare
impostazioni proprie (gs.settings / gs.save_settings). Guida completa: docs/ESTENSIONI.md
"""

from __future__ import annotations

import importlib.util
import json
import queue
import re
import threading
import time
import tomllib
import traceback
from pathlib import Path

from .. import config as C

BUILTIN_THEMES = Path(__file__).parent / "themes"
BUILTIN_EXPERIENCES = Path(__file__).parent / "static" / "experiences"
DEFAULT_EXPERIENCE = {
    "id": "catalog", "name": "Catalogo", "description": "Interfaccia classica con navigazione laterale.",
    "nav": "sidebar", "detail": "panel",
}
EXPERIENCE_METADATA = {
    "original": {
        "name": "Originale", "description": "Interfaccia originale del launcher, con navigazione laterale e schede a pannello.",
        "nav": "sidebar", "detail": "panel",
    },
    "catalog": {
        "name": "Catalogo", "description": "Catalogo della libreria con navigazione laterale.",
        "nav": "sidebar", "detail": "panel",
    },
    "shelves": {
        "name": "Scaffali", "description": "Home a scaffali e navigazione principale superiore.",
        "nav": "top", "detail": "panel",
    },
    "console": {
        "name": "Console", "description": "Raccolta immersiva navigabile per selezione e messa a fuoco.",
        "nav": "none", "detail": "page",
    },
    "dashboard": {
        "name": "Dashboard", "description": "Home a gruppi di giochi con accesso rapido alle viste.",
        "nav": "top", "detail": "panel",
    },
    "cinema": {
        "name": "Cinema", "description": "Home con gioco in evidenza e file orizzontali per la raccolta.",
        "nav": "top", "detail": "modal",
    },
    "storefront": {
        "name": "Vetrina", "description": "Home curata con giochi in evidenza e sezioni della raccolta.",
        "nav": "top", "detail": "page",
    },
}
BUILTIN_PLUGINS = Path(__file__).parent / "plugins"  # solo lo store: le altre estensioni si installano da li'
STORE_ID = "estensioni"
API_VERSION = 1


def user_dir(cfg, name: str) -> Path:
    base = cfg.config_path.parent if cfg.config_path else Path.cwd()
    d = base / name
    d.mkdir(exist_ok=True)
    return d


class Events:
    """Bus di eventi lato server: game_launching (sincrono, prima dell'avvio), game_started, game_stopped, library_built, scrape_finished, stores_refreshed."""

    def __init__(self):
        self.handlers: dict[str, list] = {}
        self.subs: set[queue.Queue] = set()  # chi ascolta in streaming (SSE di /api/v1/events)
        self.lock = threading.Lock()

    def on(self, name: str, fn):
        with self.lock:
            self.handlers.setdefault(name, []).append(fn)

    def subscribe(self) -> queue.Queue:
        """Coda che riceve ogni evento come {"event", "data"}. Se si riempie, gli eventi nuovi si perdono:
        chi ascolta non deve poter bloccare l'app."""
        q: queue.Queue = queue.Queue(maxsize=100)
        with self.lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self.lock:
            self.subs.discard(q)

    def emit(self, name: str, data: dict | None = None):
        payload = data or {}
        with self.lock:
            handlers, subs = list(self.handlers.get(name, [])), list(self.subs)
        for fn in handlers:
            try:
                fn(payload)
            except Exception:
                traceback.print_exc()
        for q in subs:
            try:
                q.put_nowait({"event": name, "data": payload})
            except queue.Full:
                pass


# ---------------------------------------------------------------- temi

def theme_dirs(cfg) -> list[Path]:
    return [user_dir(cfg, "themes"), BUILTIN_THEMES]


def list_themes(cfg) -> list[dict]:
    out = [{"id": "default", "name": "Predefinito", "description": "Scuro, blu e viola", "preview": "#151821",
            "desktop": None, "bigpicture": None}]
    seen = {"default"}
    for base in theme_dirs(cfg):
        if not base.exists():
            continue
        for d in sorted(base.iterdir()):
            mf = d / "theme.json"
            try:
                if d.name in seen or not mf.exists():
                    continue
            except OSError:  # cartella illeggibile (es. WinError 448): il tema si salta
                continue
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            seen.add(d.name)
            out.append({"id": d.name, "name": m.get("name", d.name), "description": m.get("description", ""),
                        "author": m.get("author", ""), "preview": m.get("preview", "#1c2030"),
                        "desktop": f"/themes/{d.name}/{m['desktop']}" if m.get("desktop") else None,
                        "bigpicture": f"/themes/{d.name}/{m['bigpicture']}" if m.get("bigpicture") else None})
    return out


def active_experience(cfg) -> str:
    value = cfg.app.get("experience", "catalog")
    return value if isinstance(value, str) and value in {
        item["id"] for item in list_experiences() if item.get("available")
    } else "catalog"


_EXP_CACHE: tuple[float, list[dict]] | None = None


def list_experiences() -> list[dict]:
    """Esperienze incluse nell'app: l'elenco e' in cache (scansione su disco lenta, es. OneDrive)."""
    global _EXP_CACHE
    now = time.monotonic()
    if _EXP_CACHE is None or now - _EXP_CACHE[0] > 30:
        _EXP_CACHE = (now, _scan_experiences())
    return [dict(x) for x in _EXP_CACHE[1]]


def _scan_experiences() -> list[dict]:
    """Scopre esperienze incluse nell'app senza consentire cartelle o link esterni."""
    out, seen = [], set()
    try:
        dirs = sorted(BUILTIN_EXPERIENCES.iterdir()) if BUILTIN_EXPERIENCES.exists() else []
    except OSError:
        dirs = []
    root = BUILTIN_EXPERIENCES.resolve()
    for d in dirs:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", d.name):
            continue
        try:
            folder = d.resolve()
            if folder.parent != root or not folder.is_dir():
                continue
            metadata = EXPERIENCE_METADATA.get(d.name, {})
            has_script = experience_file(d.name, "experience.js") is not None
            has_style = experience_file(d.name, "style.css") is not None
            available = has_script or d.name == "catalog"
            out.append({
                "id": d.name,
                "name": metadata.get("name", d.name),
                "description": metadata.get("description", ""),
                "nav": metadata.get("nav", "sidebar"),
                "detail": metadata.get("detail", "panel"),
                "script": f"/experiences/{d.name}/experience.js" if has_script else None,
                "style": f"/experiences/{d.name}/style.css" if has_style else None,
                "available": available,
                "error": None if has_script else "File experience.js mancante" if d.name != "catalog" else
                    "File experience.js mancante: uso il comportamento predefinito sicuro.",
            })
            seen.add(d.name)
        except OSError as e:
            out.append({"id": d.name, "name": d.name, "description": "", "script": None, "style": None,
                        "available": False, "error": f"Cartella illeggibile: {e}"})
            seen.add(d.name)
    if "catalog" not in seen:
        out.insert(0, {**DEFAULT_EXPERIENCE, "script": None, "style": None, "available": True,
                       "error": "Cartella dell'esperienza predefinita mancante: uso il comportamento sicuro incorporato."})
    out.sort(key=lambda item: (item["id"] != "catalog", item["id"]))
    return out


def experience_file(experience_id: str, rel: str) -> Path | None:
    """Risoluzione statica limitata ai due asset previsti in ogni cartella esperienza."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", experience_id) or rel not in ("experience.js", "style.css"):
        return None
    try:
        root = (BUILTIN_EXPERIENCES / experience_id).resolve()
        f = (root / rel).resolve()
        if root.parent == BUILTIN_EXPERIENCES.resolve() and root.is_dir() and root in f.parents and f.is_file():
            return f
    except OSError:
        pass
    return None


def theme_file(cfg, tid: str, rel: str) -> Path | None:
    for base in theme_dirs(cfg):
        root = (base / tid).resolve()
        f = (root / rel).resolve()
        if root.is_dir() and root in f.parents and f.is_file():
            return f
    return None


# ---------------------------------------------------------------- plugin

def plugin_dirs(cfg) -> list[Path]:
    return [user_dir(cfg, "plugins"), BUILTIN_PLUGINS]


def _manifest(d: Path) -> dict | None:
    mf = d / "plugin.toml"
    if not mf.exists():
        return None
    try:
        return tomllib.loads(mf.read_text(encoding="utf-8")).get("plugin", {})
    except (OSError, tomllib.TOMLDecodeError):
        return None


def list_plugins(cfg, errors: dict | None = None) -> list[dict]:
    disabled = set(cfg.app.get("disabled_plugins", []))
    settings = C.read_settings(cfg).get("plugins", {})
    out, seen = [], set()
    for base in plugin_dirs(cfg):
        try:
            dirs = sorted(base.iterdir()) if base.exists() else []
        except OSError:  # cartella dei plugin illeggibile: l'app parte lo stesso, senza estensioni
            continue
        for d in dirs:
            if d.name.startswith(".") or d.name in seen:  # .<id>.tmp/.old: installazione dello store a meta'
                continue
            try:
                m = _manifest(d)
                if m is None or (d.name == STORE_ID and base != BUILTIN_PLUGINS):  # lo store e' sempre quello incluso nell'app
                    continue
                seen.add(d.name)
                out.append({
                    "id": d.name, "name": m.get("name", d.name), "version": str(m.get("version", "")),
                    "author": m.get("author", ""), "description": m.get("description", ""),
                    "enabled": (d.name not in disabled or d.name == STORE_ID) and int(m.get("api", 1)) <= API_VERSION,
                    "locked": d.name == STORE_ID,
                    "frontend": f"/plugins/{d.name}/frontend.js" if (d / "frontend.js").exists() else None,
                    "style": f"/plugins/{d.name}/style.css" if (d / "style.css").exists() else None,
                    "backend": str(d / "backend.py") if (d / "backend.py").exists() else None,
                    "settings": settings.get(d.name, {}), "error": (errors or {}).get(d.name),
                    "dir": str(d),
                })
            except OSError as e:
                # es. WinError 448: collegamento (junction) non attendibile. Una cartella illeggibile non deve
                # impedire l'avvio: l'estensione compare disattivata, con il motivo.
                seen.add(d.name)
                out.append({"id": d.name, "name": d.name, "version": "", "author": "", "description": "", "enabled": False,
                            "locked": False, "frontend": None, "style": None, "backend": None, "settings": {},
                            "error": f"Cartella illeggibile: {e}", "dir": str(d)})
    out.sort(key=lambda p: p["id"] != STORE_ID)  # lo store per primo: in Impostazioni e nella barra laterale
    return out


def plugin_file(cfg, pid: str, rel: str) -> Path | None:
    for base in plugin_dirs(cfg):
        try:
            root = (base / pid).resolve()
            f = (root / rel).resolve()
            if root.is_dir() and root in f.parents and f.is_file() and f.suffix in (".js", ".css", ".png", ".jpg",
                                                                                   ".svg", ".webp", ".json", ".woff2"):
                return f
        except OSError:  # percorso non attraversabile: come se non ci fosse
            continue
    return None


class PluginAPI:
    """Oggetto passato a backend.register(gs)."""

    def __init__(self, app, cfg, pid: str, events: Events, log):
        self.app, self.cfg, self.id, self.events, self.log = app, cfg, pid, events, log
        self.settings: dict = C.read_settings(cfg).get("plugins", {}).get(pid, {})

    def route(self, path: str, methods=("GET",)):
        """Decoratore: espone una funzione su /plugin-api/<id>/<path>."""
        def deco(fn):
            self.app.add_api_route(f"/plugin-api/{self.id}/{path.lstrip('/')}", fn, methods=list(methods))
            return fn
        return deco

    def add_store(self, provider_cls):
        """Aggiunge un fornitore di libreria (sottoclasse di pozzo.stores.base.StoreProvider)."""
        from .. import stores
        if provider_cls.id not in stores.BY_ID:
            stores.PROVIDERS.append(provider_cls)
            stores.BY_ID[provider_cls.id] = provider_cls
            stores.STORE_ORDER.append(provider_cls.id)

    def add_source(self, source_cls):
        """Aggiunge una fonte di metadati per lo scraping (sottoclasse di pozzo.sources.base.Source)."""
        from ..sources import SOURCES
        SOURCES[source_cls.name] = source_cls

    def on(self, event: str, fn):
        self.events.on(event, fn)

    def save_settings(self, values: dict):
        self.settings.update(values)
        C.save_settings(self.cfg, plugins={self.id: dict(self.settings)})


def load_backends(app, cfg, events: Events, log=print) -> dict[str, str]:
    """Carica i backend dei plugin abilitati. Ritorna {id: errore} per quelli falliti."""
    errors = {}
    for p in list_plugins(cfg):
        if not p["enabled"] or not p["backend"]:
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"gs_plugin_{p['id']}", p["backend"])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "register"):
                mod.register(PluginAPI(app, cfg, p["id"], events, log))
        except Exception as e:
            errors[p["id"]] = f"{type(e).__name__}: {e}"
            log(f"Plugin {p['id']}: errore nel backend: {errors[p['id']]}")
    return errors
