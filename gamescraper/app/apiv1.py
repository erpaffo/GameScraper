"""API pubblica `/api/v1`: gli endpoint che si possono usare da fuori (CLI, Stream Deck, Home Assistant, script).

Regola: **questi endpoint non cambiano a sorpresa**. Quelli sotto `/api/` sono interni all'interfaccia e si
possono cambiare a ogni versione; questi no: si aggiungono, e si tolgono solo dopo un ciclo di deprecazione
(il campo `deprecated` compare nella risposta di `/api/v1/status`).

Autenticazione: `Authorization: Bearer <token>` (o `?token=` per EventSource), vedi `apitoken.py`. Fa
eccezione `/api/v1/status`, che serve a capire se l'app c'e' e non rivela nulla della libreria.

Errori: sempre `{"detail": "testo per la persona", "code": "not_found"}`; `404` quando la cosa non c'e',
`409` quando c'e' ma lo stato non permette l'operazione (gioco gia' in esecuzione, non installato...).
Le POST con `Idempotency-Key` ripetute entro 5 minuti restituiscono la prima risposta, senza rieseguire.
"""

from __future__ import annotations

import json
import queue
import time

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

API_VERSION = 1
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
_IDEM: dict[str, tuple[float, int, dict]] = {}
_IDEM_TTL = 300.0


def sse(events, heartbeat: float = 15.0):
    """Fotogrammi SSE dagli eventi del server (`game_started`, `library_built`, ...). Un commento `: ping`
    ogni `heartbeat` secondi tiene viva la connessione attraverso i proxy."""
    q = events.subscribe()
    try:
        yield ": connesso\n\n"
        while True:
            try:
                item = q.get(timeout=heartbeat)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            data = json.dumps(item.get("data") or {}, ensure_ascii=False, default=str)
            yield f"event: {item.get('event')}\ndata: {data}\n\n"
    finally:
        events.unsubscribe(q)


def _idem_key(request: Request) -> str:
    k = (request.headers.get("idempotency-key") or "").strip()[:120]
    return f"{request.method} {request.url.path} {k}" if k else ""


def _idem_get(key: str) -> tuple[int, dict] | None:
    hit = _IDEM.get(key)
    if not hit:
        return None
    if time.time() - hit[0] > _IDEM_TTL:
        _IDEM.pop(key, None)
        return None
    return hit[1], hit[2]


def _idem_put(key: str, status: int, body: dict):
    if not key:
        return
    now = time.time()
    for k in [k for k, v in _IDEM.items() if now - v[0] > _IDEM_TTL]:  # la memoria non cresce all'infinito
        _IDEM.pop(k, None)
    if len(_IDEM) < 500:
        _IDEM[key] = (now, status, body)


def register(app: FastAPI, ctx: dict) -> None:
    """`ctx` sono le parti gia' costruite da server.py: cfg, lib, user, launcher, events, view, game,
    play, member_keys, jobs, action, saves, saves_backup, saves_restore, backups, backup_create."""
    cfg, lib, user, launcher = ctx["cfg"], ctx["lib"], ctx["user"], ctx["launcher"]
    base = ctx["base_url"]  # indirizzo con cui il client ha raggiunto l'API, per le immagini assolute

    @app.get("/api/v1/status", tags=["stato"], summary="L'app c'e' e che versione e'")
    def v1_status():
        """L'unico endpoint senza token: serve a scoprire se l'app e' avviata. Nessun dato della libreria."""
        from .. import __version__
        return {"app": "gamescraper", "version": str(__version__), "api": API_VERSION,
                "deprecated": [], "library_built_at": lib.built_at, "games": len(lib.games)}

    @app.get("/api/v1/games", tags=["libreria"], summary="Giochi, con ricerca e pagine")
    def v1_games(request: Request, q: str = "", platform: str = "", store: str = "", status: str = "", key: str = "",
                 favorite: bool | None = None, versions: bool = False, limit: int = DEFAULT_LIMIT,
                 offset: int = 0, sort: str = "title"):
        """Risposta leggera per i client esterni (l'interfaccia usa `/api/library`, che manda tutto).
        `versions=true` include le versioni regionali, altrimenti solo la principale di ogni gioco."""
        ug = user.games()
        needle = q.strip().lower()
        out = []
        for g in lib.games:
            if not versions and not g.get("is_primary"):
                continue
            u = ug.get(g["key"]) or {}
            if favorite is not None and bool(u.get("favorite")) != favorite:
                continue
            if u.get("hidden") and favorite is not True:
                continue
            if platform and g["platform"] != platform:
                continue
            if store and (g.get("store") or "") != store:
                continue
            if status and (u.get("status") or "") != status:
                continue
            if key and g["key"] != key:
                continue
            if needle and needle not in (g.get("sort_title") or g["title"]).lower():
                continue
            out.append({
                "id": g["id"], "key": g["key"], "title": g["title"], "platform": g["platform"],
                "year": g.get("year"), "store": g.get("store"), "installed": g.get("installed"),
                "rating": g.get("rating"), "cover": f"{base(request)}/img/{g['id']}/cover" if "cover" in g["media"] else None,
                "favorite": bool(u.get("favorite")), "status": u.get("status") or "",
                "playtime": round(u.get("playtime") or 0), "last_played": round(u.get("last_played") or 0),
                "my_rating": u.get("my_rating") or 0,
            })
        keys = {"title": lambda x: (x["title"] or "").lower(), "year": lambda x: x["year"] or 0,
                "rating": lambda x: x["rating"] or 0, "playtime": lambda x: x["playtime"],
                "last_played": lambda x: x["last_played"]}
        field = sort.lstrip("-")
        if field not in keys:
            raise HTTPException(400, f"Ordinamento non valido: usa {', '.join(keys)} (con - per il contrario)")
        out.sort(key=keys[field], reverse=sort.startswith("-"))
        limit = max(1, min(MAX_LIMIT, int(limit)))
        return {"total": len(out), "limit": limit, "offset": int(offset),
                "items": out[int(offset):int(offset) + limit]}

    @app.get("/api/v1/games/{gid}", tags=["libreria"], summary="Un gioco, con tutte le sue versioni")
    def v1_game(gid: int, request: Request):
        d = ctx["game"](gid)  # 404 se non c'e'
        d.pop("files", None)  # percorsi sul disco del PC: non servono a un client esterno
        d["media"] = {k: f"{base(request)}{v}" for k, v in (d.get("media") or {}).items()}
        return d

    @app.post("/api/v1/games/{gid}/play", tags=["libreria"], summary="Avvia un gioco sul PC")
    def v1_play(gid: int, request: Request):
        """`Idempotency-Key` consigliato: una richiesta ripetuta (per esempio dopo un timeout) non avvia
        due volte e restituisce la stessa risposta."""
        k = _idem_key(request)
        hit = _idem_get(k)
        if hit:
            return JSONResponse(hit[1], status_code=hit[0])
        out = ctx["play"](gid)  # 404 gioco non trovato, 409 gia' in esecuzione / non installato
        body = {"ok": True, "id": gid, "title": lib.get(gid)["title"]}
        _idem_put(k, 200, body)
        return body

    @app.post("/api/v1/games/{gid}/favorite", tags=["libreria"], summary="Aggiunge o toglie dai preferiti")
    def v1_favorite(gid: int, body: dict = Body(default={})):
        value = bool(body.get("value", True))
        for k in ctx["member_keys"](gid):  # il preferito vale per tutte le versioni del gioco
            user.set_flag(k, "favorite", value)
        return {"ok": True, "id": gid, "favorite": value}

    @app.get("/api/v1/running", tags=["gioco in corso"], summary="Gioco in esecuzione, se c'e'")
    def v1_running():
        return launcher.running()

    @app.post("/api/v1/running/stop", tags=["gioco in corso"], summary="Chiude il gioco in esecuzione")
    def v1_stop():
        return {"stopped": launcher.stop()}

    @app.get("/api/v1/platforms", tags=["libreria"], summary="Console e store con quanti giochi, e i filtri disponibili")
    def v1_platforms():
        counts: dict[str, int] = {}
        for g in lib.games:
            if g.get("is_primary"):
                counts[g["platform"]] = counts.get(g["platform"], 0) + 1
        from .. import platforms as P
        return {"platforms": sorted(({"id": p, "name": P.PLATFORMS[p].name if p in P.PLATFORMS else p, "games": n}
                                     for p, n in counts.items()), key=lambda x: x["name"].lower()),
                "stores": sorted({g["store"] for g in lib.games if g.get("store")}),
                "statuses": ["backlog", "playing", "done", "dropped"]}

    @app.get("/api/v1/collections", tags=["libreria"], summary="Collezioni automatiche e personali")
    def v1_collections():
        v = ctx["view"]()
        return {"items": sorted(({"slug": c["slug"], "name": c["name"], "auto": c["auto"],
                                  "custom": c["custom"], "hidden": bool(c.get("hidden")), "count": len(c["groups"])}
                                 for c in v["colls"].values()), key=lambda x: x["name"].lower())}

    @app.get("/api/v1/collections/{slug}", tags=["libreria"], summary="I giochi di una collezione")
    def v1_collection(slug: str):
        v = ctx["view"]()
        c = v["colls"].get(slug)
        if not c:
            raise HTTPException(404, "Collezione non trovata")
        ids = [m["id"] for grp in c["groups"] for m in lib.groups.get(grp, []) if m.get("is_primary")]
        return {"slug": slug, "name": c["name"], "game_ids": sorted(ids)}

    @app.get("/api/v1/jobs", tags=["attivita'"], summary="Lavori in corso o appena finiti")
    def v1_jobs():
        return {"items": ctx["jobs"]()}

    @app.post("/api/v1/jobs/{action}", tags=["attivita'"], summary="Avvia o ferma un lavoro")
    def v1_job_start(action: str, request: Request):
        """`stores`, `scan`, `rebuild`, `stop-scan`, `stop-game`. Il lavoro gira in sottofondo: lo stato si
        segue con `/api/v1/jobs` o `/api/v1/events`."""
        if action not in ("stores", "scan", "rebuild", "stop-scan", "stop-game"):
            raise HTTPException(404, f"Comando sconosciuto: usa stores, scan, rebuild, stop-scan, stop-game")
        k = _idem_key(request)
        hit = _idem_get(k)
        if hit:
            return JSONResponse(hit[1], status_code=hit[0])
        out = ctx["action"]({"action": action})
        _idem_put(k, 200, out)
        return out

    @app.get("/api/v1/events", tags=["attivita'"], summary="Eventi in tempo reale (SSE)")
    def v1_events():
        """`text/event-stream`: un fotogramma per evento (`game_started`, `game_stopped`, `library_built`,
        `scrape_finished`, `stores_refreshed`, `collist_done`), più `: ping` come battito."""
        return StreamingResponse(sse(ctx["events"]), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/saves/{gid}", tags=["salvataggi"], summary="Versioni dei salvataggi di un gioco")
    def v1_saves(gid: int):
        d = ctx["saves"](gid)
        d.pop("manual", None)  # percorsi locali: si gestiscono dall'app
        return d

    @app.post("/api/v1/saves/{gid}", tags=["salvataggi"], summary="Crea subito una versione dei salvataggi")
    def v1_saves_backup(gid: int, request: Request):
        k = _idem_key(request)
        hit = _idem_get(k)
        if hit:
            return JSONResponse(hit[1], status_code=hit[0])
        out = ctx["saves_backup"](gid)  # 404 se il gioco non ha salvataggi noti
        _idem_put(k, 200, out)
        return out

    @app.post("/api/v1/saves/{gid}/restore", tags=["salvataggi"], summary="Ripristina una versione")
    def v1_saves_restore(gid: int, body: dict = Body(...)):
        """409 se il gioco è aperto: l'emulatore riscriverebbe i suoi file."""
        return ctx["saves_restore"](gid, body)

    @app.get("/api/v1/backups", tags=["dati"], summary="Copie di sicurezza dei dati personali")
    def v1_backups():
        return ctx["backups"]()

    @app.post("/api/v1/backups", tags=["dati"], summary="Crea una copia dei dati personali")
    def v1_backup_create(request: Request):
        k = _idem_key(request)
        hit = _idem_get(k)
        if hit:
            return JSONResponse(hit[1], status_code=hit[0])
        out = ctx["backup_create"]()
        _idem_put(k, 200, out)
        return out

    @app.get("/api/v1/wishlist", tags=["lista desideri"], summary="La lista desideri con i prezzi")
    def v1_wishlist():
        return {"items": user.wishlist()}
