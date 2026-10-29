"""API dei port PC (ricompilazioni e decomp, catalogo di Quiver): catalogo, installazione, ROM e mod."""

from __future__ import annotations

import threading
import time
import zipfile
from collections import defaultdict
from pathlib import Path

from fastapi import Body, HTTPException

from .. import config as C
from .. import platforms as P
from .. import ports as PT

# stato dell'installazione/aggiornamento di un port (uno alla volta); lo legge anche il controllo dei lavori in corso
PORT_JOB: dict = {"status": "idle", "log": [], "slug": "", "done": 0, "total": 0}


def register(app, cfg, lib, launcher, start_store_job):
    """Registra le route /api/ports*. Ritorna port_links(), usata dal dettaglio del gioco."""
    port_job = PORT_JOB  # fuori dalla chiusura: server.py deve sapere se un port si sta scaricando
    start_lock = threading.Lock()
    refresh = {"running": False, "rel": 0.0}  # rel: ultimo controllo delle versioni dei port installati
    port_links: dict = {"key": None, "by_slug": {}, "by_grp": {}, "in_store": {}}

    def _port_links() -> dict:
        """Port <-> ROM della libreria, ricalcolato solo quando cambiano la libreria o il catalogo."""
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            cat = c.kv_get("ports:catalog") or {}
        finally:
            c.close()
        key = (lib.built_at, cat.get("time"), len(lib.games))
        if port_links["key"] != key:
            idx, online = PT.rom_index(lib.games), PT.rom_index(lib.games, online=True)
            by_slug, by_grp, in_store = {}, defaultdict(list), {}
            for e in cat.get("items") or []:
                roms = PT.roms_for(e, idx)
                if not roms:  # ROM non posseduta: la si cerca tra gli store online
                    in_store[e["slug"]] = PT.roms_for(e, online)
                if roms:
                    by_slug[e["slug"]] = roms
                    for g in {r["grp"]: r for r in roms}.values():
                        by_grp[g["grp"]].append({"slug": e["slug"], "name": e["name"]})
            port_links.update(key=key, by_slug=by_slug, by_grp=dict(by_grp), in_store=in_store, catalog=cat)
        return port_links

    def _port_or_404(slug: str) -> dict:
        m = PT.all_installed(cfg).get(slug)
        if not m:
            raise HTTPException(404, "Port non installato")
        return m

    def _port_game_id(slug: str):
        g = lib.by_key.get(f"windows/ports:{slug}")
        return g["id"] if g else None

    def _refresh_ports_store():
        try:
            start_store_job(["ports"])
        except HTTPException:  # store gia' in aggiornamento: il port compare al prossimo aggiornamento
            port_job["log"].append("Aggiorna gli store (Impostazioni › Account) per vederlo nella libreria")

    def _run_port_job(slug: str, fn):
        def progress(done, total):
            port_job.update(done=done, total=total)

        def say(m):
            port_job["log"].append(str(m))
        try:
            fn(progress, say)
            port_job["status"] = "done"
            _refresh_ports_store()
        except Exception as e:
            say(f"Errore: {type(e).__name__}: {e}")
            port_job["status"] = "error"

    def _start_port_job(slug: str, fn):
        with start_lock:  # due installazioni insieme scriverebbero nella stessa cartella dei port
            if port_job["status"] == "running":
                raise HTTPException(409, "C'e' gia' un'installazione in corso")
            port_job.update(status="running", log=[], slug=slug, done=0, total=0)
        threading.Thread(target=_run_port_job, args=(slug, fn), daemon=True).start()
        return port_job

    def _refresh_catalog():
        """Aggiorna catalogo e versioni dei port installati; la pagina li rilegge quando refreshing torna False.
        Con «aggiorna da solo» attivo installa anche le versioni nuove: la patch la mette il launcher."""
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            PT.catalog(c)  # a catalogo scaduto lo riscarica
            tok = PT._token(cfg)
            for m in PT.all_installed(cfg).values():
                PT.update_info(c, m, tok)
            if cfg.app.get("ports_auto_update") and not port_job["status"] == "running":
                done = PT.update_all(cfg, c, tok, say=lambda m: port_job["log"].append(str(m)))
                if done:
                    _refresh_ports_store()
        except Exception:  # rete assente: resta la copia salvata
            pass
        finally:
            c.close()
            refresh.update(running=False, rel=time.time())

    def _start_refresh():
        with start_lock:
            if refresh["running"]:
                return
            refresh["running"] = True
        threading.Thread(target=_refresh_catalog, daemon=True).start()

    @app.get("/api/ports")
    def api_ports(force: int = 0):
        from ..cache import Cache
        c = Cache(cfg.cache)
        try:
            cached = PT.cached(c)
            # catalogo gia' salvato: si apre subito con quello e si aggiorna in background (force = pulsante «Aggiorna», in attesa)
            fast = bool(cached and cached.get("items") and not force)
            inst = PT.all_installed(cfg)
            if fast and (time.time() - cached.get("time", 0) >= PT.TTL
                         or inst and time.time() - refresh["rel"] >= PT.RELEASE_TTL):
                _start_refresh()
            cat = cached if fast else PT.catalog(c, force=bool(force))
            tok = PT._token(cfg)
            upd = {s: PT.update_info(c, m, tok, bool(force), offline=fast) for s, m in inst.items()}  # rete al massimo ogni 12 ore per repository
        finally:
            c.close()
        links = _port_links()

        def card(e: dict, m: dict | None) -> dict:
            art = e.get("libraryArt") or e.get("art") or {}
            sf = (e.get("launcher") or {}).get("setupFiles") or []
            return {"slug": e["slug"], "name": e.get("name") or e["slug"], "project": e.get("projectName") or "",
                    "description": e.get("description") or "",
                    "consoles": e.get("consoles") or [], "developer": (e.get("developer") or {}).get("name", "")
                    if isinstance(e.get("developer"), dict) else e.get("developer") or "",
                    "cover": art.get("capsule") or e.get("artwork") or "", "hero": art.get("hero") or "",
                    "version": e.get("lastReleaseVersion") or "", "tags": e.get("tags") or [],
                    "type": e.get("projectType") or "port",
                    "verified": ((e.get("verified") or {}).get("version") or ""),
                    "recommended": e.get("recommended") or 0, "broken": e.get("reportBroken") or 0,
                    "issues": e.get("reportIssues") or 0, "setup": sf, "mods": bool((e.get("launcher") or {}).get("mods")),
                    "roms": [{"id": g["id"], "title": g["title"], "platform": g["platform"], "file": (g.get("files") or [""])[0]}
                             for g in links["by_slug"].get(e["slug"], [])],
                    "in_store": [{"id": g["id"], "title": g["title"], "platform": g["platform"]}
                                 for g in links["in_store"].get(e["slug"], [])],
                    "installed": {"version": m.get("version") or "", "exe": m.get("exe") or "", "folder": m["folder"],
                                  "manual": bool(m.get("manual")), "skip": m.get("skip"), "game_id": _port_game_id(e["slug"]),
                                  "sha256": bool(m.get("sha256")), "repository": m.get("repository") or "",
                                  **upd.get(e["slug"], {})} if m else None}
        items = [card(e, inst.get(e["slug"])) for e in cat.get("items") or []]
        known = {e["slug"] for e in cat.get("items") or []}
        items += [card({**m, "slug": s}, m) for s, m in inst.items() if s not in known]  # aggiunti a mano
        names = {p.id: p.name for p in P.PLATFORMS.values()}
        consoles = {cid: names.get(PT.platform_of(cid) or "", {"pc": "PC (DOS e vecchi giochi)", "mobile": "Mobile", "cdi": "Philips CD-i", "ps5": "PlayStation 5"}.get(cid, cid))
                    for i in items for cid in i["consoles"]}
        return {"items": items, "time": cat.get("time"), "error": cat.get("error"), "stale": cat.get("stale", False),
                "consoles": consoles, "dir": str(PT.ports_dir(cfg)), "token": bool(PT._token(cfg)),
                "auto": bool(cfg.app.get("ports_auto_update")), "refreshing": refresh["running"]}

    @app.get("/api/ports/refreshing")
    def api_ports_refreshing():
        return {"refreshing": refresh["running"]}

    @app.get("/api/ports/job")
    def api_ports_job():
        return port_job

    @app.post("/api/ports/install")
    def api_ports_install(body: dict = Body(...)):
        slug = str(body.get("slug") or "")
        from ..cache import Cache

        def run(progress, say):
            c = Cache(cfg.cache)
            try:
                PT.install(cfg, c, slug, progress, say)
            finally:
                c.close()
        return _start_port_job(slug, run)

    @app.post("/api/ports/manual")
    def api_ports_manual(body: dict = Body(...)):
        """Port non in catalogo: da una cartella gia' scaricata o da un repository GitHub (utente/progetto)."""
        name, console = (body.get("name") or "").strip(), (body.get("console") or "").strip()
        consoles = [console] if console else []
        if body.get("folder"):
            try:
                m = PT.add_folder(cfg, body["folder"].strip().strip('"'), name, consoles)
            except ValueError as e:
                raise HTTPException(400, str(e))
            _refresh_ports_store()
            return m
        repo = (body.get("repo") or "").strip()
        return _start_port_job(repo, lambda progress, say: PT.add_repo(cfg, repo, name, consoles, progress, say))

    @app.get("/api/ports/{slug}")
    def api_port(slug: str):
        """Dettaglio di un port installato: eseguibili, ROM richieste (con la ROM della libreria che va bene)."""
        m = _port_or_404(slug)
        e = next((x for x in _port_links().get("catalog", {}).get("items") or [] if x["slug"] == slug), {})
        return {**m, "candidates": PT.exe_candidates(Path(m["folder"]), m.get("preferred")),
                "setup": [{"id": sf.get("id"), "label": sf.get("label") or sf.get("id"),
                           "destination": sf.get("destination"),
                           "present": (Path(m["folder"]) / sf.get("destination", "")).is_file(),
                           "accepted": [a.get("label") or "" for a in sf.get("accepted") or []]}
                          for sf in (e.get("launcher") or {}).get("setupFiles") or []],
                "user_files": len(PT.user_files(Path(m["folder"]), m)),
                "mods": bool(m.get("mods")), "installed_mods": m.get("installed_mods") or {}}

    @app.post("/api/ports/{slug}/update")
    def api_port_update(slug: str):
        m = _port_or_404(slug)
        if m.get("manual"):
            if not m.get("repository"):
                raise HTTPException(400, "Questo port non e' legato a un repository")
            return _start_port_job(slug, lambda progress, say: PT.add_repo(cfg, m["repository"], m.get("name", ""),
                                                                          m.get("consoles"), progress, say,
                                                                          provider=m.get("provider") or "github"))
        return api_ports_install({"slug": slug})

    @app.post("/api/ports/{slug}/exe")
    def api_port_exe(slug: str, body: dict = Body(...)):
        m = _port_or_404(slug)
        if body.get("exe") not in PT.exe_candidates(Path(m["folder"]), m.get("preferred")):
            raise HTTPException(400, "Eseguibile non valido")
        PT.set_manifest(cfg, slug, exe=body["exe"])
        _refresh_ports_store()
        return {"ok": True}

    @app.post("/api/ports/{slug}/skip")
    def api_port_skip(slug: str, body: dict = Body(...)):
        _port_or_404(slug)
        PT.set_manifest(cfg, slug, skip=body.get("version") or None)
        return {"ok": True}

    @app.post("/api/ports/{slug}/uninstall")
    def api_port_uninstall(slug: str):
        _port_or_404(slug)
        if launcher.running().get("status") in ("starting", "running") and launcher.running().get("key") == f"windows/ports:{slug}":
            raise HTTPException(409, "Chiudi il gioco prima di disinstallarlo")
        try:
            PT.uninstall(cfg, slug)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        _refresh_ports_store()
        return {"ok": True}

    @app.post("/api/ports/{slug}/rom")
    def api_port_rom(slug: str, body: dict = Body(...)):
        """Prima configurazione: copia nella cartella del port la ROM della libreria con l'hash richiesto."""
        m = _port_or_404(slug)
        links = _port_links()
        e = next((x for x in links.get("catalog", {}).get("items") or [] if x["slug"] == slug), {})
        sf = next((x for x in (e.get("launcher") or {}).get("setupFiles") or [] if x.get("id") == body.get("id")), None)
        if not sf:
            raise HTTPException(404, "File richiesto sconosciuto")
        paths = [Path(f) for g in links["by_slug"].get(slug, []) for f in g.get("files") or []]
        if not paths:
            raise HTTPException(404, "Nella libreria non c'e' la ROM di questo gioco")
        hit = PT.find_rom(sf, paths)
        if not hit:
            want = ", ".join(a.get("label") for a in sf.get("accepted") or [] if a.get("label")) or "quella indicata dal port"
            raise HTTPException(400, f"Le ROM della libreria non sono la versione richiesta ({want}): l'hash non corrisponde")
        try:
            dst = PT.setup_rom(Path(m["folder"]), sf, *hit)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"path": str(dst), "from": str(hit[0]) + (f" › {hit[1]}" if hit[1] else "")}

    @app.get("/api/ports/{slug}/mods")
    def api_port_mods(slug: str):
        from ..cache import Cache
        m = _port_or_404(slug)
        c = Cache(cfg.cache)
        try:
            items = PT.mod_list(c, m.get("mods") or {})
        finally:
            c.close()
        links = [{"provider": s.get("provider"), "url": s.get("sourceUrl")} for s in (m.get("mods") or {}).get("sources") or []]
        return {"items": items, "links": links, "installed": m.get("installed_mods") or {}}

    @app.post("/api/ports/{slug}/mods")
    def api_port_mod_add(slug: str, body: dict = Body(...)):
        from ..cache import Cache
        m = _port_or_404(slug)
        c = Cache(cfg.cache)
        try:
            mod = next((x for x in PT.mod_list(c, m.get("mods") or {}) if x["id"] == body.get("id")), None)
        finally:
            c.close()
        if not mod:
            raise HTTPException(404, "Mod non trovata")
        try:
            return PT.install_mod(cfg, slug, mod).get("installed_mods")
        except (ValueError, OSError, zipfile.BadZipFile) as e:
            raise HTTPException(400, f"Mod non installata: {e}")

    @app.delete("/api/ports/{slug}/mods/{mod_id:path}")
    def api_port_mod_del(slug: str, mod_id: str):
        _port_or_404(slug)
        return PT.remove_mod(cfg, slug, mod_id).get("installed_mods")

    @app.post("/api/ports-settings")
    def api_ports_settings(body: dict = Body(...)):
        vals = {}
        if "dir" in body:
            d = (body.get("dir") or "").strip().strip('"')
            if d and not Path(d).is_absolute():
                raise HTTPException(400, "Indica un percorso completo (es. D:\\Ports)")
            vals["ports_dir"] = d or None
        if "token" in body:
            vals["github_token"] = (body.get("token") or "").strip() or None
        if "auto" in body:
            vals["ports_auto_update"] = bool(body.get("auto"))
        C.save_settings(cfg, app=vals)
        if "ports_dir" in vals:
            _refresh_ports_store()
        return {"dir": str(PT.ports_dir(cfg)), "token": bool(PT._token(cfg)), "auto": bool(cfg.app.get("ports_auto_update"))}

    return _port_links
