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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import config as C
from .. import platforms as P
from .. import backup as BK
from .. import ports as PT
from .. import relink as RL
from .. import storerefresh as SR
from ..library import launcher as L
from . import extensions as X
from .icons import CREDIT
from . import jobs as J
from .. import stores as STORES

STORE_NAMES = {p.id: p.name for p in STORES.PROVIDERS}
STORE_NAMES["online"] = "Store online"
_REMOTE_SEM = threading.Semaphore(4)  # immagini remote scaricate al massimo 4 alla volta
_REMOTE_DOWN: dict[str, float] = {}  # host che non risponde -> fino a quando non si riprova (secondi)
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
        self.full = threading.Event()  # impostato quando ci sono anche i giochi del catalogo online

    def load(self, online: bool = True):
        """online=False: solo i giochi posseduti (avvio rapido); il catalogo online si carica dopo."""
        if not self.path.exists():
            self.full.set()
            return
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        games = [dict(r) for r in db.execute("SELECT * FROM games" + ("" if online else " WHERE store IS NOT 'online'"))]
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
        if online:
            self.full.set()

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
    app.add_middleware(GZipMiddleware, minimum_size=2048, compresslevel=1)  # in locale conta la velocita', non il peso
    lib = Library(cfg)
    lib.load(online=False)
    threading.Thread(target=lib.load, daemon=True).start()
    Path(cfg.cache).mkdir(parents=True, exist_ok=True)  # prima installazione: la cache non esiste ancora
    user = UserData(Path(cfg.cache) / "userdata.db")
    events = X.Events()
    from ..library import screens as SC
    shots = SC.Shots(cfg.cache, Path(cfg.cache) / "userdata.db")
    launcher = L.Launcher(cfg, user, on_event=events.emit, shots=shots, profile_of=user.profile)
    from ..remote import phone as PH
    phone_hub = PH.PhoneHub(cfg, C.save_settings)
    PH.register(app, phone_hub, STATIC)
    app.state.phone_hub = phone_hub  # per i test (vibrazioni verso il telefono)
    plugin_errors = X.load_backends(app, cfg, events)
    thumbs_dir = Path(cfg.cache) / "thumbs"
    state = {"building": False, "build_log": [], "build_stats": None, "thumbs_total": 0, "thumbs_done": 0}
    job: dict = {"current": J.ScrapeJob(), "stores": J.StoreJob(), "storemeta": J.StoreMetaJob(),
                 "hltb": J.HltbJob(), "collart": J.CollArtJob(), "netmeta": J.NetMetaJob(),
                 "pcstores": J.PcStoreJob(), "top": J.TopGamesJob(), "store_refresh": J.StoreRefreshJob()}
    start_lock = threading.Lock()

    def _claim(name: str, j) -> bool:
        """Mette j al posto del job `name` se quello non gira; False se gira gia'. Controllo e cambio sono
        sotto lock e j risulta subito "running": due clic rapidi (o PC e telefono) non avviano due job."""
        with start_lock:
            if job[name].status == "running":
                return False
            j.status = "running"
            job[name] = j
            return True

    # ------------------------------------------------------------ vista unita (gruppi + utente)
    def view(only=None) -> dict:
        """Calcola gruppi, versione principale, dati utente e collezioni (modifiche incluse).
        only: i soli gruppi di cui servono i dati (le collezioni restano complete)."""
        with lib.lock:
            ug = user.games()
            uc = user.collections()
            edits = user.edits()
            groups = lib.groups if only is None else {grp: lib.groups[grp] for grp in only}
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
                    "note": next((u.get("note") for u in us if u.get("note")), "") or "",
                    "my_rating": max((u.get("my_rating") or 0 for u in us), default=0),
                }
            return {"colls": colls, "groups": gdata}

    # ------------------------------------------------------------ API
    @app.get("/api/library")
    def api_library(part: str = ""):
        """part='owned': solo i giochi posseduti (subito); 'online': solo il catalogo (dopo); vuoto: tutto."""
        if part != "owned":
            lib.full.wait()
        with lib.lock:
            sel = lib.games if not part else [g for g in lib.games if (g.get("store") == "online") == (part == "online")]
            grp_list = list(dict.fromkeys(g["grp"] for g in sel))
            v = view(grp_list)
            coll_list = sorted((c for c in v["colls"].values()), key=lambda c: c["name"].lower())
            coll_idx = {c["slug"]: i for i, c in enumerate(coll_list)}
            grp_colls: dict[str, list[int]] = defaultdict(list)
            for c in coll_list:
                for grp in c["groups"]:
                    grp_colls[grp].append(coll_idx[c["slug"]])
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
                    for g in sel]
            plats = defaultdict(int)
            for grp, members in lib.groups.items():
                if members[0].get("store") != "online":  # i giochi solo online non contano come posseduti
                    plats[members[0]["platform"]] += 1
            # JSONResponse diretto: jsonable_encoder di FastAPI su ~150.000 righe costa secondi
            return JSONResponse({
                "built_at": lib.built_at, "user_rev": user.rev,
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
            })

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
        v = view([g["grp"]])
        grp = v["groups"][g["grp"]]
        p = P.PLATFORMS.get(g["platform"])
        try:
            plan = L.plan(cfg, p, user.profile(g["key"])) if p else None
        except L.LaunchError:
            plan = None
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
                "profile": user.profile(g["key"]),
                "shots": len(shots.list([m["key"] for m in lib.groups[g["grp"]]])),
                "hltb": json.loads(g["hltb"]) if g.get("hltb") else None,
                "size": g.get("size"), "magnet": (g.get("launch") or {}).get("magnet") if g.get("store") == "online" else None,
                "download_sources": ((g.get("launch") or {}).get("sources") or []) if g.get("store") == "online" else [],
                "kind": (g.get("launch") or {}).get("kind") if g.get("store") == "online" else None,
                "file_name": g.get("canonical") if g.get("store") == "online" else None,
                "page_url": g.get("page") if g.get("store") == "online" else None,
                # pagine degli store online dove il gioco si puo' scaricare (anche se e' gia' in libreria)
                "store_pages": json.loads(g.get("store_pages") or "[]"),
                "ratings": json.loads(g.get("ratings") or "[]"),
                "store": g.get("store"), "store_name": STORE_NAMES.get(g.get("store"), ""),
                "installed": bool(g.get("installed")) if g.get("store") else True,
                "can_install": bool(g.get("install_uri")), "page": g.get("page"),
                "store_playtime": g.get("store_playtime") or 0,
                "ports": [] if g.get("store") else _port_links()["by_grp"].get(g["grp"], []),
                "emulator": (STORE_NAMES.get(g.get("store"), "PC") if g.get("store")
                             else (plan["emulator"] + (f" ({plan['core']})" if plan["core"] else "")) if plan else None)}

    def _member_keys(gid: int) -> list[str]:
        g = lib.get(gid)
        return [m["key"] for m in lib.groups[g["grp"]]]

    @app.post("/api/game/{gid}/flag")
    def api_flag(gid: int, body: dict = Body(...)):
        field, value = body.get("field"), body.get("value")
        if field not in ("favorite", "hidden", "status", "last_played", "note", "my_rating"):
            raise HTTPException(400, "campo non valido")
        if field == "note":
            value = str(value or "").strip()[:4000] or None
        elif field == "my_rating":
            if value not in (None, "", 0, 1, 2, 3, 4, 5):
                raise HTTPException(400, "voto non valido (1-5)")
            value = int(value) if value else None
        elif field == "last_played":  # "rimuovi da Giocati di recente": si azzera solo l'ultima partita, tempo e avvii restano
            value = None
        elif field == "status":
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

    # ------------------------------------------------------------ profilo di avvio del gioco
    @app.get("/api/game/{gid}/profile")
    def api_profile(gid: int):
        g = lib.get(gid)
        p = P.PLATFORMS.get(g["platform"])
        if not p or g.get("store"):
            raise HTTPException(400, "I profili di avvio valgono per i giochi delle console")
        base = L.plan(cfg, p)
        try:
            cur = L.plan(cfg, p, user.profile(g["key"]))
        except L.LaunchError as e:
            cur = {"error": str(e)}
        emus = [{"id": e, "configured": bool(cfg.emulators.get(e) and Path(cfg.emulators[e]).exists()),
                 "preset": any(x == e for x, _ in L.PRESETS.get(p.id, [])), "default_core": L.default_core(p.id, e),
                 "exe": cfg.emulators.get(e) or "",
                 "args": ["-L", "{core}", "{rom}"] if e == L.RA else L.STANDALONE_ARGS.get(e, ["{rom}"])}
                for e in L.EMULATOR_NAMES]
        cores = []
        if cfg.emulators.get(L.RA):
            cdir = L._core_path(cfg, "x").parent
            if cdir.is_dir():
                cores = sorted(f.name.split("_libretro")[0] for f in cdir.iterdir() if "_libretro" in f.name)
        return {"profile": user.profile(g["key"]) or {}, "default": base, "current": cur, "emulators": emus,
                "cores": cores, "platform": p.name}

    @app.post("/api/game/{gid}/profile")
    def api_profile_save(gid: int, body: dict = Body(...)):
        g = lib.get(gid)
        data = {k: str(body.get(k) or "").strip() for k in ("emulator", "core", "exe", "args", "extra")}
        data = {k: v for k, v in data.items() if v}
        if data.get("emulator") and data["emulator"] not in L.EMULATOR_NAMES:
            raise HTTPException(400, "Emulatore sconosciuto")
        if data.get("exe") and not Path(data["exe"].strip('"')).is_file():
            raise HTTPException(400, "Eseguibile non trovato")
        if data.get("exe"):
            data["exe"] = data["exe"].strip('"')
        user.set_profile(_member_keys(gid), data or None)
        return api_profile(gid)

    @app.delete("/api/game/{gid}/profile")
    def api_profile_delete(gid: int):
        user.set_profile(_member_keys(gid), None)
        return api_profile(gid)

    # ------------------------------------------------------------ schermate
    def _shot_payload(s: dict) -> dict:
        g = lib.by_key.get(s["key"])
        return {**s, "url": f"/shots/{s['id']}", "game": g["id"] if g else None, "title": g["title"] if g else s["key"],
                "platform": (g.get("store") or g["platform"]) if g else ""}

    @app.get("/api/game/{gid}/shots")
    def api_game_shots(gid: int):
        g = lib.get(gid)
        return {"shots": [_shot_payload(s) for s in shots.list([m["key"] for m in lib.groups[g["grp"]]])]}

    @app.get("/api/shots")
    def api_shots():
        out = []
        for s in shots.list():
            g = lib.by_key.get(s["key"])
            if g:  # la versione principale del gioco, per raggruppare le schermate di tutte le versioni
                prim = next((m for m in lib.groups[g["grp"]] if m.get("is_primary")), g)
                out.append({**_shot_payload(s), "game": prim["id"], "title": prim["title"],
                            "platform": prim.get("store") or prim["platform"]})
            else:
                out.append(_shot_payload(s))
        return {"shots": out, "folder": str(shots.root), "settings": SC.settings(cfg)}

    @app.get("/shots/{sid}")
    def shot_file(sid: int, w: int = 0):
        p = shots.path(sid)
        if not p or not p.exists():
            raise HTTPException(404)
        if w:
            try:
                p = _thumb(p, min(max(w, 64), 1600))
            except Exception:
                pass
        return FileResponse(p, headers={"Cache-Control": "public, max-age=604800"})

    @app.delete("/api/shots/{sid}")
    def api_shot_delete(sid: int):
        shots.delete(sid)
        return {"ok": True}

    @app.post("/api/shots/open")
    def api_shot_open(body: dict = Body(default={})):
        p = shots.path(int(body["id"])) if body.get("id") else shots.root
        if not p or not p.exists():
            shots.root.mkdir(parents=True, exist_ok=True)
            p = shots.root
        if sys.platform == "win32" and p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            L.open_uri(str(p if p.is_dir() else p.parent))
        return {"ok": True}

    @app.post("/api/shots/import-steam")
    def api_shots_steam():
        prov = STORES.BY_ID.get("steam")
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            root = prov(cfg, c, None, lambda *a: None).path() if prov else None
        finally:
            c.close()
        if not root:
            raise HTTPException(400, "Steam non trovato su questo computer")
        keys = {g["key"].split(":", 1)[1]: g["key"] for g in lib.games if g.get("store") == "steam"}
        n = SC.import_steam(shots, Path(root), keys)
        return {"imported": n}

    @app.post("/api/shots/settings")
    def api_shots_settings(body: dict = Body(...)):
        cur = SC.settings(cfg)
        if "hotkey" in body:
            hk = str(body.get("hotkey") or "").strip().lower()
            if hk and not SC.parse_hotkey(hk):
                raise HTTPException(400, "Scorciatoia non valida (es. ctrl+shift+s, f10, alt+f12)")
            cur["hotkey"] = hk
        for k in ("pad", "import_emulator"):
            if k in body:
                cur[k] = bool(body[k])
        C.save_settings(cfg, app={"screenshots": cur})
        cfg.app["screenshots"] = cur
        return {"settings": cur}

    @app.post("/api/running/screenshot")
    def api_running_shot():
        return {"ok": launcher.screenshot()}

    @app.post("/api/play-key")
    def api_play_key(body: dict = Body(...)):
        g = lib.by_key.get(str(body.get("key") or ""))
        if not g:
            raise HTTPException(404, "Gioco non trovato in libreria")
        api_play(g["id"])
        return {"ok": True, "title": g["title"], "id": g["id"]}

    # ------------------------------------------------------------ streaming con Sunshine / Moonlight
    from ..remote import sunshine as SUN

    def _sun_conf() -> dict:
        return dict(cfg.app.get("sunshine") or {})

    def _stream_games(limit: int) -> list[dict]:
        v = view()
        out, seen = [], set()

        def add(g):
            if g and g["grp"] not in seen and (g.get("installed") if g.get("store") else True) and g.get("store") != "online":
                seen.add(g["grp"])
                out.append({"key": g["key"], "title": g["title"], "id": g["id"]})
        prim = [lib.by_id.get(d["primary"]) for d in v["groups"].values()]
        gd = {g["grp"]: v["groups"][g["grp"]] for g in prim if g}
        for g in sorted((g for g in prim if g and gd[g["grp"]].get("status") == "playing"), key=lambda g: -gd[g["grp"]]["last_played"]):
            add(g)
        for g in sorted((g for g in prim if g and gd[g["grp"]]["favorite"]), key=lambda g: g["sort_title"] or ""):
            add(g)
        for g in sorted((g for g in prim if g and gd[g["grp"]]["last_played"]), key=lambda g: -gd[g["grp"]]["last_played"]):
            add(g)
        return out[:limit]

    @app.get("/api/sunshine")
    def api_sunshine():
        d = SUN.install_dir()
        conf = _sun_conf()
        st = {"installed": bool(d), "service": SUN.service_state(), "start_mode": SUN.start_mode() if d else "",
              "user": conf.get("user", ""),
              "has_password": bool(conf.get("password")), "count": conf.get("count", 24),
              "last": conf.get("last"), "web": SUN.API, "apps": None, "error": ""}
        if d and conf.get("user") and conf.get("password") and st["service"] != "stopped":
            try:
                s = SUN.Sunshine(conf["user"], conf["password"])
                try:
                    st["apps"] = [{"name": a.get("name"), "ours": SUN.ours(a)} for a in s.apps()]
                finally:
                    s.close()
            except Exception as e:
                st["error"] = str(e) if isinstance(e, PermissionError) else f"Sunshine non risponde ({type(e).__name__})"
        st["preview"] = [g["title"] for g in _stream_games(int(st["count"]))]
        return st

    @app.post("/api/sunshine")
    def api_sunshine_publish(body: dict = Body(...)):
        conf = _sun_conf()
        if body.get("user"):
            conf["user"] = str(body["user"]).strip()
        if body.get("password"):
            conf["password"] = str(body["password"])
        if body.get("count") not in (None, ""):
            conf["count"] = max(0, min(100, int(body["count"])))
        C.save_settings(cfg, app={"sunshine": conf})
        cfg.app["sunshine"] = conf
        if not body.get("publish"):
            return api_sunshine()
        if not (conf.get("user") and conf.get("password")):
            raise HTTPException(400, "Servono utente e password dell'interfaccia web di Sunshine")

        def cover_of(g):
            m = lib.by_id[g["id"]]["media"]
            rel = m.get("cover") or m.get("header")
            return Path(cfg.cache) / rel if rel and not rel.startswith("url:") else None
        try:
            r = SUN.publish(cfg, conf["user"], conf["password"], _stream_games(int(conf.get("count", 24))),
                            C.settings_path(cfg).parent / "sunshine", cover_of)
        except PermissionError as e:
            raise HTTPException(401, str(e))
        except Exception as e:
            raise HTTPException(502, f"Sunshine non risponde: avvialo e riprova ({type(e).__name__}: {e})")
        conf["last"] = {"time": time.time(), **r}
        C.save_settings(cfg, app={"sunshine": conf})
        return api_sunshine()

    @app.get("/api/stream/optimize")
    def api_stream_optimize():
        """Controlli sul PC per abbassare la latenza dello streaming (Sunshine, encoder, rete)."""
        try:
            return {"checks": SUN.optimize_checks()}
        except Exception as e:
            return {"checks": [{"level": "warn", "title": "Controllo non riuscito", "detail": f"{type(e).__name__}: {e}"}]}

    @app.post("/api/sunshine/start")
    def api_sunshine_start():
        if not SUN.start_service():
            raise HTTPException(409, "Avvio annullato o non possibile")
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
        j = J.StoreJob(only=only)
        if not _claim("stores", j):
            raise HTTPException(409, "Aggiornamento degli store gia' in corso")

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
                if P_.id in ("manual", "ports"):  # giochi aggiunti a mano e port PC non sono uno store da importare
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

    @app.post("/api/roms/extra")
    def api_roms_extra(body: dict = Body(...)):
        """Aggiunge (action=add) o toglie (action=remove) una cartella ROM aggiuntiva (es. un altro disco)."""
        raw = str(body.get("path") or "").strip().strip('"')
        p = Path(os.path.expandvars(os.path.expanduser(raw)))
        cur = [str(x) for x in cfg.extra_roms]
        if body.get("action") == "remove":
            cur = [x for x in cur if x != raw]
        else:
            if not raw or not p.is_dir():
                raise HTTPException(400, "Cartella non trovata")
            p = p.resolve()
            if p == Path(cfg.roms).resolve() or str(p) in cur:
                raise HTTPException(400, "Cartella gia' presente")
            cur.append(str(p))
        C.save_settings(cfg, paths={"extra_roms": cur})
        return {"extra": [{"path": str(r), "online": r.exists()} for r in C.rom_roots(cfg)[1:]]}

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
        if job["stores"].status == "running":
            raise HTTPException(409, "Attendi la fine dell'aggiornamento degli store")
        j = J.StoreMetaJob(only_missing=bool(body.get("missing")))
        if not _claim("storemeta", j):
            raise HTTPException(409, "Ricerca dei metadati degli store gia' in corso")

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

    @app.get("/api/catalogs/{pid}")
    def api_catalog_get(pid: str):
        from ..cache import Cache
        from ..catalog import cached_info
        from ..storemeta import coverage
        c = Cache(cfg.cache)
        try:
            pages = []
            for u in cfg.catalogs.get(pid, []):
                d = cached_info(c, pid, u)
                pages.append({"url": u, "games": len(d["entries"]), "time": d["time"],
                              "type": d["type"] or (cfg.catalog_options.get(pid) or {}).get("type", "filelist"),
                              "total_pages": d["total_pages"]})
            cov = coverage(c)
        finally:
            c.close()
        return {"platform": pid, "pages": pages, "job": job["store_refresh"].state(), "meta": job["netmeta"].state(),
                "meta_coverage": cov}

    @app.post("/api/catalogs/{pid}/netmeta")
    def api_catalog_netmeta(pid: str, body: dict = Body(default={})):
        """Copertine e descrizioni: GameTDB offline (subito) + lettura delle pagine dei siti."""
        if pid not in P.PLATFORMS:
            raise HTTPException(404, "Console sconosciuta")
        limit = int(body.get("limit") or 0)  # 0 = tutte le voci (circa 2 richieste al secondo)
        if not _claim("netmeta", J.NetMetaJob(limit=max(0, min(limit, 50000)),
                                              search_ziperto=bool(body.get("ziperto", True)))):
            return job["netmeta"].state()
        threading.Thread(target=lambda: job["netmeta"].run(cfg, lambda stats: lib.load()),
                         daemon=True).start()
        return job["netmeta"].state()

    @app.post("/api/catalogs/{pid}/netmeta/stop")
    def api_catalog_netmeta_stop(pid: str):
        job["netmeta"].stop = True
        return job["netmeta"].state()

    @app.get("/api/pc-stores")
    def api_pc_stores():
        """Store PC non ufficiali configurati, con quanti giochi ha reso ciascun elenco."""
        from ..cache import Cache
        from ..catalog import cached_info
        c = Cache(cfg.cache)
        try:
            rows = []
            for it in (cfg.pc_stores or []):
                info = cached_info(c, "windows", it["url"])
                rows.append({"url": it["url"], "kind": it.get("kind", "direct"), "games": len(info["entries"]),
                             "type": info["type"], "time": info["time"]})
        finally:
            c.close()
        return {"stores": rows, "job": job["store_refresh"].state()}

    @app.post("/api/pc-stores")
    def api_pc_stores_set(body: dict = Body(...)):
        raw = body.get("stores") or []
        cleaned = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            url = str(it.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            kind = str(it.get("kind") or "direct").lower()
            cleaned.append({"url": url, "kind": kind if kind in ("repack", "direct") else "direct"})
        C.set_setting(cfg, "pc_stores", cleaned)
        if body.get("refresh") and cleaned:
            _start_store_refresh(manual=True, only=[f"windows:{it['url']}" for it in cleaned])
        elif not cleaned:
            threading.Thread(target=lambda: (build(cfg, log=lambda m: None), lib.load()), daemon=True).start()
        return api_pc_stores()

    @app.post("/api/pc-stores/probe")
    async def api_pc_stores_probe(body: dict = Body(...)):
        """Anteprima di un indirizzo: formato, giochi nella pagina, pagine totali, primi titoli.

        Non salva nulla: serve a capire se l'elenco scelto contiene tutto il catalogo.
        """
        from .. import catalog, platforms as P
        from ..cache import Cache
        from ..net import make_client
        url = str(body.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "Indirizzo non valido (serve http:// o https://)")
        c = Cache(cfg.cache)
        try:
            async with make_client() as client:
                page = await catalog._get_page(cfg, c, client, "windows", url, log=lambda m: None)
        except Exception as e:
            raise HTTPException(502, f"Pagina non raggiungibile: {type(e).__name__}: {e}")
        finally:
            c.close()
        info = catalog.describe_page(page, url, set(P.PLATFORMS["windows"].exts))
        info["url"] = url
        return info

    @app.post("/api/pc-stores/refresh")
    def api_pc_stores_refresh():
        """Anche «Leggi gli elenchi» passa dalla coda unica: se qualcosa sta girando, si accoda."""
        if not cfg.pc_stores:
            raise HTTPException(400, "Nessuno store PC aggiunto")
        _start_store_refresh(manual=True, only=[f"windows:{it['url']}" for it in cfg.pc_stores])
        return api_pc_stores()

    # ------------------------------------------------------------ aggiornamento di tutti gli store
    # Gli elenchi letti restano in cache.db: la libreria si ricostruisce da li', quindi all'avvio i
    # giochi degli store ci sono gia' e questo aggiornamento gira in sottofondo (in parallelo).

    def _start_store_refresh(manual: bool = False, only: list[str] | None = None) -> int:
        """Mette in coda gli store scelti (o tutti): la coda rispetta il limite «in parallelo»."""
        from .. import applog
        available = SR.targets(cfg)
        if only:
            wanted = {str(x) for x in only}
            available = [t for t in available if t["id"] in wanted or t["url"] in wanted]
            if not available:
                applog.write("piano", f"in coda rifiutato: nessuno store tra {only}")
                raise HTTPException(404, "Nessuno di questi store è configurato")
        elif not available:
            applog.write("piano", "in coda rifiutato: nessuno store configurato")
            if manual:
                raise HTTPException(400, "Nessuno store configurato")
            return 0
        added = job["store_refresh"].add(cfg, available, lambda stats: lib.load())
        applog.write("piano", f"in coda: {added} store" + (f" (scelti: {', '.join(map(str, only))})" if only
                                                           else " (tutti)"))
        return added

    @app.get("/api/store-refresh")
    def api_store_refresh():
        rows = SR.targets(cfg)
        return {"schedule": SR.settings(cfg), "last": cfg.app.get("store_schedule_last"),
                "stores": len(rows), "list": rows, "job": job["store_refresh"].state()}

    @app.post("/api/store-refresh")
    def api_store_refresh_set(body: dict = Body(...)):
        """Salva quando aggiornare gli store: all'avvio, ogni giorno a un'ora scelta, o solo a mano."""
        C.save_settings(cfg, app={"store_schedule": {"mode": str(body.get("mode") or "startup"),
                                                     "time": str(body.get("time") or "03:00"),
                                                     "workers": body.get("workers") or SR.DEFAULT_WORKERS}})
        return api_store_refresh()

    @app.post("/api/store-refresh/run")
    def api_store_refresh_run(body: dict = Body(default={})):
        """Avvio manuale: di tutti gli elenchi oppure solo di quelli indicati (`ids` o `urls`)."""
        ids = [str(x) for x in (body.get("ids") or []) + (body.get("urls") or [])]
        _start_store_refresh(manual=True, only=ids or None)
        return api_store_refresh()

    @app.post("/api/store-refresh/stop")
    def api_store_refresh_stop():
        from .. import applog
        job["store_refresh"].stop = True  # le pagine in corso finiscono, poi si ferma subito
        applog.write("piano", "interruzione richiesta")
        return job["store_refresh"].state()

    def _store_scheduler():
        """Aggiornamento automatico degli store: all'avvio oppure ogni giorno all'ora scelta."""
        import datetime as _dt

        from .. import applog
        started = False
        time.sleep(15)  # lascia finire l'avvio dell'app (libreria, plugin)
        while True:
            try:
                sch = SR.settings(cfg)
                if sch["mode"] != "manual":
                    applog.write("piano", f"controllo: modalita'={sch['mode']} ora={sch['time']} "
                                          f"ultimo={cfg.app.get('store_schedule_last')} building={state['building']}")
                if sch["mode"] == "startup" and not started:
                    if _start_store_refresh():
                        started = True
                        C.save_settings(cfg, app={"store_schedule_last": _dt.datetime.now().isoformat(timespec="minutes")})
                elif sch["mode"] == "daily":
                    now = _dt.datetime.now()
                    today = now.strftime("%Y-%m-%d")
                    if not str(cfg.app.get("store_schedule_last") or "").startswith(today):
                        hh, mm = (int(x) for x in sch["time"].split(":")[:2])
                        if now.hour * 60 + now.minute >= hh * 60 + mm and _start_store_refresh():
                            C.save_settings(cfg, app={"store_schedule_last": now.isoformat(timespec="minutes")})
                else:
                    started = True  # modalita' manuale: niente da fare
            except Exception as e:
                print(f"Pianificazione store: {e}")
                from .. import applog
                applog.write("piano", f"errore: {type(e).__name__}: {e}")
            time.sleep(60)

    threading.Thread(target=_store_scheduler, daemon=True).start()

    @app.post("/api/catalogs/{pid}")
    def api_catalog_set(pid: str, body: dict = Body(...)):
        if pid not in P.PLATFORMS:
            raise HTTPException(404, "Console sconosciuta")
        urls = [u.strip() for u in body.get("urls", []) if u.strip().startswith(("http://", "https://"))]
        # il tipo di parser (e le eventuali classi CSS) restano quando si cambiano solo gli URL
        settings = dict(C.read_settings(cfg).get("catalogs", {}))
        current = settings.get(pid)
        opts = dict(current) if isinstance(current, dict) else {}
        opts.pop("urls", None)
        if body.get("type"):
            opts["type"] = str(body["type"])
        if urls:
            settings[pid] = {**opts, "urls": urls} if opts else urls
        else:  # nessun URL: la console esce dai cataloghi, con le sue opzioni
            settings.pop(pid, None)
        C.set_setting(cfg, "catalogs", settings)
        if body.get("refresh", True) and urls:
            _start_catalog_job(pid)
        elif not urls:
            threading.Thread(target=lambda: (build(cfg, log=lambda m: None), lib.load()), daemon=True).start()
        return api_catalog_get(pid)

    def _start_catalog_job(pid: str):
        """Anche la lettura di una console passa dalla coda unica degli store (niente più «già in corso»)."""
        urls = [f"{pid}:{u}" for u in (cfg.catalogs.get(pid) or [])]
        if not urls:
            raise HTTPException(400, "Nessuno store online configurato per questa console")
        _start_store_refresh(manual=True, only=urls)

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

    def _relink_busy():
        if state["building"] or job["current"].status == "running":
            raise HTTPException(409, "Uno scraping o un aggiornamento e' gia' in corso")

    @app.get("/api/relink/plan")
    def api_relink_plan():
        """Giochi i cui file non esistono piu' e cartelle nuove che sembrano la stessa cosa rinominata."""
        _relink_busy()
        pairs = RL.plan(cfg, log=lambda m: None)
        return {"pairs": [{"old": o, "new": n, "title": t, "path": p} for o, n, t, p in pairs]}

    @app.post("/api/relink/apply")
    def api_relink_apply(body: dict = Body(...)):
        _relink_busy()
        want = set(body.get("new") or [])
        # l'abbinamento si ricalcola qui: il client sceglie solo quali applicare, non le chiavi da spostare
        pairs = [x for x in RL.plan(cfg, log=lambda m: None) if x[1] in want]
        if not pairs:
            raise HTTPException(404, "Nessun abbinamento da applicare (le cartelle sono cambiate?)")
        RL.apply(cfg, pairs, log=lambda m: None)
        threading.Thread(target=_rebuild, daemon=True).start()
        return {"applied": len(pairs)}

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

    # ------------------------------------------------------------ copie di sicurezza dei dati utente
    def _auto_backup():
        try:
            BK.auto(cfg)
        except Exception as e:  # una copia mancata non deve impedire l'avvio
            print(f"backup automatico non riuscito: {type(e).__name__}: {e}", file=sys.stderr)

    threading.Thread(target=_auto_backup, daemon=True).start()

    @app.get("/api/backups")
    def api_backups():
        return {"folder": str(BK.folder(cfg)), "items": BK.list_backups(cfg)}

    @app.post("/api/backups")
    def api_backup_create():
        return {"name": BK.create(cfg).name}

    @app.post("/api/backups/restore")
    def api_backup_restore(body: dict = Body(...)):
        if launcher.running().get("status") in ("starting", "extracting", "running"):
            raise HTTPException(409, "Chiudi il gioco in corso prima di ripristinare")  # registrerebbe la partita sui dati vecchi
        try:
            return BK.restore(cfg, body.get("name") or "", user=user)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------------------ RetroAchievements: obiettivi dei giochi retro
    from .. import retroachievements as RA

    def _ra_conf() -> dict:
        return dict(cfg.app.get("retroachievements") or {})

    @app.get("/api/ra")
    def api_ra_state():
        c = _ra_conf()
        return {"username": c.get("username", ""), "has_key": bool(c.get("key"))}

    @app.post("/api/ra")
    def api_ra_save(body: dict = Body(...)):
        """Salva nome utente e Web API key (profilo di retroachievements.org) dopo averli provati con una richiesta."""
        c = _ra_conf()
        if "username" in body:
            c["username"] = str(body.get("username") or "").strip()
        if body.get("key"):
            c["key"] = str(body["key"]).strip()
        if c.get("username") and c.get("key"):
            try:
                with RA.client(c["username"], c["key"]) as rc:
                    RA.user_progress(rc, count=1)
            except RA.RAError as e:
                raise HTTPException(400, str(e))
        C.save_settings(cfg, app={"retroachievements": c})
        cfg.app["retroachievements"] = c
        return api_ra_state()

    @app.get("/api/game/{gid}/ra")
    def api_game_ra(gid: int, force: int = 0):
        """Obiettivi del gioco e progresso dell'utente. Cache: catalogo della console 7 giorni, abbinamento 30 giorni,
        progresso 30 minuti (force=1 lo riscarica)."""
        g = lib.get(gid)
        conf = _ra_conf()
        if not (conf.get("username") and conf.get("key")):
            return {"configured": False}
        try:
            cid = RA.resolve_console_id(g["platform"])
        except KeyError:
            return {"configured": True, "supported": False}
        from ..cache import Cache
        cache = Cache(cfg.cache)
        try:
            with RA.client(conf["username"], conf["key"]) as rc:
                mk = f"ra_match:{g['key']}"
                ra_id = cache.kv_get(mk, max_age=30 * 86400)
                if ra_id is None:
                    catalog = cache.kv_get(f"ra_gamelist:{cid}", max_age=7 * 86400)
                    if catalog is None:
                        catalog = RA.game_list(rc, cid)
                        cache.kv_set(f"ra_gamelist:{cid}", catalog)
                    f = Path(g["files"][0]) if g.get("files") else None
                    # il titolo in libreria puo' essere tradotto ("Versione Smeraldo"): RetroAchievements usa i nomi
                    # inglesi, come il nome canonico (No-Intro) e di solito il nome del file
                    from ..scanner import clean_title
                    names = [n for n in dict.fromkeys([g["title"], g.get("canonical") or "", clean_title(f.stem) if f else ""]) if n]
                    md5 = None
                    if g["platform"] in RA._RAW_MD5_CONSOLES | {"snes"}:
                        # cartucce senza intestazione: l'hash RA e' l'MD5 della ROM, gia' noto dal DAT No-Intro (anche per gli .zip)
                        for p in g.get("files") or []:
                            row = cache.db.execute("SELECT data FROM files WHERE path=?", (p,)).fetchone()
                            d = json.loads(row[0]) if row else {}
                            md5 = (d.get("dat_md5") or d.get("md5") or "").lower() or None
                            if md5:
                                break
                    md5 = md5 or (RA.rom_hash(f, g["platform"]) if f else None)
                    hit = next((h for h in (RA.match_game(n, catalog, rom_md5=md5) for n in names) if h), None)
                    ra_id = hit["id"] if hit else 0
                    cache.kv_set(mk, ra_id)
                if not ra_id:
                    return {"configured": True, "supported": True, "found": False}
                pk = f"ra_prog:{conf['username']}:{ra_id}"
                prog = None if force else cache.kv_get(pk, max_age=1800)
                if prog is None:
                    prog = RA.game_progress(rc, ra_id)
                    cache.kv_set(pk, prog)
        except RA.RAError as e:
            return {"configured": True, "supported": True, "error": str(e)}
        finally:
            cache.close()
        return {"configured": True, "supported": True, "found": True, "game": prog,
                "url": f"https://retroachievements.org/game/{ra_id}"}

    # ------------------------------------------------------------ esportazione per altri frontend (ES-DE, Pegasus)
    export_state = {"running": False}

    @app.post("/api/export/frontend")
    def api_export_frontend(body: dict = Body(...)):
        from .. import frontends
        fmt = body.get("format")
        if fmt not in ("esde", "pegasus"):
            raise HTTPException(400, "formato non valido (esde o pegasus)")
        raw = str(body.get("out_dir") or "").strip().strip('"')
        out = Path(os.path.expandvars(os.path.expanduser(raw))) if raw else Path(cfg.output) / fmt
        if export_state["running"]:
            raise HTTPException(409, "Esportazione gia' in corso")
        export_state["running"] = True
        try:
            out.mkdir(parents=True, exist_ok=True)
            fn = frontends.export_esde if fmt == "esde" else frontends.export_pegasus
            summary = fn(Path(cfg.cache), out, [p for p in body.get("platforms") or [] if p in P.PLATFORMS] or None)
        except OSError as e:
            raise HTTPException(400, f"Cartella non scrivibile: {e}")
        finally:
            export_state["running"] = False
        return {"ok": True, "summary": summary, "games": sum(summary.values()), "out_dir": str(out)}

    # ------------------------------------------------------------ Switch: aggiornamento e DLC presenti nella cartella
    switch_keys: dict = {}

    @app.get("/api/game/{gid}/switch")
    def api_game_switch(gid: int):
        """{update, dlc} dalla cache della fonte nsp; se mancano (gioco scansionato prima) si leggono dalla cartella."""
        g = lib.get(gid)
        if g["platform"] != "switch" or not g.get("files"):
            return {}
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            nsp = (c.all_resources([g["key"]]).get(g["key"]) or {}).get("nsp") or {}
        finally:
            c.close()
        if "switch_update" in nsp or "switch_dlc" in nsp:
            return {"update": nsp.get("switch_update"), "dlc": int(nsp.get("switch_dlc") or 0)}
        folder = Path(g["files"][0])
        if not folder.is_dir():
            return {}
        from .. import switchmeta
        try:
            if "k" not in switch_keys:
                switch_keys["k"] = switchmeta.load_keys(getattr(cfg, "switch_keys", ""))
            fc = switchmeta.folder_contents(folder, switch_keys["k"])
        except Exception as e:  # chiavi mancanti o pacchetto illeggibile: nessuna informazione, nessun errore
            return {"error": f"{type(e).__name__}: {e}"[:160]}
        return {"update": fc.get("update_display") or (str(fc["update"]) if fc.get("update") is not None else None),
                "dlc": fc.get("dlc") or 0}

    # ------------------------------------------------------------ salvataggi dei giochi (copie di sicurezza)
    from ..library import saves as SV

    def _saves_of(g: dict) -> tuple[str, list[Path]]:
        """(emulatore, cartelle/file dei salvataggi) del gioco, con l'emulatore che lo avvierebbe adesso."""
        if g.get("store") == "ports":  # port PC: con portable.txt i salvataggi stanno nella sua cartella
            m = PT.all_installed(cfg).get(g["key"].split(":", 1)[1])
            return "Port PC", PT.user_files(Path(m["folder"]), m) if m else []
        p = P.PLATFORMS.get(g["platform"])
        try:
            pl = L.plan(cfg, p, user.profile(g["key"])) if p and not g.get("store") else None
        except L.LaunchError:
            pl = None
        if not pl or not SV.supported(pl["emulator"]):
            return (pl or {}).get("emulator") or "", []
        tid = ""
        if g["platform"] == "switch":
            from ..cache import Cache
            c = Cache(cfg.cache)
            try:
                tid = ((c.all_resources([g["key"]]).get(g["key"]) or {}).get("nsp") or {}).get("titleid") or ""
            finally:
                c.close()
        stem = Path(g["files"][0]).stem if g.get("files") else ""
        return pl["emulator"], SV.game_saves(pl["emulator"], pl.get("exe"), rom_stem=stem, title_id=tid)

    @app.get("/api/game/{gid}/saves")
    def api_saves(gid: int):
        g = lib.get(gid)
        emu, paths = _saves_of(g)
        return {"emulator": emu, "supported": SV.supported(emu) or emu == "Port PC", "paths": [str(x) for x in paths],
                "items": SV.list_backups(cfg, g["key"])}

    @app.post("/api/game/{gid}/saves")
    def api_saves_backup(gid: int):
        g = lib.get(gid)
        emu, paths = _saves_of(g)
        out = SV.backup(cfg, g["key"], g["title"], paths) if paths else None
        if not out:
            raise HTTPException(404, f"Nessun salvataggio trovato per questo gioco ({emu or 'emulatore non configurato'})")
        return {"name": out.name}

    @app.post("/api/game/{gid}/saves/restore")
    def api_saves_restore(gid: int, body: dict = Body(...)):
        g = lib.get(gid)
        if launcher.running().get("status") in ("starting", "extracting", "running"):
            raise HTTPException(409, "Chiudi il gioco prima di ripristinare i salvataggi")  # l'emulatore li riscriverebbe
        try:
            return SV.restore(cfg, g["key"], body.get("name") or "")
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(400, str(e))

    def _saves_after_game(st: dict):
        """A fine partita: copia dei salvataggi di quel gioco (se l'emulatore e' supportato)."""
        g = lib.by_key.get(st.get("key") or "")
        if not g or not (SV.supported(st.get("emulator_id") or "") or g.get("store") == "ports")                 or not cfg.app.get("save_backups", True):
            return
        try:
            _, paths = _saves_of(g)
            size = sum(f.stat().st_size for p in paths for f in ([p] if p.is_file() else p.rglob("*")) if f.is_file())
            if paths and size <= 1 << 30:  # oltre 1 GB (x10 copie) solo a mano: non si riempie il disco in silenzio
                SV.backup(cfg, g["key"], g["title"], paths, "dopo-partita")
        except Exception as e:  # una copia mancata non deve disturbare
            print(f"copia dei salvataggi non riuscita: {type(e).__name__}: {e}", file=sys.stderr)

    events.on("game_stopped", lambda st: threading.Thread(target=_saves_after_game, args=(st,), daemon=True).start())

    # ------------------------------------------------------------ versione, novita' e aggiornamenti
    @app.get("/api/about")
    def api_about():
        from .. import __version__, updates
        return {"version": __version__, "changelog": updates.changelog(), "data_dir": str(C.settings_path(cfg).parent)}

    # ------------------------------------------------------------ spazio dei dati scaricati (cache)
    storage_cache: dict = {}

    def _dir_size(path: Path) -> tuple[int, int]:
        """(byte, file) di una cartella, senza seguire i collegamenti."""
        total = n = 0
        stack = [str(path)]
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                total += e.stat(follow_symlinks=False).st_size
                                n += 1
                        except OSError:
                            pass
            except OSError:
                pass
        return total, n

    @app.get("/api/storage")
    def api_storage(force: int = 0):
        """Dove stanno i metadati scaricati e quanto pesano: per cartella, console, fonte e store online."""
        if not force and storage_cache.get("t", 0) > time.time() - 120:
            return storage_cache["v"]
        from ..cache import Cache
        root = Path(cfg.cache)
        folders = []
        for e in sorted(root.iterdir(), key=lambda x: x.name):
            if e.is_dir():
                b, n = _dir_size(e)
            else:
                b, n = e.stat().st_size, 1
            folders.append({"name": e.name + ("/" if e.is_dir() else ""), "bytes": b, "files": n})
        folders.sort(key=lambda x: -x["bytes"])
        media = root / "media"
        plats: dict = defaultdict(lambda: {"media": 0, "files": 0, "types": {}, "db": 0, "rows": 0, "catalog": 0, "entries": 0})
        if media.is_dir():
            for pd in media.iterdir():
                if not pd.is_dir():
                    continue
                for td in pd.iterdir():
                    b, n = _dir_size(td) if td.is_dir() else (td.stat().st_size, 1)
                    d = plats[pd.name]
                    d["media"] += b
                    d["files"] += n
                    d["types"][td.name] = d["types"].get(td.name, 0) + b
        sources, kv = [], []
        c = Cache(cfg.cache)
        try:
            # il peso e' quello del testo salvato (le righe del database), non del file .db intero
            for plat, nrow, nb in c.db.execute(
                    "SELECT substr(key,1,instr(key,'/')-1), COUNT(*), SUM(length(value)+length(field)+length(key)) "
                    "FROM resources GROUP BY 1"):
                if plat:
                    plats[plat]["db"] += nb or 0
                    plats[plat]["rows"] += nrow
            for src, nrow, nb in c.db.execute(
                    "SELECT source, COUNT(*), SUM(length(value)+length(field)+length(key)) FROM resources GROUP BY source ORDER BY 3 DESC"):
                sources.append({"source": src, "rows": nrow, "bytes": nb or 0})
            labels = {"catalog": "Elenchi degli store online", "netmeta": "Copertine e descrizioni degli store",
                      "storemeta": "Dati degli store PC", "store": "Store PC", "ports": "Port PC", "art": "Immagini",
                      "hltb": "Durata (HowLongToBeat)", "collart": "Immagini delle collezioni", "size": "Dimensioni"}
            for k, nrow, nb in c.db.execute(
                    "SELECT substr(k,1,instr(k||':',':')-1), COUNT(*), SUM(length(v)) FROM kv GROUP BY 1 ORDER BY 3 DESC"):
                kv.append({"kind": k or "altro", "label": labels.get(k, k or "altro"), "rows": nrow, "bytes": nb or 0})
            try:
                for pid, nrow, nb in c.db.execute(
                        "SELECT json_extract(v,'$.pid'), COUNT(*), SUM(length(v)) FROM kv WHERE k LIKE 'catalog:%' GROUP BY 1"):
                    if pid:
                        plats[pid]["catalog"] += nb or 0
                        plats[pid]["entries"] += nrow
            except sqlite3.Error:
                pass
        finally:
            c.close()
        files = {n: (root / n).stat().st_size for n in ("cache.db", "library.db", "userdata.db") if (root / n).exists()}
        out = {"path": str(root), "total": sum(f["bytes"] for f in folders), "folders": folders, "files": files,
               "platforms": sorted(({"id": k, **v, "total": v["media"] + v["db"] + v["catalog"]} for k, v in plats.items()),
                                   key=lambda x: -x["total"]),
               "sources": sources, "kv": kv}
        storage_cache.update(t=time.time(), v=out)
        return out

    @app.get("/api/update")
    def api_update(force: int = 0):
        from .. import updates
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            return {**updates.check(c, force=bool(force)), "install_kind": updates.install_kind()}
        finally:
            c.close()

    @app.post("/api/update/install")
    def api_update_install():
        """Scarica e installa la nuova versione in sottofondo; poi l'app si chiude e si riapre aggiornata."""
        from .. import updates
        from ..cache import Cache
        if updates.STATE["status"] != "running":
            def run():
                c = Cache(cfg.cache)
                try:  # 1,5 s perche' la pagina legga "riavvio" prima che il processo esca
                    updates.install(c, quit=lambda: threading.Timer(1.5, os._exit, args=(0,)).start())
                finally:
                    c.close()
            updates.STATE["status"] = "running"
            threading.Thread(target=run, daemon=True).start()
        return updates.STATE

    @app.get("/api/update/install")
    def api_update_install_state():
        from .. import updates
        return updates.STATE

    # ------------------------------------------------------------ salute: problemi da sistemare, in un posto solo
    @app.get("/api/health")
    def api_health():
        """Controlli veloci (nessuna richiesta in rete). Ogni voce: livello ok/warn/error, testo e, se c'e', l'azione
        dell'interfaccia che la risolve (fix: scheda delle impostazioni, scraping o copia dei dati)."""
        out = []

        def add(level, title, detail="", fix=None):
            out.append({"level": level, "title": title, "detail": detail, "fix": fix})

        def num(n):
            return f"{n:,}".replace(",", ".")

        def roms():
            roots = C.rom_roots(cfg)
            off = [r for r in roots if not r.exists()]
            if len(off) == len(roots):
                add("error", "Cartella delle ROM non raggiungibile", f"{cfg.roms} (disco scollegato o cartella spostata?)",
                    {"label": "Cambia cartella", "tab": "scan"})
            elif off:
                add("warn", f"{len(off)} cartelle ROM non raggiungibili", ", ".join(map(str, off))
                    + ". I loro giochi spariscono dalla libreria al prossimo aggiornamento finché il disco non torna "
                    "(i dati restano salvati).", {"label": "Nuovi giochi", "tab": "scan"})
            else:
                add("ok", "Cartelle delle ROM raggiungibili", ", ".join(map(str, roots)))

        def emulators():
            with lib.lock:
                pids = sorted({g["platform"] for g in lib.games if not g.get("store")} & set(P.PLATFORMS))
            bad = [r for r in (_emu_row(p) for p in pids) if not r["effective"]]
            if bad:
                add("warn", f"{len(bad)} console senza emulatore pronto",
                    "; ".join(f"{r['name']}: {r['problem']}" for r in bad[:8]) + ("…" if len(bad) > 8 else ""),
                    {"label": "Emulatori", "tab": "emu"})
            elif pids:
                add("ok", "Emulatori pronti per tutte le console", f"{len(pids)} console")

        def switch_keys():
            with lib.lock:
                has = any(g["platform"] == "switch" for g in lib.games)
            if not has:
                return
            from .. import switchmeta
            found = switchmeta.key_candidates(getattr(cfg, "switch_keys", ""))
            if found:
                add("ok", "prod.keys trovato: metadati Switch letti dai file", str(found[0]))
            else:
                add("warn", "prod.keys non trovato", "Senza chiavi i giochi Switch usano solo le fonti online. "
                    "Indicalo in config.toml, [paths] switch_keys, oppure installa le chiavi in Ryujinx/Eden.")

        def library():
            with lib.lock:
                prim = [g for g in lib.games if g["is_primary"] and not g.get("store")]
            nodata = sum(1 for g in prim if not g["found"])
            nocover = sum(1 for g in prim if g["found"] and "cover" not in g["media"])
            fix = {"label": "Completa i dati mancanti", "scan": "missing"}
            if nodata:
                add("warn", f"{num(nodata)} giochi senza dati", "Nessuna fonte li ha riconosciuti.", fix)
            if nocover:
                add("warn", f"{num(nocover)} giochi senza copertina", "", fix)
            if not nodata and not nocover:
                add("ok", "Tutti i giochi hanno dati e copertina", num(len(prim)))

        def missing_files():
            with lib.lock:
                rows = [(g["title"], g["files"]) for g in lib.games if g["files"] and not g.get("store")]
            gone = [t for t, fl in rows if not any(Path(f).exists() for f in fl)]
            if gone:
                add("warn", f"{num(len(gone))} giochi con i file non trovati",
                    ", ".join(gone[:6]) + ("…" if len(gone) > 6 else "") + ". Se hai rinominato o spostato le cartelle, "
                    "riassociali da Nuovi giochi prima di aggiornare la libreria.", {"label": "Nuovi giochi", "tab": "scan"})
            else:
                add("ok", "Tutti i file dei giochi sono presenti", num(len(rows)))

        def sources():
            from ..cache import Cache
            c = Cache(cfg.cache)
            try:
                fg = c.kv_get("freegames") or {}
            finally:
                c.close()
            errs = [e.split(":")[0] for e in fg.get("errors", []) if e != "reset"]
            down = [f"immagini da {h}" for h, until in _REMOTE_DOWN.items() if until > time.time()]
            if errs or down:
                add("warn", "Alcune fonti online non rispondono", "; ".join(errs + down)
                    + ". Si riprova da solo più tardi; intanto si usa l'ultimo elenco salvato.")

        def backups():
            last = BK.list_backups(cfg)
            if not last:
                add("warn", "Nessuna copia dei tuoi dati", "", {"label": "Crea una copia", "backup": True})
                return
            days = int((time.time() - last[0]["time"]) / 86400)
            old = days >= 8
            add("warn" if old else "ok", f"Ultima copia dei tuoi dati: {'oggi' if not days else f'{days} giorni fa'}",
                last[0]["name"], {"label": "Crea una copia", "backup": True} if old else None)

        def streaming():
            if not SUN.install_dir():
                return
            state, mode = SUN.service_state(), SUN.start_mode()
            fix = {"label": "Avvia Sunshine (anche con Windows)", "sunshine": True}
            if state == "stopped":
                add("warn", "Sunshine è fermo: lo streaming verso TV e telefono non funziona",
                    "Moonlight non trova il PC finché il servizio di Sunshine non è avviato.", fix)
            elif state == "running" and mode and mode != "auto":
                add("warn", "Sunshine non parte con Windows", "Dopo un riavvio lo streaming smette di funzionare.", fix)
            elif state == "running":
                add("ok", "Sunshine attivo: streaming pronto per Moonlight")

        for fn in (roms, emulators, switch_keys, library, missing_files, sources, backups, streaming):
            try:
                fn()
            except Exception as e:  # un controllo rotto non deve nascondere gli altri
                add("warn", "Controllo non riuscito", f"{fn.__name__}: {type(e).__name__}: {e}")
        order = {"error": 0, "warn": 1, "ok": 2}
        return {"items": sorted(out, key=lambda x: order[x["level"]])}

    @app.get("/api/status")
    def api_status():
        return {k: state[k] for k in ("building", "build_stats", "thumbs_total", "thumbs_done")} | {
            "build_log": state["build_log"][-8:], "has_library": bool(lib.games) or lib.built_at > 0,
            "built_at": lib.built_at, "user_rev": user.rev,
            "running": launcher.running().get("status") or ""}

    @app.post("/api/focus")
    def api_focus():
        """Una seconda copia dell'app chiede di riportare in primo piano la finestra gia' aperta."""
        fn = WINDOW_FOCUS["fn"]
        if fn:
            fn()
        return {"window": bool(fn)}

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
        from urllib.parse import urlparse
        h = hashlib.sha1(url_.encode()).hexdigest()
        folder = Path(cfg.cache) / "media" / "net"
        hit = next(iter(folder.glob(h + ".*")), None) if folder.exists() else None
        if hit:
            return hit.relative_to(cfg.cache).as_posix()
        # piu' indirizzi separati da "|": si prova il primo che risponde (es. copertine GameTDB
        # in regioni diverse, dove non tutte esistono)
        candidates = [u.strip() for u in url_.split("|") if u.strip()][:4]
        for candidate in candidates:
            host = urlparse(candidate).hostname or ""
            with _REMOTE_SEM:
                # un host irraggiungibile (es. CDN che blocca l'IP) non deve tenere appese decine di richieste: il browser
                # ha pochi collegamenti verso l'app e le copertine dei giochi resterebbero in coda dietro a queste
                if _REMOTE_DOWN.get(host, 0) > time.time():
                    continue
                try:
                    r = httpx.get(candidate, timeout=httpx.Timeout(10, connect=4), follow_redirects=True,
                                  headers={"User-Agent": UA})
                except httpx.HTTPError as e:
                    if isinstance(e, (httpx.ConnectTimeout, httpx.ConnectError)):
                        _REMOTE_DOWN[host] = time.time() + 300
                    continue
            ext = sniff(r.content[:16]) if r.status_code == 200 else None
            if not ext:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"{h}.{ext}"
            dest.write_bytes(r.content)
            return dest.relative_to(cfg.cache).as_posix()
        return None

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
    def _phone_startup():
        if phone_hub.conf.get("enabled"):
            threading.Thread(target=phone_hub.start, args=(app,), daemon=True).start()

    @app.on_event("shutdown")
    def _phone_shutdown():
        phone_hub.stop()

    @app.on_event("startup")
    def _startup():
        if not lib.games and lib.path.exists():
            lib.full.wait()  # nessun gioco posseduto: prima di ricreare la libreria si guarda anche il catalogo
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
        # sandbox: un SVG scaricato da un sito puo' contenere script, che qui girerebbero con l'accesso alle API
        return FileResponse(p, media_type=ICON_TYPES.get(p.suffix, "image/png"),
                            headers={"Cache-Control": "public, max-age=86400", "Content-Security-Policy": "sandbox"})

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
        j = J.HltbJob(only_missing=body.get("mode", "missing") != "all")
        if not _claim("hltb", j):
            raise HTTPException(409, "Ricerca delle durate gia' in corso")

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

    # ------------------------------------------------------------ migliori giochi (Top 100 per console, globale 100/1000)
    @app.get("/api/top")
    def api_top(pid: str = "all", limit: int = 100):
        from .. import topgames
        limit = max(1, min(limit, topgames.GLOBAL))
        with lib.lock:
            games = list(lib.games)
        items = topgames.global_(games, limit) if pid == "all" else topgames.local(games, pid, min(limit, topgames.LOCAL))
        return {"pid": pid, "items": items, "job": job["top"].state()}

    @app.post("/api/top/fetch")
    def api_top_fetch(body: dict = Body(default={})):
        pid = body.get("pid") or "all"
        j = J.TopGamesJob(pids=[] if pid == "all" else [pid])
        if not _claim("top", j):
            raise HTTPException(409, "Ricerca dei voti gia' in corso")

        def after(stats):
            lib.load()
            events.emit("library_built", stats)

        threading.Thread(target=j.run, args=(cfg, after), daemon=True).start()
        return j.state()

    @app.get("/api/top/state")
    def api_top_state():
        return job["top"].state()

    @app.post("/api/top/cancel")
    def api_top_cancel():
        job["top"].stop = True
        return job["top"].state()

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
        if not _claim("collart", j):
            raise HTTPException(409, "Ricerca delle immagini delle collezioni gia' in corso")

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
        hist = user.wish_history()
        for w in items:
            w["owned"] = normalize(w["title"]) in owned
            w["history"] = hist.get(w["id"], [])
        return {"items": items, "settings": {"country": s["country"], "threshold": s["threshold"],
                                             "has_key": bool(s["itad_key"]), "has_gg": bool(s["gg_key"])},
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
        for k in ("itad_key", "gg_key"):
            if k in body:
                cur[k] = (body.get(k) or "").strip() or None
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
                    "shared.akamai.steamstatic.com", "cdn.akamai.steamstatic.com", "assets.isthereanydeal.com",
                    "cdn.cloudflare.steamstatic.com", "m.media-amazon.com",  # copertine Prime (feed LootScraper)
                    "media.retroachievements.org",  # icone degli obiettivi
                    "cdn2.steamgriddb.com", "ccdn.thunderstore.io", "gcdn.thunderstore.io")  # port PC e mod

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
        fs = C.read_settings(cfg).get("free_state") or {}
        seen, claimed = set(fs.get("seen") or []), set(fs.get("claimed") or [])
        items = [{**i, "owned": normalize(i["title"]) in owned, "wished": normalize(i["title"]) in wished,
                  "new": _free_id(i) not in seen, "claimed": _free_id(i) in claimed, "id": _free_id(i)}
                 for i in d.get("items", [])]
        new = sum(1 for i in items if i["new"] and not i["owned"] and not i["claimed"])
        return {"items": items, "time": d.get("time"), "errors": d.get("errors", []), "stale": d.get("stale", False),
                "new": new, "first": not fs}

    def _free_id(i: dict) -> str:
        return f"{i.get('source')}|{i.get('console')}|{normalize(i.get('title') or '')}"

    @app.post("/api/free/mark")
    def api_free_mark(body: dict = Body(...)):
        """seen=true: tutti quelli in elenco non sono piu' «nuovi»; claim=<id> + value: riscattato si/no."""
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            current = {_free_id(i) for i in (c.kv_get("freegames") or {}).get("items", [])}
        finally:
            c.close()
        fs = C.read_settings(cfg).get("free_state") or {}
        seen, claimed = set(fs.get("seen") or []), set(fs.get("claimed") or [])
        if body.get("seen"):
            seen |= current
        if body.get("claim"):
            (claimed.add if body.get("value", True) else claimed.discard)(str(body["claim"]))
        # si tengono solo le offerte ancora in elenco: le liste non crescono all'infinito
        C.set_setting(cfg, "free_state", {"seen": sorted(seen & current), "claimed": sorted(claimed & current)})
        return {"ok": True}

    @app.get("/remote-img")
    def remote_img(u: str):
        from urllib.parse import urlparse
        if urlparse(u).hostname not in REMOTE_HOSTS:
            raise HTTPException(400)
        rel = _fetch_remote(u)
        if not rel:
            raise HTTPException(404)
        return FileResponse(Path(cfg.cache) / rel, headers={"Cache-Control": "public, max-age=604800"})

    # ------------------------------------------------------------ port PC (ricompilazioni e decomp, catalogo di Quiver)
    from . import ports_api
    _port_links = ports_api.register(app, cfg, lib, launcher, _start_store_job)

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
        # la versione nell'indirizzo dei file statici evita che il browser usi una copia vecchia
        # di app.js/core.js dopo un aggiornamento (era il motivo di pulsanti con comportamenti strani)
        from .. import __version__
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace("{{v}}", str(__version__))
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/bigpicture")
    def bigpicture():
        from .. import __version__
        html = (STATIC / "bigpicture.html").read_text(encoding="utf-8").replace("{{v}}", str(__version__))
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _plain(s: str) -> str:
    import re
    return re.sub(r"\[/?[a-z ]*\]", "", s)


def _port_free(port: int) -> bool:
    import socket
    with socket.socket() as sk:
        try:
            sk.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class LocalGuard:
    """Middleware ASGI del server desktop: risponde solo a richieste fatte all'app stessa.

    - Host diverso da 127.0.0.1/localhost -> 403 (DNS rebinding: un sito che punta il suo dominio a 127.0.0.1
      leggerebbe libreria, account e codice di abbinamento del telefono);
    - POST/PUT/DELETE e WebSocket con un Origin di un altro sito -> 403 (CSRF: un sito che avvia giochi,
      disinstalla port o usa il controller/mouse del telefono). Senza Origin (curl, gamescraper-cli) si passa.
    """

    def __init__(self, app, port: int):
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            h = dict(scope["headers"])
            origin = h.get(b"origin", b"").decode("latin-1")
            unsafe = scope["type"] == "websocket" or scope.get("method") not in ("GET", "HEAD", "OPTIONS")
            if h.get(b"host", b"").decode("latin-1") not in self.hosts or (unsafe and origin and origin not in self.origins):
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 4403})
                return await Response("Richiesta non consentita", status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)


WINDOW_FOCUS = {"fn": None}  # impostata quando l'app gira in una finestra nativa


def window_titlebar(hwnd: int, color: str) -> dict:
    """Barra del titolo e bordo di una finestra Windows del colore dato ('#rgb' o '#rrggbb').
    Windows 11: colore esatto. Windows 10 (2004+): solo barra scura o chiara, secondo la luminosita'."""
    import ctypes
    h = str(color).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))  # ValueError se il colore non e' esadecimale
    dark = r * 299 + g * 587 + b * 114 < 128_000
    dwm, user32 = ctypes.windll.dwmapi, ctypes.windll.user32

    def put(attr: int, value: int) -> bool:
        v = ctypes.c_int(value)
        return dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(v), 4) == 0

    out = {"dark": put(20, dark) or put(19, dark)}  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 sulle build prima della 2004)
    rgb = r | g << 8 | b << 16  # COLORREF: 0x00BBGGRR
    out["color"] = put(35, rgb)  # DWMWA_CAPTION_COLOR, solo Windows 11
    if out["color"]:
        put(34, rgb)  # DWMWA_BORDER_COLOR
        put(36, 0xFFFFFF if dark else 0x000000)  # DWMWA_TEXT_COLOR
    # Windows 10 ridisegna la barra solo quando la finestra cambia stato attivo: la si "riattiva"
    active = user32.GetForegroundWindow() == hwnd
    user32.SendMessageW(ctypes.c_void_p(hwnd), 0x0086, not active, 0)  # WM_NCACTIVATE
    user32.SendMessageW(ctypes.c_void_p(hwnd), 0x0086, active, 0)
    return out


def _focus_running_window(port: int) -> bool:
    import json as _json
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/focus", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return bool(_json.loads(r.read()).get("window"))
    except Exception:
        return False


def _is_gamescraper(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return b"build_stats" in r.read(400)
    except Exception:
        return False


def run(cfg, port: int = 8765, browser: bool = False, bigpicture: bool = False):
    import uvicorn
    if not _port_free(port):
        if _is_gamescraper(port):  # una copia e' gia' aperta: non se ne avvia un'altra sugli stessi dati
            if not browser and _focus_running_window(port):
                print("GameScraper e' gia' aperto: ho riportato in primo piano la sua finestra.")
                return
            print(f"GameScraper e' gia' in esecuzione su http://127.0.0.1:{port}/ - lo apro nel browser. "
                  "Per aprirne un'altra usa --port.")
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{port}/" + ("bigpicture" if bigpicture else ""))
            return
        free = next((q for q in range(port + 1, port + 50) if _port_free(q)), None)
        if free is None:
            raise SystemExit(f"Porta {port} occupata da un altro programma: scegline un'altra con --port")
        print(f"Porta {port} occupata da un altro programma: uso la {free}")
        port = free
    app = LocalGuard(create_app(cfg), port)  # il server del telefono usa l'app senza guardia, dietro PhoneGate
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

            def set_titlebar(self, color):
                """Barra del titolo della finestra del colore del tema (chiamata dal frontend)."""
                if sys.platform != "win32":
                    return None
                try:
                    return window_titlebar(webview.windows[0].native.Handle.ToInt64(), color)
                except Exception as e:  # colore non valido o finestra non pronta: resta quella di sistema
                    return {"error": str(e)}

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
        api = Api()
        WINDOW_FOCUS["fn"] = api.focus_window
        webview.create_window("GameScraper", start_url, width=1500, height=940, min_size=(960, 600),
                              background_color="#111318", js_api=api, fullscreen=bigpicture)
        webview.start()
    except Exception as e:  # pywebview non disponibile: si usa il browser
        print(f"Finestra desktop non disponibile ({e}), apro il browser: {url}")
        import webbrowser
        webbrowser.open(start_url)
        t.join()
    finally:
        server.should_exit = True
