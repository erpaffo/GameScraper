"""Server locale del frontend (FastAPI).

Velocita' all'avvio:
- la libreria (library.db, gia' unita e con le collezioni calcolate) si carica in memoria in ms
- /api/library manda TUTTA la libreria in un unico JSON compatto (colonne, compresso gzip):
  il browser filtra/ordina/cerca in locale, senza altre richieste
- le copertine si servono come miniature WebP generate una volta e messe in cache su disco
  (una cover originale puo' pesare 1-2 MB; la miniatura ~15 KB), con revalidazione HTTP (ETag)
- la griglia del frontend e' virtualizzata: esistono nel DOM solo le copertine visibili
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import config as C
from .. import platforms as P
from ..library import launcher as L
from . import extensions as X
from .icons import CREDIT
from . import jobs as J
from .. import stores as STORES

STORE_NAMES = {p.id: p.name for p in STORES.PROVIDERS}
STORE_NAMES["online"] = "Store online"
_REMOTE_SEM = threading.Semaphore(4)  # immagini remote scaricate al massimo 4 alla volta
from ..library.build import build
from ..library.userdata import UserData
from ..matching import normalize

STATIC = Path(__file__).parent / "static"
THUMB_WIDTHS = (200, 300, 450, 700, 1280)


class Library:
    """Istantanea in memoria di library.db + dati utente."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = Path(cfg.cache) / "library.db"
        self.games: list[dict] = []
        self.by_id: dict[int, dict] = {}
        self.by_key: dict[str, dict] = {}
        self.groups: dict[str, list[dict]] = defaultdict(list)  # vuoti al primo avvio (libreria non ancora creata)
        self.auto: dict[str, dict] = {}
        self.built_at = 0.0
        self.lock = threading.RLock()

    def load(self):
        if not self.path.exists():
            return
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        games = [dict(r) for r in db.execute("SELECT * FROM games")]
        for g in games:
            g["genres"] = json.loads(g["genres"] or "[]")
            g["files"] = json.loads(g["files"] or "[]")
            g["media"] = json.loads(g["media"] or "{}")
            g["launch"] = json.loads(g.get("launch") or "{}")
        auto: dict[str, dict] = {}
        for slug, name in db.execute("SELECT slug, name FROM collections"):
            auto[slug] = {"name": name, "keys": set()}
        for slug, key in db.execute("SELECT slug, key FROM collection_games"):
            auto[slug]["keys"].add(key)
        built = db.execute("SELECT v FROM meta WHERE k='built_at'").fetchone()
        db.close()
        with self.lock:
            self.games = games
            self.by_id = {g["id"]: g for g in games}
            self.by_key = {g["key"]: g for g in games}
            self.groups: dict[str, list[dict]] = defaultdict(list)
            for g in games:
                self.groups[g["grp"]].append(g)
            self.auto = auto
            self.built_at = float(built[0]) if built else 0.0

    def get(self, gid: int) -> dict:
        g = self.by_id.get(gid)
        if not g:
            raise HTTPException(404, "Gioco non trovato")
        return g


def _players(v, platform: str = "") -> int:
    """Numero massimo di giocatori IN LOCALE (stesso schermo/console), 0 se sconosciuto.
    '1-4', '2', 'Up to 8'... -> il massimo. Valori da gioco online (es. 16, 64) vengono scartati:
    per i PC oltre 4 (le schede PC indicano quasi sempre i giocatori online), per le console oltre 8."""
    n = [int(x) for x in re.findall(r"\d+", str(v or ""))]
    m = max(n) if n else 0
    if m > (4 if platform == "windows" else 8):
        return 0
    return m


def _hltb_main(v) -> float | None:
    """Ore per finire la storia (o storia + extra) da HowLongToBeat, None se sconosciuto."""
    if not v:
        return None
    try:
        h = json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return None
    return h.get("main") or h.get("extra") or h.get("complete")


def _players_label(v, platform: str = "") -> str:
    m = _players(v, platform)
    return "" if not m else "1 (solo giocatore)" if m == 1 else f"1–{m} in locale"


def create_app(cfg) -> FastAPI:
    app = FastAPI(title="GameScraper")
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    lib = Library(cfg)
    lib.load()
    Path(cfg.cache).mkdir(parents=True, exist_ok=True)  # prima installazione: la cache non esiste ancora
    user = UserData(Path(cfg.cache) / "userdata.db")
    events = X.Events()
    launcher = L.Launcher(cfg, user, on_event=events.emit)
    plugin_errors = X.load_backends(app, cfg, events)
    thumbs_dir = Path(cfg.cache) / "thumbs"
    state = {"building": False, "build_log": [], "build_stats": None, "thumbs_total": 0, "thumbs_done": 0}
    job: dict = {"current": J.ScrapeJob(), "stores": J.StoreJob(), "storemeta": J.StoreMetaJob(), "hltb": J.HltbJob(), "collart": J.CollArtJob()}

    # ------------------------------------------------------------ vista unita (gruppi + utente)
    def view() -> dict:
        """Calcola gruppi, versione principale, dati utente e collezioni (modifiche incluse)."""
        with lib.lock:
            ug = user.games()
            uc = user.collections()
            edits = user.edits()
            groups = lib.groups
            # collezioni: automatiche + personali, a livello di GRUPPO (tutte le versioni di un gioco)
            colls: dict[str, dict] = {}
            for slug, c in lib.auto.items():
                grps = {lib.by_key[k]["grp"] for k in c["keys"] if k in lib.by_key}
                colls[slug] = {"slug": slug, "name": c["name"], "auto": True, "custom": False, "groups": grps}
            for slug, u in uc.items():
                if u["custom"]:
                    colls[slug] = {"slug": slug, "name": u["name"], "auto": False, "custom": True, "groups": set()}
                if slug in colls:
                    colls[slug]["name"] = u["name"] or colls[slug]["name"]
                    colls[slug]["hidden"] = bool(u["hidden"])
            for slug, key, action in edits:
                g = lib.by_key.get(key)
                if slug in colls and g:
                    (colls[slug]["groups"].add if action == "add" else colls[slug]["groups"].discard)(g["grp"])

            gdata = {}
            for grp, members in groups.items():
                pref = [m for m in members if ug.get(m["key"], {}).get("preferred") == "1"]
                primary = pref[0] if pref else next((m for m in members if m["is_primary"]), members[0])
                us = [ug.get(m["key"], {}) for m in members]
                gdata[grp] = {
                    "primary": primary["id"],
                    "favorite": int(any(u.get("favorite") for u in us)),
                    "hidden": int(any(u.get("hidden") for u in us)),
                    "last_played": max((u.get("last_played") or 0 for u in us), default=0),
                    "playtime": sum(u.get("playtime") or 0 for u in us),
                    "play_count": sum(u.get("play_count") or 0 for u in us),
                    # stato: quello della versione principale, altrimenti il primo impostato
                    "status": ug.get(primary["key"], {}).get("status") or next((u.get("status") for u in us if u.get("status")), "") or "",
                }
            return {"colls": colls, "groups": gdata}

    # ------------------------------------------------------------ API
    @app.get("/api/library")
    def api_library():
        v = view()
        with lib.lock:
            coll_list = sorted((c for c in v["colls"].values()), key=lambda c: c["name"].lower())
            coll_idx = {c["slug"]: i for i, c in enumerate(coll_list)}
            grp_colls: dict[str, list[int]] = defaultdict(list)
            for c in coll_list:
                for grp in c["groups"]:
                    grp_colls[grp].append(coll_idx[c["slug"]])
            grp_list = list(lib.groups)
            grp_idx = {g: i for i, g in enumerate(grp_list)}
            groups = []
            for grp in grp_list:
                d = v["groups"][grp]
                groups.append([d["primary"], d["favorite"], d["hidden"], round(d["last_played"]), round(d["playtime"]),
                               d["play_count"], grp_colls.get(grp, []), len(lib.groups[grp]), d["status"]])
            rows = [[g["id"], g["title"], g["sort_title"], g["platform"], g["year"], int("cover" in g["media"]),
                     grp_idx[g["grp"]], g["rating"], g["developer"] or "", ", ".join(g["genres"]), g["found"],
                     g["region"] or "", g.get("store") or "", 1 if g.get("store") is None else g.get("installed") or 0,
                     g.get("size") or 0, g.get("rating_count") or 0, g.get("rating_critics"), _players(g.get("players"), g["platform"]),
                     round((g.get("store_playtime") or 0) / 60), _hltb_main(g.get("hltb"))]
                    for g in lib.games]
            plats = defaultdict(int)
            for grp, members in lib.groups.items():
                if members[0].get("store") != "online":  # i giochi solo online non contano come posseduti
                    plats[members[0]["platform"]] += 1
            return {
                "built_at": lib.built_at,
                "platforms": sorted(({"id": p, "name": P.PLATFORMS[p].name if p in P.PLATFORMS else p, "count": n}
                                     for p, n in plats.items()), key=lambda x: x["name"].lower()),
                "collections": [{"slug": c["slug"], "name": c["name"], "auto": c["auto"], "custom": c["custom"],
                                 "hidden": c.get("hidden", False), "count": len(c["groups"])} for c in coll_list],
                "skip_duplicates": cfg.app.get("skip_duplicates", True),
                "catalogs": {k: v for k, v in cfg.catalogs.items() if v}, "icons_credit": CREDIT,
                "games": {"cols": ["id", "title", "sort", "platform", "year", "cover", "group", "rating", "developer",
                                   "genres", "found", "region", "store", "installed", "size", "votes", "critics", "players", "store_minutes", "hltb"],
                          "rows": rows},
                "stores": [{"id": p.id, "name": p.name} for p in STORES.PROVIDERS],
                "groups": {"cols": ["primary", "favorite", "hidden", "last_played", "playtime", "play_count",
                                    "collections", "versions", "status"], "rows": groups},
                "smart": cfg.app.get("smart_collections") or [],
            }

    FIELD_LABELS = {"title": "dati del gioco", "description": "descrizione", "developer": "sviluppatore",
                    "publisher": "editore", "genres": "generi", "release_date": "data di uscita",
                    "players": "giocatori", "rating": "valutazione"}
    MEDIA_LABELS = {"cover": "copertina", "screenshot": "screenshot", "title": "schermata del titolo",
                    "wheel": "logo (wheel)", "marquee": "marquee", "video": "video", "box3d": "box 3D",
                    "fanart": "fanart"}

    def _missing(g: dict) -> list[str]:
        if not g["found"]:
            return [FIELD_LABELS["title"]]
        out = [FIELD_LABELS.get(f, f) for f in cfg.fields
               if f != "title" and (g.get(f) in (None, "", []) if f in g else False)]
        out += [MEDIA_LABELS.get(m, m) for m in cfg.media if m not in g["media"]]
        return out

    @app.get("/api/game/{gid}")
    def api_game(gid: int):
        g = lib.get(gid)
        v = view()
        grp = v["groups"][g["grp"]]
        p = P.PLATFORMS.get(g["platform"])
        plan = L.plan(cfg, p) if p else None
        versions = [{"id": m["id"], "title": m["title"],
                     "file": (STORE_NAMES.get(m.get("store"), m.get("store")) if m.get("store")
                              else Path(m["files"][0]).name if m["files"] else m["title"]),
                     "region": m["region"], "store": m.get("store"), "installed": m.get("installed"),
                     "primary": m["id"] == grp["primary"]} for m in lib.groups[g["grp"]]]
        versions.sort(key=lambda x: (not x["primary"], x["file"].lower()))
        colls = [{"slug": c["slug"], "name": c["name"], "auto": c["auto"]}
                 for c in v["colls"].values() if g["grp"] in c["groups"]]
        return {**{k: g[k] for k in ("id", "key", "title", "platform", "year", "release_date", "developer", "publisher",
                                     "genres", "players", "rating", "description", "region", "files", "video_url",
                                     "canonical", "crc", "found")},
                "platform_name": p.name if p else g["platform"],
                "media": {k: f"/img/{gid}/{k}" for k in g["media"]},
                "sources": json.loads(g["origin"] or "{}"),
                "user": grp, "versions": versions, "collections": sorted(colls, key=lambda c: c["name"].lower()),
                "missing": [] if g.get("store") else _missing(g),
                "rating_count": g.get("rating_count"), "rating_critics": g.get("rating_critics"),
                "players_local": _players_label(g.get("players"), g["platform"]),
                "hltb": json.loads(g["hltb"]) if g.get("hltb") else None,
                "size": g.get("size"), "magnet": (g.get("launch") or {}).get("magnet") if g.get("store") == "online" else None,
                "file_name": g.get("canonical") if g.get("store") == "online" else None,
                "page_url": g.get("page") if g.get("store") == "online" else None,
                "ratings": json.loads(g.get("ratings") or "[]"),
                "store": g.get("store"), "store_name": STORE_NAMES.get(g.get("store"), ""),
                "installed": bool(g.get("installed")) if g.get("store") else True,
                "can_install": bool(g.get("install_uri")), "page": g.get("page"),
                "store_playtime": g.get("store_playtime") or 0,
                "emulator": (STORE_NAMES.get(g.get("store"), "PC") if g.get("store")
                             else (plan["emulator"] + (f" ({plan['core']})" if plan["core"] else "")) if plan else None)}

    def _member_keys(gid: int) -> list[str]:
        g = lib.get(gid)
        return [m["key"] for m in lib.groups[g["grp"]]]

    @app.post("/api/game/{gid}/flag")
    def api_flag(gid: int, body: dict = Body(...)):
        field, value = body.get("field"), body.get("value")
        if field not in ("favorite", "hidden", "status"):
            raise HTTPException(400, "campo non valido")
        if field == "status":
            if value not in ("", None, "backlog", "playing", "done", "dropped"):
                raise HTTPException(400, "stato non valido")
            value = value or None
        else:
            value = 1 if value else 0
        keys = _member_keys(gid)
        for k in keys:  # a livello di gioco: tutte le versioni
            user.set_flag(k, field, value)
        return {"ok": True}

    @app.post("/api/game/{gid}/preferred")
    def api_preferred(gid: int):
        g = lib.get(gid)
        for k in _member_keys(gid):
            user.set_flag(k, "preferred", "1" if k == g["key"] else None)
        return {"ok": True}

    @app.post("/api/game/{gid}/install")
    def api_install(gid: int):
        g = lib.get(gid)
        if not g.get("install_uri"):
            raise HTTPException(400, "Questo gioco non ha un collegamento per l'installazione")
        try:
            L.open_uri(g["install_uri"])
        except L.LaunchError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/game/{gid}/torrent")
    def api_torrent(gid: int):
        g = lib.get(gid)
        mag = (g.get("launch") or {}).get("magnet")
        if not mag:
            raise HTTPException(400, "Nessun link magnet per questo gioco")
        try:
            L.open_uri(mag)
        except L.LaunchError as e:
            raise HTTPException(409, "Nessun client torrent installato per aprire i link magnet")
        return {"ok": True}

    @app.post("/api/game/{gid}/store-page")
    def api_store_page(gid: int):
        g = lib.get(gid)
        if not g.get("page"):
            raise HTTPException(400, "Nessuna pagina dello store")
        try:
            L.open_uri(g["page"])
        except L.LaunchError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/game/{gid}/play")
    def api_play(gid: int):
        g = lib.get(gid)
        if g.get("store"):
            if not g.get("installed"):
                raise HTTPException(409, "Il gioco non e' installato: usa «Installa»")
            try:
                launcher.launch_pc(g["key"], g["title"], g["launch"], g.get("install_dir") or "",
                                   STORE_NAMES.get(g["store"], g["store"]))
            except L.LaunchError as e:
                raise HTTPException(409, str(e))
            return {"ok": True}
        p = P.PLATFORMS.get(g["platform"])
        path = next((Path(f) for f in g["files"] if Path(f).exists()), None)
        if not p or not path:
            raise HTTPException(404, "File del gioco non trovato (disco scollegato?)")
        try:
            launcher.launch(g["key"], g["title"], p, path)
        except L.LaunchError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.get("/api/running")
    def api_running():
        return launcher.running()

    @app.post("/api/running/stop")
    def api_running_stop():
        return {"stopped": launcher.stop()}

    @app.post("/api/game/{gid}/open-folder")
    def api_open_folder(gid: int):
        g = lib.get(gid)
        f = next((Path(x) for x in g["files"] if Path(x).exists()), None)
        if not f:
            raise HTTPException(404, "File non trovato")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(f)])
        else:
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", str(f.parent)])
        return {"ok": True}

    @app.post("/api/collections")
    def api_coll_create(body: dict = Body(...)):
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "nome mancante")
        slug = user.create_collection(name)
        if body.get("game"):
            for k in _member_keys(int(body["game"])):
                user.edit_membership(slug, k, "add")
        return {"slug": slug}

    @app.patch("/api/collections/{slug}")
    def api_coll_update(slug: str, body: dict = Body(...)):
        user.update_collection(slug, name=body.get("name"), hidden=body.get("hidden"))
        return {"ok": True}

    @app.delete("/api/collections/{slug}")
    def api_coll_delete(slug: str):
        if slug in lib.auto:  # le automatiche si nascondono, non si cancellano
            user.update_collection(slug, hidden=1)
        else:
            user.delete_collection(slug)
        return {"ok": True}

    @app.post("/api/collections/{slug}/members")
    def api_coll_members(slug: str, body: dict = Body(...)):
        action = body.get("action")
        if action not in ("add", "remove"):
            raise HTTPException(400, "azione non valida")
        for k in _member_keys(int(body["game"])):
            user.edit_membership(slug, k, action)
        return {"ok": True}

    # ------------------------------------------------------------ emulatori
    def _emu_row(pid: str) -> dict:
        p = P.PLATFORMS[pid]
        over = cfg.launch.get(pid, {})
        presets = L.PRESETS.get(pid, [])
        plan = L.plan(cfg, p)
        emu = over.get("emulator") or ""
        default_core = next((c for e, c in presets if e == L.RA), None)
        problem = None
        if not plan:
            if emu:
                exe = over.get("exe") or cfg.emulators.get(emu)
                if not exe:
                    problem = f"manca il percorso di {emu}"
                elif not Path(exe).exists():
                    problem = "eseguibile non trovato"
                elif emu == L.RA:
                    problem = f"core RetroArch non trovato: {over.get('core') or default_core}"
            else:
                ra_ok = cfg.emulators.get(L.RA) and Path(cfg.emulators[L.RA]).exists()
                ra_cores = [c for e, c in presets if e == L.RA and c]
                if ra_ok and ra_cores:
                    problem = f"RetroArch c'e', manca il core {' o '.join(ra_cores)} (scaricalo da RetroArch: Core > Scarica core)"
                elif presets:
                    problem = "installa uno tra: " + ", ".join(dict.fromkeys(e for e, _ in presets))
                else:
                    problem = "scegli un emulatore"
        if emu == L.RA:
            dargs = "-L {core} {rom}"
        else:
            dargs = " ".join(L.STANDALONE_ARGS.get(emu, ["{rom}"]))
        return {"platform": pid, "name": p.name, "emulator": emu,
                "core": over.get("core") or "", "args": " ".join(over.get("args") or []),
                "default_core": default_core or "", "default_args": dargs,
                "candidates": [e + (f" ({c})" if c else "") for e, c in presets],
                "effective": (plan["emulator"] + (f" ({plan['core']})" if plan["core"] else "")) if plan else None,
                "problem": problem}

    @app.get("/api/emulators")
    def api_emulators():
        with lib.lock:
            in_lib = sorted({g["platform"] for g in lib.games})
        pids = [p for p in in_lib if p in P.PLATFORMS]
        names = sorted(set(L.EMULATOR_NAMES) | {k for k in cfg.emulators if k != "retroarch_cores"}
                       | {v.get("emulator") for v in cfg.launch.values() if v.get("emulator")})
        return {"config": str(cfg.config_path or ""), "settings": str(C.settings_path(cfg)),
                "emulators": {k: v for k, v in cfg.emulators.items() if k != "retroarch_cores"},
                "retroarch_cores": cfg.emulators.get("retroarch_cores", ""),
                "known": names, "platforms": [_emu_row(p) for p in pids]}

    @app.post("/api/emulators")
    def api_emulators_save(body: dict = Body(...)):
        import shlex
        launch = {}
        for pid, v in (body.get("platforms") or {}).items():
            if pid not in P.PLATFORMS:
                continue
            emu = (v.get("emulator") or "").strip()
            if not emu:
                launch[pid] = None  # automatico: primo emulatore disponibile tra i preset
                continue
            entry = {"emulator": emu}
            if (v.get("core") or "").strip():
                entry["core"] = v["core"].strip()
            if (v.get("args") or "").strip():
                entry["args"] = [a.strip('"') for a in shlex.split(v["args"], posix=False)]
            launch[pid] = entry
        emus = {k: (v or "").strip() or None for k, v in (body.get("emulators") or {}).items()}
        if "retroarch_cores" in body:
            emus["retroarch_cores"] = (body.get("retroarch_cores") or "").strip() or None
        C.save_settings(cfg, launch=launch, emulators=emus)
        return api_emulators()

    @app.post("/api/emulators/detect")
    def api_emulators_detect(body: dict = Body(...)):
        folder = (body.get("folder") or "").strip()
        if not folder or not Path(folder).is_dir():
            raise HTTPException(400, "Cartella non valida")
        found = L.detect_emulators(Path(folder))
        if found:
            C.save_settings(cfg, emulators=found)
        return {"found": found, **api_emulators()}

    # ------------------------------------------------------------ account e store PC
    def _store_counts() -> dict:
        from ..cache import Cache
        out = {}
        c = Cache(cfg.cache)
        try:
            for p in STORES.PROVIDERS:
                snap = c.kv_get(f"store:{p.id}") or []
                out[p.id] = (len(snap), sum(1 for s in snap if s.get("installed")))
            out["_refreshed"] = c.kv_get("stores:refreshed") or 0
        finally:
            c.close()
        return out

    @app.get("/api/accounts")
    def api_accounts():
        from ..cache import Cache
        counts = _store_counts()
        c = Cache(cfg.cache)
        try:
            rows = []
            for P_ in STORES.PROVIDERS:
                prov = P_(cfg, c, None, lambda *a: None)
                try:
                    det, acc = prov.detected(), prov.account()
                except Exception as e:
                    det, acc = False, {"error": str(e)}
                total, inst = counts.get(P_.id, (0, 0))
                rows.append({"id": P_.id, "name": P_.name, "login": P_.login, "detected": det, "account": acc,
                             "enabled": STORES.enabled(cfg, P_.id), "games": total, "installed": inst,
                             "note": P_.owned_note, "login_url": getattr(P_, "login_url", None)})
        finally:
            c.close()
        return {"stores": rows, "refreshed": counts["_refreshed"], "job": job["stores"].state()}

    @app.post("/api/accounts/steam")
    def api_account_steam(body: dict = Body(...)):
        cur = dict(cfg.stores.get("steam", {}))
        for k in ("api_key", "steamid"):
            if k in body:
                cur[k] = (body[k] or "").strip()
        C.save_settings(cfg, stores={"steam": {k: v for k, v in cur.items() if v}})
        return api_accounts()

    @app.post("/api/accounts/{sid}/enabled")
    def api_account_enabled(sid: str, body: dict = Body(...)):
        if sid not in STORES.BY_ID:
            raise HTTPException(404)
        cur = dict(cfg.stores.get(sid, {}))
        cur["enabled"] = bool(body.get("enabled"))
        C.save_settings(cfg, stores={sid: cur})
        return api_accounts()

    @app.post("/api/accounts/{sid}/code")
    def api_account_code(sid: str, body: dict = Body(...)):
        """Completa il login (Epic/GOG) con il codice/pagina/URL ottenuti dalla pagina ufficiale."""
        import asyncio as _a
        from ..cache import Cache
        from ..net import make_client
        if sid not in ("epic", "gog"):
            raise HTTPException(404)

        async def go():
            c = Cache(cfg.cache)
            try:
                async with make_client() as client:
                    return await STORES.provider(sid, cfg, c, client).finish_login(body.get("code") or "")
            finally:
                c.close()
        try:
            user_name = _a.run(go())
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        _start_store_job([sid])
        return {"ok": True, "user": user_name}

    @app.post("/api/accounts/{sid}/logout")
    def api_account_logout(sid: str):
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            c.db.execute("DELETE FROM kv WHERE k=?", (f"auth:{sid}",))
            c.commit()
        finally:
            c.close()
        return api_accounts()

    def _start_store_job(only=None):
        if job["stores"].status == "running":
            raise HTTPException(409, "Aggiornamento degli store gia' in corso")
        j = J.StoreJob(only=only)
        job["stores"] = j

        def after(stats):
            lib.load()
            events.emit("stores_refreshed", stats)
            events.emit("library_built", stats)
            threading.Thread(target=_pregen_thumbs, daemon=True).start()

        threading.Thread(target=j.run, args=(cfg, after), daemon=True).start()
        return j.state()

    @app.post("/api/stores/refresh")
    def api_stores_refresh(body: dict = Body(default={})):
        return _start_store_job(body.get("only"))

    @app.get("/api/stores/refresh")
    def api_stores_state():
        return job["stores"].state()

    # ------------------------------------------------------------ configurazione guidata (primo avvio)
    setup_state: dict = {"status": "idle", "phase": "", "log": []}

    def _setup_done() -> bool:
        return bool(cfg.app.get("setup_done")) or lib.built_at > 0

    def _setup_stores() -> list[dict]:
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            out = []
            for P_ in STORES.PROVIDERS:
                if P_.id == "manual":  # i giochi aggiunti a mano non sono uno store da importare
                    continue
                try:
                    det = P_(cfg, c, None, lambda *a: None).detected()
                except Exception:
                    det = False
                out.append({"id": P_.id, "name": P_.name, "detected": det, "enabled": STORES.enabled(cfg, P_.id),
                            "login": P_.login})
            return out
        finally:
            c.close()

    @app.get("/api/setup")
    def api_setup():
        roms = Path(cfg.roms)
        return {"done": _setup_done(), "has_library": bool(lib.games) or lib.built_at > 0,
                "roms": str(roms) if roms.is_dir() else "", "data_dir": str(C.settings_path(cfg).parent),
                "accounts": {"ss_user": cfg.ss.get("user") or "", "ss_password": bool(cfg.ss.get("password")),
                             "igdb_id": cfg.igdb.get("client_id") or "", "igdb_secret": bool(cfg.igdb.get("client_secret")),
                             "rawg": bool(cfg.rawg.get("api_key")), "tgdb": bool(cfg.tgdb.get("api_key"))},
                "stores": _setup_stores(), "state": setup_state}

    @app.post("/api/setup/check-roms")
    def api_setup_check(body: dict = Body(...)):
        """Console trovate in una cartella, senza salvarla."""
        from dataclasses import replace as _replace

        from ..scanner import find_platform_dirs
        p = Path(os.path.expandvars(os.path.expanduser(str(body.get("path") or "").strip().strip('"'))))
        if not str(body.get("path") or "").strip() or not p.is_dir():
            raise HTTPException(400, "Cartella non trovata")
        found = find_platform_dirs(_replace(cfg, roms=p))
        rows = {}
        for plat, d in found:
            rows.setdefault(plat.id, {"id": plat.id, "name": plat.name, "folders": 0})
            rows[plat.id]["folders"] += 1
        return {"path": str(p.resolve()), "platforms": sorted(rows.values(), key=lambda r: r["name"].lower())}

    @app.post("/api/setup")
    def api_setup_save(body: dict = Body(...)):
        """Salva la configurazione; con start=true importa gli store, crea la libreria e
        (se richiesto) avvia lo scraping dei giochi nuovi in sottofondo."""
        sections: dict = {}
        roms = str(body.get("roms") or "").strip().strip('"')
        if roms:
            p = Path(os.path.expandvars(os.path.expanduser(roms)))
            if not p.is_dir():
                raise HTTPException(400, "Cartella delle ROM non trovata")
            sections["paths"] = {"roms": str(p.resolve())}
        acc = body.get("accounts") or {}
        ss = {k: v.strip() for k, v in (("user", acc.get("ss_user")), ("password", acc.get("ss_password"))) if v and v.strip()}
        if ss:
            sections["screenscraper"] = ss
        ig = {k: v.strip() for k, v in (("client_id", acc.get("igdb_id")), ("client_secret", acc.get("igdb_secret")))
              if v and v.strip()}
        if ig:
            sections["igdb"] = ig
        if (acc.get("rawg") or "").strip():
            sections["rawg"] = {"api_key": acc["rawg"].strip()}
        if (acc.get("tgdb") or "").strip():
            sections["thegamesdb"] = {"api_key": acc["tgdb"].strip()}
        stores = body.get("stores")
        if isinstance(stores, dict):
            sections["stores"] = {sid: {**dict(cfg.stores.get(sid, {})), "enabled": bool(on)}
                                  for sid, on in stores.items() if sid in STORES.BY_ID}
        sections["app"] = {"setup_done": True}
        C.save_settings(cfg, **sections)
        if body.get("start"):
            if setup_state["status"] == "running":
                raise HTTPException(409, "Configurazione gia' in corso")
            threading.Thread(target=_setup_run, args=(bool(body.get("scrape", True)),), daemon=True).start()
        return api_setup()

    def _setup_run(scrape: bool):
        say = lambda m: setup_state["log"].append(_plain(str(m)))  # noqa: E731
        setup_state.update(status="running", log=[], phase="Preparazione")
        try:
            sel = [s["id"] for s in _setup_stores() if s["enabled"] and s["detected"]]
            if sel:
                setup_state["phase"] = "Importazione dei giochi degli store"
                sj = J.StoreJob(only=sel)
                job["stores"] = sj
                sj.say = lambda m, _j=sj: (_j.log.append(_plain(m)), say(m))
                sj.run(cfg, lambda stats: lib.load())
            if not lib.built_at or not sel:
                setup_state["phase"] = "Creazione della libreria"
                state["build_log"] = []
                build(cfg, log=say)
                lib.load()
            threading.Thread(target=_pregen_thumbs, daemon=True).start()
            if scrape and Path(cfg.roms).is_dir():
                setup_state["phase"] = "Scraping dei metadati avviato in sottofondo"
                try:
                    api_scan_start({"mode": "new"})
                except HTTPException as e:
                    say(e.detail)
            setup_state.update(status="done", phase="Pronto")
        except Exception as e:
            setup_state.update(status="error", phase=f"Errore: {type(e).__name__}: {e}")
            say(setup_state["phase"])

    @app.post("/api/roms")
    def api_roms(body: dict = Body(...)):
        """Cartella radice delle ROM scelta dall'app (salvata in settings.json)."""
        p = Path(os.path.expandvars(os.path.expanduser(str(body.get("path") or "").strip().strip('"'))))
        if not str(p) or not p.is_dir():
            raise HTTPException(400, "Cartella non trovata")
        C.save_settings(cfg, paths={"roms": str(p.resolve())})
        return {"root": str(cfg.roms)}

    @app.post("/api/stores/metadata")
    def api_storemeta_start(body: dict = Body(default={})):
        if job["storemeta"].status == "running":
            raise HTTPException(409, "Ricerca dei metadati degli store gia' in corso")
        if job["stores"].status == "running":
            raise HTTPException(409, "Attendi la fine dell'aggiornamento degli store")
        j = J.StoreMetaJob(only_missing=bool(body.get("missing")))
        job["storemeta"] = j

        def after(stats):
            lib.load()
            events.emit("library_built", stats)

        threading.Thread(target=j.run, args=(cfg, after), daemon=True).start()
        return j.state()

    @app.get("/api/stores/metadata")
    def api_storemeta_state():
        return job["storemeta"].state()

    @app.post("/api/stores/metadata/cancel")
    def api_storemeta_cancel():
        job["storemeta"].stop = True
        return job["storemeta"].state()

    @app.post("/api/manual")
    def api_manual_add(body: dict = Body(...)):
        import shlex
        exe = (body.get("exe") or "").strip().strip('"')
        if not exe or not Path(exe).is_file():
            raise HTTPException(400, "Seleziona un file .exe esistente")
        title = (body.get("title") or "").strip() or Path(exe).stem
        args = [a.strip('"') for a in shlex.split(body.get("args") or "", posix=False)]
        user.add_manual(title, exe, args, (body.get("cwd") or "").strip())
        return _start_store_job(["manual"])

    @app.delete("/api/manual/{key:path}")
    def api_manual_delete(key: str):
        g = lib.by_key.get(key)
        if not g or g.get("store") != "manual":
            raise HTTPException(404)
        user.delete_manual(int(key.split(":", 1)[1]))
        return _start_store_job(["manual"])

    # ------------------------------------------------------------ fonti, limiti, database locali, pianificazione
    SOURCE_INFO = {
        "libretro": ("offline", "Identificazione per CRC/nome (No-Intro, Redump), franchise, immagini libretro"),
        "launchbox": ("offline", "Valutazioni della community, descrizioni, generi, ESRB, video, immagini e loghi"),
        "tgdb": ("offline", "Copia completa di TheGamesDB: descrizioni, giocatori, copertine (nessuna quota)"),
        "arcadedb": ("online", "Solo arcade (MAME): dati, immagini, video"),
        "igdb": ("online", "Descrizioni, valutazioni utenti e critica, franchise, copertine"),
        "screenscraper": ("online", "Ricerca per checksum, descrizioni in italiano, wheel, video (quota del tuo account)"),
        "rawg": ("online", "Valutazioni utenti e voto Metacritic (chiave gratuita, 20.000 richieste/mese)"),
        "thegamesdb": ("online", "API di TheGamesDB (quota mensile molto bassa: meglio la copia completa)"),
        "wikipedia": ("online", "Descrizioni, sviluppatori, editori, generi e anno da Wikipedia (italiano, poi inglese): "
                                "gratuita, senza chiave, solo per i dati ancora mancanti"),
    }
    bulk_job: dict = {"status": "idle", "log": []}

    def _sources_state():
        from ..bulk import BULK
        from ..policy import DEFAULTS, Ledger, policy_for
        from ..sources import SOURCES
        ledger = Ledger(Path(cfg.cache) / "cache.db")
        try:
            usage = ledger.snapshot()
        finally:
            ledger.close()
        order = list(cfg.sources) + [x for x in SOURCES if x not in cfg.sources]
        rows = []
        for sid in order:
            kind, desc = SOURCE_INFO.get(sid, ("online", ""))
            pol = policy_for(cfg, sid)
            rows.append({"id": sid, "kind": kind, "description": desc, "enabled": sid in cfg.sources,
                         "policy": {"rate": pol.rate, "daily": pol.daily, "monthly": pol.monthly},
                         "usage": usage.get(sid, {"today": 0, "month": 0}),
                         "needs_key": sid in ("rawg", "igdb", "screenscraper", "thegamesdb"),
                         "has_key": bool({"rawg": cfg.rawg.get("api_key"), "igdb": cfg.igdb.get("client_id"),
                                          "screenscraper": cfg.ss.get("devid"), "thegamesdb": cfg.tgdb.get("api_key")}.get(sid, True))})
        return {"sources": rows, "bulk": [B(cfg.cache).status() for B in BULK.values()], "bulk_job": bulk_job,
                "schedule": cfg.app.get("schedule", {"enabled": False, "time": "03:00", "mode": "missing"}),
                "schedule_last": cfg.app.get("schedule_last", "")}

    @app.get("/api/sources")
    def api_sources():
        return _sources_state()

    @app.post("/api/sources")
    def api_sources_save(body: dict = Body(...)):
        from ..sources import SOURCES
        sections = {}
        if isinstance(body.get("order"), list):
            order = [x for x in body["order"] if x in SOURCES]
            if order:
                sections["scrape"] = {"sources": order}
                cfg.sources = order
        if isinstance(body.get("limits"), dict):
            from ..policy import DEFAULTS, Policy
            lim = {}
            for sid, v in body["limits"].items():
                base = Policy(sid, *DEFAULTS[sid]) if sid in DEFAULTS else Policy(sid)
                clean = {}
                for k in ("rate", "daily", "monthly"):
                    raw_v = v.get(k)
                    val = (float(raw_v) if k == "rate" else int(float(raw_v))) if raw_v not in (None, "") else 0
                    if k == "rate" and val <= 0:
                        raise HTTPException(400, f"{sid}: il ritmo deve essere maggiore di zero")
                    if val != getattr(base, k):  # si salvano solo i valori cambiati rispetto ai predefiniti
                        clean[k] = val
                lim[sid] = clean or None
                if not clean:
                    cfg.limits.pop(sid, None)
            sections["limits"] = lim
        if "rawg_key" in body:
            sections["rawg"] = {"api_key": (body["rawg_key"] or "").strip() or None}
        if isinstance(body.get("schedule"), dict):
            sch = body["schedule"]
            sections["app"] = {"schedule": {"enabled": bool(sch.get("enabled")), "time": str(sch.get("time") or "03:00")[:5],
                                            "mode": sch.get("mode") if sch.get("mode") in ("new", "missing") else "missing"}}
        C.save_settings(cfg, **sections)
        return _sources_state()

    @app.post("/api/bulk/{bid}/refresh")
    def api_bulk_refresh(bid: str):
        from ..bulk import BULK
        if bid not in BULK:
            raise HTTPException(404)
        if bulk_job["status"] == "running":
            raise HTTPException(409, "Aggiornamento gia' in corso")

        def run():
            import asyncio as _a
            from ..net import make_client
            bulk_job.update(status="running", log=[], id=bid)

            async def go():
                async with make_client() as client:
                    return await BULK[bid](cfg.cache).ensure(client, log=lambda m: bulk_job["log"].append(str(m)), force=True)
            try:
                bulk_job["status"] = "done" if _a.run(go()) else "error"
            except Exception as e:
                bulk_job["log"].append(f"Errore: {e}")
                bulk_job["status"] = "error"
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    @app.post("/api/scan/plan")
    def api_scan_plan(body: dict = Body(default={})):
        from ..cache import Cache
        from ..pipeline import plan
        from ..scanner import find_platform_dirs, scan
        c = Cache(cfg.cache)
        try:
            targets = find_platform_dirs(cfg)
            plats = [p for p in (body.get("platforms") or []) if p in P.PLATFORMS]
            if plats:
                targets = [t for t in targets if t[0].id in plats]
            games = scan(cfg, c, targets)
            for g in games:
                g.needs_hash = False
            mode = body.get("mode") or "new"
            return {"plan": plan(cfg, c, games, cfg.sources, refresh=mode == "all", retry_missing=mode == "missing"),
                    "games": len(games)}
        finally:
            c.close()

    def _scheduler():
        """Scraping automatico all'ora scelta (se l'app e' aperta), una volta al giorno, entro i limiti."""
        import datetime as _dt
        while True:
            time.sleep(60)
            try:
                sch = cfg.app.get("schedule") or {}
                if not sch.get("enabled"):
                    continue
                now = _dt.datetime.now()
                today = now.strftime("%Y-%m-%d")
                hh, mm = (int(x) for x in str(sch.get("time", "03:00")).split(":")[:2])
                if now.hour * 60 + now.minute < hh * 60 + mm or cfg.app.get("schedule_last") == today:
                    continue
                if job["current"].status == "running" or state["building"] or job["stores"].status == "running":
                    continue
                C.save_settings(cfg, app={"schedule_last": today})
                api_scan_start({"mode": sch.get("mode", "missing")})
            except Exception as e:
                print(f"Pianificazione: {e}")

    threading.Thread(target=_scheduler, daemon=True).start()

    # ------------------------------------------------------------ store online per console, console personalizzate
    cat_job: dict = {"status": "idle", "log": []}

    @app.get("/api/catalogs/{pid}")
    def api_catalog_get(pid: str):
        from ..cache import Cache
        from ..catalog import catalog_key
        c = Cache(cfg.cache)
        try:
            pages = []
            for u in cfg.catalogs.get(pid, []):
                d = c.kv_get(catalog_key(pid, u)) or {}
                pages.append({"url": u, "games": len(d.get("entries", [])), "time": d.get("time", 0)})
        finally:
            c.close()
        return {"platform": pid, "pages": pages, "job": cat_job}

    @app.post("/api/catalogs/{pid}")
    def api_catalog_set(pid: str, body: dict = Body(...)):
        if pid not in P.PLATFORMS:
            raise HTTPException(404, "Console sconosciuta")
        urls = [u.strip() for u in body.get("urls", []) if u.strip().startswith(("http://", "https://"))]
        C.set_setting(cfg, "catalogs", {**C.read_settings(cfg).get("catalogs", {}), pid: urls})
        if body.get("refresh", True) and urls:
            _start_catalog_job(pid)
        elif not urls:
            threading.Thread(target=lambda: (build(cfg, log=lambda m: None), lib.load()), daemon=True).start()
        return api_catalog_get(pid)

    def _start_catalog_job(pid: str):
        if cat_job["status"] == "running":
            raise HTTPException(409, "Lettura degli store online gia' in corso")

        def run():
            import asyncio as _a

            from .. import catalog
            from ..cache import Cache
            from ..net import make_client
            cat_job.update(status="running", log=[], platform=pid)
            say = lambda m: cat_job["log"].append(str(m))  # noqa: E731
            c = Cache(cfg.cache)
            try:
                async def go():
                    async with make_client() as client:
                        for u in cfg.catalogs.get(pid, []):
                            try:
                                await catalog.fetch(cfg, c, client, P.PLATFORMS[pid], u, log=say)
                            except Exception as e:
                                say(f"{u}: {e}")
                _a.run(go())
                c.close()
                c = None
                say("Aggiornamento della libreria…")
                build(cfg, log=say)
                lib.load()
                cat_job["status"] = "done"
            except Exception as e:
                say(f"Errore: {type(e).__name__}: {e}")
                cat_job["status"] = "error"
            finally:
                if c:
                    c.close()
        threading.Thread(target=run, daemon=True).start()

    @app.post("/api/catalogs/{pid}/refresh")
    def api_catalog_refresh(pid: str):
        _start_catalog_job(pid)
        return api_catalog_get(pid)

    @app.post("/api/platforms/custom")
    def api_custom_platform(body: dict = Body(...)):
        import re as _re
        name = (body.get("name") or "").strip()
        pid = _re.sub(r"[^a-z0-9_-]", "", (body.get("id") or name).lower().replace(" ", "-"))
        if not name or not pid:
            raise HTTPException(400, "Servono nome e identificativo")
        if pid in P.PLATFORMS and not P.PLATFORMS[pid].extra.get("custom"):
            raise HTTPException(409, f"«{pid}» esiste gia' tra le console incluse")
        exts = " ".join(e.lower().lstrip(".") for e in str(body.get("exts", "")).replace(",", " ").split())
        if not exts:
            raise HTTPException(400, "Indica almeno un'estensione dei file (es. iso chd)")
        item = {"id": pid, "name": name, "exts": exts, "aliases": [a.strip() for a in body.get("aliases", []) if a.strip()],
                "cd": bool(body.get("cd")), "dir_games": bool(body.get("dir_games")),
                "igdb": str(body.get("igdb") or ""), "ss": str(body.get("ss") or "")}
        items = [x for x in C.read_settings(cfg).get("custom_platforms", []) if x.get("id") != pid] + [item]
        C.set_setting(cfg, "custom_platforms", items)
        return {"ok": True, "id": pid}

    @app.delete("/api/platforms/custom/{pid}")
    def api_custom_platform_delete(pid: str):
        items = [x for x in C.read_settings(cfg).get("custom_platforms", []) if x.get("id") != pid]
        C.set_setting(cfg, "custom_platforms", items)
        P.unregister_custom(pid)
        return {"ok": True}

    # ------------------------------------------------------------ scraping dall'app
    @app.get("/api/platforms")
    def api_platforms():
        return [{"id": p.id, "name": p.name, "custom": bool(p.extra.get("custom")), "exts": " ".join(p.exts),
                 "aliases": list(p.aliases)} for p in sorted(P.PLATFORMS.values(), key=lambda x: x.name.lower()) if not p.virtual]

    @app.get("/api/scan/preview")
    def api_scan_preview():
        with lib.lock:
            files = {f for g in lib.games for f in g["files"]}
            incomplete: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for g in lib.games:
                if not g["is_primary"]:
                    continue
                if not g["found"]:
                    incomplete[g["platform"]][0] += 1
                elif _missing(g):
                    incomplete[g["platform"]][1] += 1
        try:
            return J.preview(cfg, files, {k: tuple(v) for k, v in incomplete.items()})
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/folders")
    def api_folders(body: dict = Body(...)):
        folder, pid = (body.get("folder") or "").strip(), (body.get("platform") or "").strip()
        if not folder or (pid and pid not in P.PLATFORMS):
            raise HTTPException(400, "dati non validi")
        C.save_settings(cfg, platform_folders={folder: pid or None})
        return api_scan_preview()

    @app.post("/api/scan")
    def api_scan_start(body: dict = Body(default={})):
        cur = job["current"]
        if cur.status == "running" or state["building"]:
            raise HTTPException(409, "Uno scraping o un aggiornamento e' gia' in corso")
        plats = [p for p in (body.get("platforms") or []) if p in P.PLATFORMS] or None
        mode = body.get("mode") or ("all" if body.get("refresh") else "new")
        keys, title = None, ""
        if body.get("game"):  # pulsante "Cerca dati mancanti" nel dettaglio: tutte le versioni del gioco
            g = lib.get(int(body["game"]))
            keys = [m["key"] for m in lib.groups[g["grp"]]]
            plats, title, mode = [g["platform"]], g["title"], body.get("mode") or "missing"
        j = J.ScrapeJob(platforms=plats, refresh=mode == "all", retry_missing=mode == "missing", keys=keys, title=title)
        job["current"] = j

        def after(stats):
            state["build_stats"] = stats
            lib.load()
            events.emit("scrape_finished", stats)
            events.emit("library_built", stats)
            threading.Thread(target=_pregen_thumbs, daemon=True).start()

        threading.Thread(target=j.run, args=(cfg, after), daemon=True).start()
        return j.state()

    @app.get("/api/scan")
    def api_scan_state():
        return job["current"].state()

    @app.post("/api/scan/cancel")
    def api_scan_cancel():
        job["current"].cancel()
        return job["current"].state()

    @app.get("/api/status")
    def api_status():
        return {k: state[k] for k in ("building", "build_stats", "thumbs_total", "thumbs_done")} | {
            "build_log": state["build_log"][-8:], "has_library": bool(lib.games) or lib.built_at > 0}

    def _rebuild():
        if job["current"].status == "running":
            return
        state["building"] = True
        state["build_log"] = []
        try:
            state["build_stats"] = build(cfg, log=lambda m: state["build_log"].append(_plain(str(m))))
            lib.load()
            threading.Thread(target=_pregen_thumbs, daemon=True).start()
        except Exception as e:
            state["build_log"].append(f"Errore: {type(e).__name__}: {e}")
        finally:
            state["building"] = False

    @app.post("/api/rebuild")
    def api_rebuild():
        if not state["building"]:
            threading.Thread(target=_rebuild, daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------ immagini
    def _thumb(src: Path, w: int) -> Path:
        h = hashlib.sha1(f"{src}:{src.stat().st_mtime_ns}".encode()).hexdigest()[:20]
        out = thumbs_dir / str(w) / f"{h}.webp"
        if out.exists():
            return out
        from PIL import Image
        out.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im.thumbnail((w, w * 3), Image.Resampling.LANCZOS)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            tmp = out.with_suffix(".tmp")
            im.save(tmp, "WEBP", quality=82, method=4)
            os.replace(tmp, out)
        return out

    @app.get("/img/{gid}/{kind}")
    def api_img(gid: int, kind: str, request: Request, w: int = 0):
        g = lib.get(gid)
        rel = g["media"].get(kind)
        if not rel:
            raise HTTPException(404)
        if rel.startswith("url:"):  # immagine remota (giochi degli store online): scaricata solo quando serve
            rel = _fetch_remote(rel[4:])
            if not rel:
                raise HTTPException(404)
        src = Path(cfg.cache) / rel
        if not src.exists():
            raise HTTPException(404)
        # i numeri dei giochi cambiano quando la libreria viene ricostruita: il browser deve sempre
        # ricontrollare. L'ETag identifica il FILE (percorso + data + dimensione): se e' lo stesso si
        # risponde 304 e il browser usa la sua copia, altrimenti riceve l'immagine giusta.
        st = src.stat()
        etag = '"' + hashlib.sha1(f"{rel}:{st.st_mtime_ns}:{st.st_size}:{w}".encode()).hexdigest()[:20] + '"'
        headers = {"Cache-Control": "no-cache", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        if w:
            w = min(THUMB_WIDTHS, key=lambda x: abs(x - w))
            try:
                return FileResponse(_thumb(src, w), media_type="image/webp", headers=headers)
            except Exception:
                pass  # immagine non decodificabile: si serve l'originale
        return FileResponse(src, headers=headers)

    def _fetch_remote(url_: str) -> str | None:
        import httpx

        from ..net import UA, sniff
        h = hashlib.sha1(url_.encode()).hexdigest()
        folder = Path(cfg.cache) / "media" / "net"
        hit = next(iter(folder.glob(h + ".*")), None) if folder.exists() else None
        if hit:
            return hit.relative_to(cfg.cache).as_posix()
        with _REMOTE_SEM:
            try:
                r = httpx.get(url_, timeout=20, follow_redirects=True, headers={"User-Agent": UA})
            except httpx.HTTPError:
                return None
        ext = sniff(r.content[:16]) if r.status_code == 200 else None
        if not ext:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{h}.{ext}"
        dest.write_bytes(r.content)
        return dest.relative_to(cfg.cache).as_posix()

    def _pregen_thumbs():
        """Genera in background le miniature delle copertine, cosi' lo scorrimento e' fluido da subito."""
        with lib.lock:
            srcs = [Path(cfg.cache) / g["media"]["cover"] for g in lib.games
                    if "cover" in g["media"] and not g["media"]["cover"].startswith("url:")]
        state["thumbs_total"], state["thumbs_done"] = len(srcs), 0

        def one(s):
            try:
                if s.exists():
                    _thumb(s, 300)
            except Exception:
                pass
            state["thumbs_done"] += 1

        with ThreadPoolExecutor(max(1, (os.cpu_count() or 4) // 2)) as ex:
            list(ex.map(one, srcs))

    @app.on_event("startup")
    def _startup():
        if not lib.games:
            if not _setup_done() and not Path(cfg.roms).is_dir():
                return  # primo avvio: prima la configurazione guidata
            threading.Thread(target=_rebuild, daemon=True).start()
        else:
            threading.Thread(target=_pregen_thumbs, daemon=True).start()

    # ------------------------------------------------------------ temi, plugin, link esterni
    @app.get("/api/extensions")
    def api_extensions():
        return {"themes": X.list_themes(cfg), "active_theme": cfg.app.get("theme", "default"),
                "plugins": X.list_plugins(cfg, plugin_errors),
                "paths": {"themes": str(X.user_dir(cfg, "themes")), "plugins": str(X.user_dir(cfg, "plugins"))}}

    @app.post("/api/app/duplicates")
    def api_duplicates(body: dict = Body(...)):
        C.save_settings(cfg, app={"skip_duplicates": bool(body.get("skip"))})
        return {"skip_duplicates": cfg.app.get("skip_duplicates", True)}

    @app.post("/api/app/theme")
    def api_theme(body: dict = Body(...)):
        tid = body.get("theme") or "default"
        if tid not in {t["id"] for t in X.list_themes(cfg)}:
            raise HTTPException(404, "Tema non trovato")
        C.save_settings(cfg, app={"theme": tid})
        return {"ok": True}

    @app.post("/api/app/plugin")
    def api_plugin_toggle(body: dict = Body(...)):
        disabled = set(cfg.app.get("disabled_plugins", []))
        (disabled.discard if body.get("enabled") else disabled.add)(body.get("id"))
        C.save_settings(cfg, app={"disabled_plugins": sorted(disabled)})
        return {"ok": True}

    @app.get("/themes/{tid}/{rel:path}")
    def theme_static(tid: str, rel: str):
        f = X.theme_file(cfg, tid, rel)
        if not f:
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "no-cache"})

    @app.get("/plugins/{pid}/{rel:path}")
    def plugin_static(pid: str, rel: str):
        f = X.plugin_file(cfg, pid, rel)
        if not f:
            raise HTTPException(404)
        media = "text/javascript" if f.suffix == ".js" else None
        return FileResponse(f, media_type=media, headers={"Cache-Control": "no-cache"})

    @app.post("/api/open")
    def api_open(body: dict = Body(...)):
        """Apre un link web nel browser predefinito (solo http/https)."""
        import webbrowser
        url_ = str(body.get("url") or "")
        if not url_.startswith(("https://", "http://")):
            raise HTTPException(400, "Sono ammessi solo link web")
        webbrowser.open(url_)
        return {"ok": True}

    # ------------------------------------------------------------ icone di console e store
    from .icons import CREDIT, Icons
    icons = Icons(cfg.cache)
    ICON_TYPES = {".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png", ".jpg": "image/jpeg",
                  ".webp": "image/webp", ".gif": "image/gif"}

    def _icon_response(p):
        if not p:
            raise HTTPException(404)
        return FileResponse(p, media_type=ICON_TYPES.get(p.suffix, "image/png"),
                            headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/icons/platform/{pid}")
    def icon_platform(pid: str):
        return _icon_response(icons.platform(pid))

    @app.get("/icons/store/{sid}")
    def icon_store(sid: str):
        return _icon_response(icons.store(sid))

    @app.get("/icons/site")
    def icon_site(url: str):
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400)
        return _icon_response(icons.site(url, badge=("🌐", "#2d5a8c")))

    # ------------------------------------------------------------ HowLongToBeat
    @app.post("/api/hltb")
    def api_hltb_start(body: dict = Body(default={})):
        if job["hltb"].status == "running":
            raise HTTPException(409, "Ricerca delle durate gia' in corso")
        j = J.HltbJob(only_missing=body.get("mode", "missing") != "all")
        job["hltb"] = j

        def after(stats):
            lib.load()
            events.emit("library_built", stats)

        threading.Thread(target=j.run, args=(cfg, after), daemon=True).start()
        return j.state()

    @app.get("/api/hltb")
    def api_hltb_state():
        from ..cache import Cache
        st = job["hltb"].state()
        c = Cache(cfg.cache)
        try:
            st["known"] = c.db.execute("SELECT COUNT(*) FROM kv WHERE k LIKE 'hltb:%'").fetchone()[0]
        finally:
            c.close()
        st["with_hltb"] = sum(1 for g in lib.games if g.get("hltb") and g.get("is_primary"))
        return st

    @app.post("/api/hltb/cancel")
    def api_hltb_cancel():
        job["hltb"].stop = True
        return job["hltb"].state()

    # ------------------------------------------------------------ immagini delle collezioni
    # icona: logo da Wikipedia, altrimenti il logo (wheel) del gioco piu' votato della collezione;
    # copertina: grafica (hero/fanart/screenshot) del gioco piu' votato, altrimenti l'immagine di Wikipedia
    art_state: dict = {"key": None, "map": {}, "rev": 0}
    ART_COVER = ("hero", "fanart", "screenshot", "header", "title")
    ART_ICON = ("wheel", "logo", "marquee")

    def _art_map() -> dict:
        key = (lib.built_at, art_state["rev"], len(user.edits()), len(user.collections()))  # anche collezioni modificate
        if art_state["key"] == key:
            return art_state["map"]
        from ..cache import Cache
        v = view()
        c = Cache(cfg.cache)
        try:
            wiki = {k[8:]: json.loads(val) for k, val in c.db.execute("SELECT k, v FROM kv WHERE k LIKE 'collart:%'")}
        finally:
            c.close()
        out = {}
        for slug, col in v["colls"].items():
            games = []
            for grp in col["groups"]:
                d = v["groups"].get(grp)
                g = lib.by_id.get(d["primary"]) if d else None
                if g and g.get("store") != "online":
                    games.append(g)
            games.sort(key=lambda g: (-(g.get("rating") or 0) * min(g.get("rating_count") or 1, 200), g["title"]))
            w = wiki.get(slug) or {}
            ent = {"page": w.get("page")}
            icon_g = next((g for g in games for k in ART_ICON if k in g["media"]), None)
            if w.get("icon"):
                ent["icon"] = ("file", w["icon"])
            elif icon_g:
                ent["icon"] = ("game", icon_g["id"], next(k for k in ART_ICON if k in icon_g["media"]))
            cov_g = next((g for g in games for k in ART_COVER if k in g["media"]), None)
            if cov_g:
                ent["cover"] = ("game", cov_g["id"], next(k for k in ART_COVER if k in cov_g["media"]))
            elif w.get("cover"):
                ent["cover"] = ("file", w["cover"])
            elif games and "cover" in games[0]["media"]:
                ent["cover"] = ("game", games[0]["id"], "cover")
            ent["poster"] = next((g["id"] for g in games if "cover" in g["media"]), None)
            out[slug] = ent
        art_state.update(key=key, map=out)
        return out

    @app.get("/coll-img/{slug}/{kind}")
    def coll_img(slug: str, kind: str, request: Request, w: int = 0):
        from fastapi.responses import RedirectResponse
        ent = _art_map().get(slug)
        src = (ent or {}).get(kind)
        if not src:
            raise HTTPException(404)
        if src[0] == "game":
            return RedirectResponse(f"/img/{src[1]}/{src[2]}" + (f"?w={w}" if w else ""), status_code=307)
        p = Path(cfg.cache) / "collart" / src[1]
        if not p.exists():
            raise HTTPException(404)
        if w and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            try:
                p = _thumb(p, min(max(w, 32), 1600))
            except Exception:
                pass
        return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/collart")
    def api_collart_state():
        st = job["collart"].state()
        m = _art_map()
        st["with_icon"] = sum(1 for e in m.values() if e.get("icon"))
        st["with_wiki"] = sum(1 for e in m.values() if e.get("icon", ("",))[0] == "file")
        st["total"] = len(m)
        return st

    @app.post("/api/collart")
    def api_collart_start(body: dict = Body(default={})):
        return _start_collart(body.get("mode", "missing") != "all")

    def _start_collart(only_missing=True):
        if job["collart"].status == "running":
            raise HTTPException(409, "Ricerca delle immagini delle collezioni gia' in corso")
        v = view()
        cols = [(slug, c["name"]) for slug, c in v["colls"].items() if c["groups"]]
        j = J.CollArtJob(collections=cols, only_missing=only_missing)
        job["collart"] = j

        def after(_stats):
            art_state["rev"] += 1
            events.emit("collart_done", _stats)

        threading.Thread(target=j.run, args=(cfg, after), daemon=True).start()
        return j.state()

    @app.post("/api/collart/cancel")
    def api_collart_cancel():
        job["collart"].stop = True
        return job["collart"].state()

    def _collart_auto():
        time.sleep(45)  # dopo l'avvio: scarica in sottofondo le immagini mancanti delle collezioni
        try:
            if lib.games and job["collart"].status != "running":
                _start_collart(True)
        except Exception as e:
            print(f"Immagini collezioni: {e}")

    threading.Thread(target=_collart_auto, daemon=True).start()

    # ------------------------------------------------------------ collezioni intelligenti (regole salvate)
    @app.post("/api/smart")
    def api_smart_save(body: dict = Body(...)):
        items = body.get("items")
        if not isinstance(items, list):
            raise HTTPException(400, "elenco non valido")
        clean = []
        for it in items[:200]:
            if not isinstance(it, dict) or not str(it.get("name") or "").strip():
                continue
            clean.append({"id": str(it.get("id") or "")[:40] or f"s{int(time.time() * 1000)}",
                          "name": str(it["name"]).strip()[:80], "icon": str(it.get("icon") or "⚡")[:4],
                          "rules": it.get("rules") if isinstance(it.get("rules"), dict) else {}})
        C.save_settings(cfg, app={"smart_collections": clean})
        cfg.app["smart_collections"] = clean
        return {"items": clean}

    # ------------------------------------------------------------ lista dei desideri
    from .. import wishlist as W
    wish_state = {"checking": False, "last": 0.0, "log": []}

    def _wish_payload():
        s = W.settings(cfg)
        items = user.wishlist()
        owned = {normalize(g["title"]) for g in lib.games if g.get("store") != "online"
                 and (g.get("store") is None or g.get("installed") or g.get("store"))}
        for w in items:
            w["owned"] = normalize(w["title"]) in owned
        return {"items": items, "settings": {"country": s["country"], "threshold": s["threshold"],
                                             "has_key": bool(s["itad_key"])},
                "unseen": sum(1 for w in items if w["alert"] and not w["seen"]), "checking": wish_state["checking"]}

    def _wish_check(force=False):
        if wish_state["checking"]:
            return
        wish_state["checking"] = True
        try:
            r = W.check(cfg, user, log=lambda m: wish_state["log"].append(m), force=force)
            wish_state["last"] = time.time()
            if r["alerts"]:
                events.emit("wishlist_alerts", r)
        except Exception as e:
            wish_state["log"].append(f"{type(e).__name__}: {e}")
        finally:
            wish_state["checking"] = False

    @app.get("/api/wishlist")
    def api_wishlist():
        return _wish_payload()

    @app.post("/api/wishlist/search")
    def api_wishlist_search(body: dict = Body(...)):
        q = (body.get("q") or "").strip()
        if len(q) < 2:
            return {"results": []}
        try:
            return {"results": W.search(cfg, q)}
        except Exception as e:
            raise HTTPException(502, f"Ricerca non riuscita: {e}")

    @app.post("/api/wishlist/add")
    def api_wishlist_add(body: dict = Body(...)):
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(400, "Serve un titolo")
        wid = body.get("id") or ("steam:" + body["steam_appid"] if body.get("steam_appid") else "t:" + normalize(title))
        target = body.get("target")
        user.wish_add(wid, title, str(body.get("steam_appid") or ""), str(body.get("itad_id") or ""),
                      float(target) if target not in (None, "") else None)
        threading.Thread(target=_wish_check, daemon=True).start()
        return _wish_payload()

    @app.post("/api/wishlist/import-steam")
    def api_wishlist_import():
        prov = next((p for p in STORES.PROVIDERS if p.id == "steam"), None)
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            sid = prov(cfg, c, None, lambda *a: None).steamid() if prov else ""
        finally:
            c.close()
        if not sid:
            raise HTTPException(400, "Account Steam non trovato: collegalo in Impostazioni › Account")
        try:
            items = W.steam_wishlist(sid)
        except Exception as e:
            raise HTTPException(502, str(e))
        for it in items:
            user.wish_add("steam:" + it["steam_appid"], it["title"], it["steam_appid"])
        threading.Thread(target=_wish_check, daemon=True).start()
        return {**_wish_payload(), "imported": len(items)}

    @app.post("/api/wishlist/check")
    def api_wishlist_check(body: dict = Body(default={})):
        threading.Thread(target=_wish_check, kwargs={"force": bool(body.get("force", True))}, daemon=True).start()
        return {"ok": True}

    @app.post("/api/wishlist/settings")
    def api_wishlist_settings(body: dict = Body(...)):
        cur = dict(cfg.app.get("wishlist") or {})
        if "itad_key" in body:
            cur["itad_key"] = (body.get("itad_key") or "").strip() or None
        if body.get("country"):
            cur["country"] = str(body["country"]).strip().upper()[:2]
        if body.get("threshold") not in (None, ""):
            cur["threshold"] = max(5, min(95, int(body["threshold"])))
        C.save_settings(cfg, app={"wishlist": cur})
        cfg.app["wishlist"] = cur
        return _wish_payload()

    @app.post("/api/wishlist/seen")
    def api_wishlist_seen():
        for w in user.wishlist():
            if not w["seen"]:
                user.wish_update(w["id"], seen=1)
        return _wish_payload()

    @app.post("/api/wishlist/item/{wid:path}")
    def api_wishlist_edit(wid: str, body: dict = Body(...)):
        t = body.get("target")
        user.wish_update(wid, target=float(t) if t not in (None, "") else None, alert="", seen=1)
        threading.Thread(target=_wish_check, kwargs={"force": True}, daemon=True).start()
        return _wish_payload()

    @app.delete("/api/wishlist/item/{wid:path}")
    def api_wishlist_delete(wid: str):
        user.wish_delete(wid)
        return _wish_payload()

    def _wish_loop():
        time.sleep(30)
        while True:
            if user.wishlist() and time.time() - wish_state["last"] > 12 * 3600:
                _wish_check()
            time.sleep(1800)

    threading.Thread(target=_wish_loop, daemon=True).start()

    # ------------------------------------------------------------ giochi gratis
    from .. import freegames as FG
    REMOTE_HOSTS = ("cdn.allkeyshop.com", "www.gamerpower.com", "gamerpower.com", "cdn1.epicgames.com",
                    "shared.akamai.steamstatic.com", "cdn.akamai.steamstatic.com", "assets.isthereanydeal.com")

    @app.get("/api/free")
    def api_free(force: int = 0):
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            d = FG.get(c, force=bool(force))
        finally:
            c.close()
        owned = {normalize(g["title"]) for g in lib.games if g.get("store") != "online"}
        wished = {normalize(w["title"]) for w in user.wishlist()}
        items = [{**i, "owned": normalize(i["title"]) in owned, "wished": normalize(i["title"]) in wished}
                 for i in d.get("items", [])]
        return {"items": items, "time": d.get("time"), "errors": d.get("errors", []), "stale": d.get("stale", False)}

    @app.get("/remote-img")
    def remote_img(u: str):
        from urllib.parse import urlparse
        if urlparse(u).hostname not in REMOTE_HOSTS:
            raise HTTPException(400)
        rel = _fetch_remote(u)
        if not rel:
            raise HTTPException(404)
        return FileResponse(Path(cfg.cache) / rel, headers={"Cache-Control": "public, max-age=604800"})

    # ------------------------------------------------------------ statistiche
    @app.get("/api/stats")
    def api_stats(year: int = 0):
        import datetime as _dt
        v = view()
        by_key = lib.by_key
        y = year or _dt.date.today().year
        start = _dt.datetime(y, 1, 1).timestamp()
        end = _dt.datetime(y + 1, 1, 1).timestamp()
        per_game: dict = defaultdict(float)
        months = [0.0] * 12
        years: dict = defaultdict(float)
        for key, st, sec in user.sessions():
            g = by_key.get(key)
            years[_dt.datetime.fromtimestamp(st).year] += sec
            if g and start <= st < end:
                per_game[g["grp"]] += sec
                months[_dt.datetime.fromtimestamp(st).month - 1] += sec
        top_year = []
        for grp, sec in sorted(per_game.items(), key=lambda x: -x[1])[:15]:
            d = v["groups"].get(grp) or {}
            g = lib.get(d.get("primary")) if d.get("primary") else None
            if g:
                top_year.append({"id": g["id"], "title": g["title"], "platform": g.get("store") or g["platform"], "seconds": sec})
        return {"year": y, "months": months, "years": dict(sorted(years.items())), "top_year": top_year}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/bigpicture")
    def bigpicture():
        return FileResponse(STATIC / "bigpicture.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _plain(s: str) -> str:
    import re
    return re.sub(r"\[/?[a-z ]*\]", "", s)


def run(cfg, port: int = 8765, browser: bool = False, bigpicture: bool = False):
    import uvicorn
    app = create_app(cfg)
    url = f"http://127.0.0.1:{port}/"
    start_url = url + ("bigpicture" if bigpicture else "")
    if browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(start_url)).start()
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
        return
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    try:
        import webview

        class Api:
            """Esposto al JavaScript come window.pywebview.api: finestre native di scelta file/cartella."""

            def pick_file(self):
                win = webview.windows[0]
                kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
                types = ("Programmi (*.exe)", "Tutti i file (*.*)") if sys.platform == "win32" else ("Tutti i file (*)",)
                r = win.create_file_dialog(kind, file_types=types)
                return r[0] if r else None

            def login(self, store_id):
                """Apre la pagina ufficiale di login (Epic/GOG) e cattura il codice di autorizzazione."""
                import json as _json
                import urllib.request
                prov = STORES.BY_ID.get(store_id)
                if not prov or not getattr(prov, "login_url", None):
                    return {"ok": False, "error": "login non previsto"}
                win = webview.create_window(f"Accedi a {prov.name}", prov.login_url, width=520, height=760)
                done = {"code": None}

                def watch():
                    for _ in range(600):  # 5 minuti
                        time.sleep(0.5)
                        try:
                            u = win.get_current_url() or ""
                        except Exception:
                            return
                        if store_id == "gog" and "on_login_success" in u and "code=" in u:
                            done["code"] = u
                        elif store_id == "epic" and "/id/api/redirect" in u:
                            try:
                                done["code"] = win.evaluate_js("document.body.innerText")
                            except Exception:
                                pass
                        if done["code"]:
                            win.destroy()
                            return
                watch()
                if not done["code"]:
                    return {"ok": False, "error": "accesso annullato"}
                req = urllib.request.Request(f"{url}api/accounts/{store_id}/code",
                                             data=_json.dumps({"code": done["code"]}).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=60) as r:
                        return {"ok": True, **_json.loads(r.read())}
                except Exception as e:
                    return {"ok": False, "error": str(getattr(e, "read", lambda: b"")().decode() or e)}

            def focus_window(self):
                """Riporta in primo piano la finestra dell'app quando il gioco si chiude."""
                win = webview.windows[0]
                try:
                    win.restore()
                    win.show()
                    if sys.platform == "win32":
                        import ctypes
                        hwnd = win.native.Handle.ToInt64()
                        ctypes.windll.user32.ShowWindow(hwnd, 9)
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                return True

            def set_fullscreen(self, on):
                """Schermo intero (Big Picture)."""
                win = webview.windows[0]
                if bool(on) != state_fs["on"]:
                    win.toggle_fullscreen()
                    state_fs["on"] = bool(on)
                return state_fs["on"]

            def pick_folder(self):
                win = webview.windows[0]
                kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
                r = win.create_file_dialog(kind)
                return r[0] if r else None

        state_fs = {"on": bigpicture}
        webview.create_window("GameScraper", start_url, width=1500, height=940, min_size=(960, 600),
                              background_color="#111318", js_api=Api(), fullscreen=bigpicture)
        webview.start()
    except Exception as e:  # pywebview non disponibile: si usa il browser
        print(f"Finestra desktop non disponibile ({e}), apro il browser: {url}")
        import webbrowser
        webbrowser.open(start_url)
        t.join()
    finally:
        server.should_exit = True
