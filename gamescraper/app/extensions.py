"""Temi e plugin.

TEMI  - cartella <config>/themes/<id>/ (oppure quelli inclusi in gamescraper/app/themes/):
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
import threading
import tomllib
import traceback
from pathlib import Path

from .. import config as C

BUILTIN_THEMES = Path(__file__).parent / "themes"
BUILTIN_PLUGINS = Path(__file__).parent / "plugins"
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
        self.lock = threading.Lock()

    def on(self, name: str, fn):
        with self.lock:
            self.handlers.setdefault(name, []).append(fn)

    def emit(self, name: str, data: dict | None = None):
        for fn in list(self.handlers.get(name, [])):
            try:
                fn(data or {})
            except Exception:
                traceback.print_exc()


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
            if d.name in seen or not mf.exists():
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
        if not base.exists():
            continue
        for d in sorted(base.iterdir()):
            m = _manifest(d)
            if m is None or d.name in seen:
                continue
            seen.add(d.name)
            out.append({
                "id": d.name, "name": m.get("name", d.name), "version": str(m.get("version", "")),
                "author": m.get("author", ""), "description": m.get("description", ""),
                "enabled": d.name not in disabled and int(m.get("api", 1)) <= API_VERSION,
                "frontend": f"/plugins/{d.name}/frontend.js" if (d / "frontend.js").exists() else None,
                "style": f"/plugins/{d.name}/style.css" if (d / "style.css").exists() else None,
                "backend": str(d / "backend.py") if (d / "backend.py").exists() else None,
                "settings": settings.get(d.name, {}), "error": (errors or {}).get(d.name),
                "dir": str(d),
            })
    return out


def plugin_file(cfg, pid: str, rel: str) -> Path | None:
    for base in plugin_dirs(cfg):
        root = (base / pid).resolve()
        f = (root / rel).resolve()
        if root.is_dir() and root in f.parents and f.is_file() and f.suffix in (".js", ".css", ".png", ".jpg",
                                                                               ".svg", ".webp", ".json", ".woff2"):
            return f
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
        """Aggiunge un fornitore di libreria (sottoclasse di gamescraper.stores.base.StoreProvider)."""
        from .. import stores
        if provider_cls.id not in stores.BY_ID:
            stores.PROVIDERS.append(provider_cls)
            stores.BY_ID[provider_cls.id] = provider_cls
            stores.STORE_ORDER.append(provider_cls.id)

    def add_source(self, source_cls):
        """Aggiunge una fonte di metadati per lo scraping (sottoclasse di gamescraper.sources.base.Source)."""
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
