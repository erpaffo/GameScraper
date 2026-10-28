"""Sincronizzazione dei dati personali tra PC.

Un "pacchetto" (bundle) contiene i dati personali di un profilo: per ogni gioco preferito, nascosto, stato, nota,
voto, versione principale e ultima partita, ognuno con l'ora della sua ultima modifica (tabella sync_times di
userdata.db); le sessioni di gioco; collezioni, modifiche alle collezioni e lista desideri, con le cancellazioni.
merge() unisce il pacchetto di un altro PC: per ogni dato vince la modifica piu' recente, le sessioni si sommano
senza doppioni (il tempo di gioco e' la somma delle sessioni dei due PC). Unire due volte lo stesso pacchetto non
cambia nulla.

Facoltativo (what = "all"): anche le impostazioni non legate al PC (store online, fonti, tema, collezioni
intelligenti...): vince la versione salvata piu' di recente. Percorsi, emulatori, account degli store, telefono e
profili restano sempre del singolo PC.

Trasporti:
- cartella condivisa (OneDrive, Dropbox, NAS, Syncthing): <cartella>/PozzoLauncher-sync/<profilo>/<pc>.json, ogni PC
  scrive solo il proprio file, quindi il servizio di sincronizzazione non crea conflitti;
- rete locale: il server HTTPS del telefono accetta i PC abbinati (vedi remote/phone.py); il certificato del PC
  remoto e' verificato con l'impronta salvata all'abbinamento.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import ssl
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from . import config as C
from .library.userdata import DELETED, UserData

FORMAT = 1
GAME_FIELDS = ("favorite", "hidden", "preferred", "status", "note", "my_rating", "last_played")
WISH_FIELDS = ("title", "steam_appid", "itad_id", "target", "added")
# impostazioni sincronizzabili (what = "all"): sezioni intere di settings.json e voci di "app"
SETTINGS_SECTIONS = ("catalogs", "pc_stores", "limits", "screenscraper", "igdb", "rawg", "thegamesdb", "custom_platforms")
APP_KEYS = ("theme", "smart_collections", "disabled_plugins", "skip_duplicates", "wishlist", "retroachievements")
DIR = "PozzoLauncher-sync"
INTERVAL = 300  # secondi tra due sincronizzazioni automatiche


# ---------------------------------------------------------------- configurazione e profili

def conf(cfg) -> dict:
    """Impostazioni della sincronizzazione (app.sync), con l'identita' di questo PC creata la prima volta."""
    c = {"folder": "", "what": "personal", "peers": [], **(cfg.app.get("sync") or {})}
    if not c.get("pc_id"):
        c["pc_id"] = uuid.uuid4().hex[:12]
        save_conf(cfg, c)
    return c


def save_conf(cfg, c: dict):
    cfg.app["sync"] = c
    C.save_settings(cfg, app={"sync": c})


def profiles(cfg) -> list[dict]:
    """Profili utente; "default" (Principale, rinominabile) usa sempre cache/userdata.db."""
    names = {p["id"]: p.get("name") or p["id"] for p in cfg.app.get("profiles") or [] if p.get("id")}
    return ([{"id": "default", "name": names.get("default", "Principale")}]
            + [{"id": i, "name": n} for i, n in names.items() if i != "default"])


def _save_profiles(cfg, lst: list[dict]):
    cfg.app["profiles"] = lst
    C.save_settings(cfg, app={"profiles": lst})


def profile_path(cfg, pid: str) -> Path:
    base = Path(cfg.cache)
    return base / "userdata.db" if pid == "default" else base / "profiles" / f"{pid}.db"


def profile_id(name: str) -> str:
    """Id dal nome: lo stesso nome su due PC e' lo stesso profilo (cosi' si sincronizzano)."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "profilo"


def open_profile(cfg, pid: str) -> UserData:
    path = profile_path(cfg, pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    return UserData(path, shared=profile_path(cfg, "default"))


def add_profile(cfg, name: str) -> dict:
    """Nuovo profilo (o quello che esiste gia' con lo stesso nome)."""
    name = name.strip()[:40]
    if not name:
        raise ValueError("Serve un nome")
    pid = profile_id(name)
    if pid == "default":
        pid = "default-2"
    known = profiles(cfg)
    hit = next((p for p in known if p["id"] == pid), None)
    if hit:
        return hit
    _save_profiles(cfg, [*known, {"id": pid, "name": name}])
    return {"id": pid, "name": name}


def rename_profile(cfg, pid: str, name: str):
    """Cambia solo il nome mostrato: l'id resta, cosi' il profilo continua a sincronizzarsi con gli altri PC."""
    name = name.strip()[:40]
    if not name:
        raise ValueError("Serve un nome")
    _save_profiles(cfg, [{**p, "name": name} if p["id"] == pid else p for p in profiles(cfg)])


def delete_profile(cfg, pid: str) -> Path | None:
    """Toglie il profilo; il suo database va in profiles/eliminati/ (non si cancella nulla)."""
    if pid == "default":
        raise ValueError("Il profilo principale non si elimina")
    _save_profiles(cfg, [p for p in profiles(cfg) if p["id"] != pid])
    src = profile_path(cfg, pid)
    if not src.exists():
        return None
    dst = src.parent / "eliminati" / f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    for ext in ("-wal", "-shm"):  # il database e' chiuso dal server prima di chiamare questa funzione
        Path(f"{src}{ext}").unlink(missing_ok=True)
    return dst


# ---------------------------------------------------------------- pacchetto e unione

def _stamps(ud: UserData) -> dict:
    return {(t, i, f): v for t, i, f, v in ud._q("SELECT tbl, id, field, t FROM sync_times")}


def export(ud: UserData, profile: dict, pc: dict, settings: dict | None = None) -> dict:
    st = _stamps(ud)
    games = {}
    for key, g in ud.games().items():
        f = {k: [g[k], st.get(("game", key, k), 0)] for k in GAME_FIELDS
             if g[k] not in (None, 0, "") or ("game", key, k) in st}
        if f:
            games[key] = f
    colls = {slug: {**c, "t": st.get(("coll", slug, ""), 0)} for slug, c in ud.collections().items()}
    edits = {f"{s}\t{k}": [a, st.get(("edit", f"{s}\t{k}", ""), 0)] for s, k, a in ud.edits()}
    wish = {w["id"]: {**{k: w[k] for k in WISH_FIELDS}, "t": st.get(("wish", w["id"], ""), 0)} for w in ud.wishlist()}
    deleted = {}
    for (tbl, i, f), t in st.items():
        if f == DELETED:
            deleted.setdefault(tbl, {})[i] = t
    out = {"format": FORMAT, "pc": pc["pc_id"], "pc_name": pc.get("pc_name", ""), "time": time.time(),
           "profile": profile, "games": games, "sessions": [list(s) for s in ud.sessions()],
           "colls": colls, "edits": edits, "wish": wish, "deleted": deleted}
    if settings is not None:
        out["settings"] = settings
    return out


def _newer(rt: float, lt: float, local_value, remote_value) -> bool:
    """Vince il dato modificato piu' di recente; a parita' (dati mai toccati da quando esiste la sincronizzazione)
    vince il valore impostato su quello vuoto, cosi' al primo giro si uniscono i preferiti dei due PC."""
    return rt > lt or (rt == lt and local_value in (None, 0, "") and remote_value not in (None, 0, ""))


def merge(ud: UserData, b: dict) -> dict:
    """Unisce il pacchetto di un altro PC nel profilo. Ritorna quante cose sono cambiate."""
    if b.get("format") != FORMAT:
        raise ValueError("Pacchetto di sincronizzazione di una versione diversa di PozzoLauncher: aggiorna entrambi i PC")
    st, local = _stamps(ud), ud.games()
    n = {"games": 0, "sessions": 0, "colls": 0, "wish": 0}
    stamps = []
    with ud.lock:
        db = ud.db
        for key, fields in (b.get("games") or {}).items():
            lg = local.get(key, {})
            for f, (val, rt) in fields.items():
                if f not in GAME_FIELDS:
                    continue
                if _newer(rt, st.get(("game", key, f), 0), lg.get(f), val):
                    db.execute(f"INSERT INTO user_games(key, {f}) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET {f}=excluded.{f}",
                               (key, val))
                    stamps.append(("game", key, f, rt))
                    n["games"] += 1
        have = {(k, round(s, 3)) for k, s, _ in db.execute("SELECT key, start, seconds FROM play_sessions")}
        for key, start, secs in b.get("sessions") or []:
            if (key, round(start, 3)) in have:
                continue
            have.add((key, round(start, 3)))
            db.execute("INSERT INTO play_sessions VALUES(?, ?, ?)", (key, start, secs))
            db.execute("INSERT INTO user_games(key, play_count, playtime) VALUES(?, 1, ?) ON CONFLICT(key) DO UPDATE "
                       "SET play_count=COALESCE(play_count, 0)+1, playtime=COALESCE(playtime, 0)+excluded.playtime",
                       (key, secs))
            n["sessions"] += 1
        dels = b.get("deleted") or {}
        lcolls = {r[0] for r in db.execute("SELECT slug FROM user_collections")}
        for slug, c in (b.get("colls") or {}).items():
            lt = max(st.get(("coll", slug, ""), 0), st.get(("coll", slug, DELETED), 0))
            if c["t"] > lt or (c["t"] == lt == 0 and slug not in lcolls):
                db.execute("INSERT INTO user_collections(slug, name, custom, hidden, created) VALUES(?, ?, ?, ?, ?) "
                           "ON CONFLICT(slug) DO UPDATE SET name=excluded.name, custom=excluded.custom, "
                           "hidden=excluded.hidden", (slug, c.get("name"), c.get("custom") or 0, c.get("hidden") or 0, time.time()))
                stamps.append(("coll", slug, "", c["t"]))
                n["colls"] += 1
        for slug, t in (dels.get("coll") or {}).items():
            if t > st.get(("coll", slug, ""), 0) and t > st.get(("coll", slug, DELETED), 0):
                db.execute("DELETE FROM user_collections WHERE slug=?", (slug,))
                db.execute("DELETE FROM collection_edits WHERE slug=?", (slug,))
                stamps.append(("coll", slug, DELETED, t))
                n["colls"] += 1
        ledits = {f"{s}\t{k}": a for s, k, a in db.execute("SELECT slug, key, action FROM collection_edits")}
        for sk, (action, rt) in (b.get("edits") or {}).items():
            if _newer(rt, st.get(("edit", sk, ""), 0), ledits.get(sk), action) and ledits.get(sk) != action:
                slug, key = sk.split("\t", 1)
                db.execute("INSERT OR REPLACE INTO collection_edits VALUES(?, ?, ?)", (slug, key, action))
                stamps.append(("edit", sk, "", rt))
                n["colls"] += 1
        lwish = {r[0] for r in db.execute("SELECT id FROM wishlist")}
        for wid, w in (b.get("wish") or {}).items():
            lt = max(st.get(("wish", wid, ""), 0), st.get(("wish", wid, DELETED), 0))
            if w["t"] > lt or (w["t"] == lt == 0 and wid not in lwish):
                vals = [w.get(k) for k in WISH_FIELDS]
                db.execute(f"INSERT INTO wishlist(id, {', '.join(WISH_FIELDS)}) VALUES(?{', ?' * len(WISH_FIELDS)}) "
                           f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in WISH_FIELDS)}",
                           (wid, *vals))
                stamps.append(("wish", wid, "", w["t"]))
                n["wish"] += 1
        for wid, t in (dels.get("wish") or {}).items():
            if t > st.get(("wish", wid, ""), 0) and t > st.get(("wish", wid, DELETED), 0):
                db.execute("DELETE FROM wishlist WHERE id=?", (wid,))
                db.execute("DELETE FROM wish_prices WHERE id=?", (wid,))
                stamps.append(("wish", wid, DELETED, t))
                n["wish"] += 1
        # l'ora resta quella della modifica originale: un terzo PC confronta sempre la stessa ora
        db.executemany("INSERT OR REPLACE INTO sync_times VALUES(?, ?, ?, ?)", stamps)
        db.commit()
        if any(n.values()):
            ud.rev += 1
    return n


# ---------------------------------------------------------------- impostazioni (what = "all")

def _settings_subset(cfg) -> dict:
    s = C.read_settings(cfg)
    return {"sections": {k: s[k] for k in SETTINGS_SECTIONS if k in s},
            "app": {k: (s.get("app") or {})[k] for k in APP_KEYS if k in (s.get("app") or {})}}


def settings_bundle(cfg, c: dict) -> dict:
    """Impostazioni sincronizzabili con l'ora dell'ultima modifica (si accorge da solo dei cambiamenti)."""
    data = _settings_subset(cfg)
    h = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    if c.get("settings_hash") != h:
        c.update(settings_hash=h, settings_t=time.time())
        save_conf(cfg, c)
    return {"t": c["settings_t"], "data": data}


def merge_settings(cfg, c: dict, remote: dict | None) -> bool:
    if not remote or remote.get("t", 0) <= c.get("settings_t", 0):
        return False
    data = remote["data"]

    def change(s):
        for k in SETTINGS_SECTIONS:
            if k in data.get("sections", {}):
                s[k] = data["sections"][k]
            else:
                s.pop(k, None)
        app = s.setdefault("app", {})
        for k in APP_KEYS:
            if k in data.get("app", {}):
                app[k] = data["app"][k]
            else:
                app.pop(k, None)
    C._update_settings(cfg, change)
    h = hashlib.sha256(json.dumps(_settings_subset(cfg), sort_keys=True).encode()).hexdigest()
    c.update(settings_hash=h, settings_t=remote["t"])
    save_conf(cfg, c)
    return True


# ---------------------------------------------------------------- un giro di sincronizzazione

class Sync:
    """Stato e giro di sincronizzazione (cartella + PC in rete) per tutti i profili."""

    def __init__(self, cfg, user_of):
        self.cfg, self.user_of = cfg, user_of  # user_of(pid) -> UserData del profilo (gia' aperto dal server)
        self.last: dict = {}
        self.busy = False

    def pc(self) -> dict:
        c = conf(self.cfg)
        return {"pc_id": c["pc_id"], "pc_name": c.get("pc_name") or socket.gethostname()}

    def bundles(self, only: set | None = None) -> dict:
        c, pc = conf(self.cfg), self.pc()
        out = {}
        for p in profiles(self.cfg):
            if only is None or p["id"] in only:
                sett = settings_bundle(self.cfg, c) if c["what"] == "all" and p["id"] == "default" else None
                out[p["id"]] = export(self.user_of(p["id"]), p, pc, sett)
        return out

    def receive(self, bundles: dict) -> dict:
        """Unisce i pacchetti ricevuti (crea i profili che qui non esistono)."""
        c, n = conf(self.cfg), {}
        known = {p["id"] for p in profiles(self.cfg)}
        for pid, b in bundles.items():
            if pid not in known:
                add_profile(self.cfg, (b.get("profile") or {}).get("name") or pid)
            r = merge(self.user_of(pid), b)
            if c["what"] == "all" and pid == "default" and merge_settings(self.cfg, c, b.get("settings")):
                r["settings"] = 1
            n[pid] = r
        return n

    def run(self) -> dict:
        if self.busy:
            return self.last
        self.busy = True
        res = {"time": time.time(), "folder": None, "peers": {}}
        try:
            c = conf(self.cfg)
            if c.get("folder"):
                try:
                    res["folder"] = self.via_folder(Path(c["folder"]))
                except Exception as e:  # cartella scollegata, OneDrive in pausa...: si riprova al prossimo giro
                    res["folder"] = {"error": f"{type(e).__name__}: {e}"}
            for peer in c.get("peers") or []:
                try:
                    res["peers"][peer["id"]] = self.via_peer(peer)
                except Exception as e:  # PC spento o fuori dalla rete
                    res["peers"][peer["id"]] = {"error": f"{type(e).__name__}: {e}"}
        finally:
            self.busy = False
            self.last = res
        return res

    # ------------------------------------------------------------ cartella condivisa
    def via_folder(self, folder: Path) -> dict:
        if not folder.is_dir():
            raise FileNotFoundError(f"Cartella non raggiungibile: {folder}")
        root, me = folder / DIR, self.pc()["pc_id"]
        incoming: dict = {}
        if root.is_dir():
            for pdir in root.iterdir():
                for f in pdir.glob("*.json") if pdir.is_dir() else []:
                    if f.stem != me:
                        try:
                            incoming.setdefault(f"{pdir.name}\t{f.stem}", json.loads(f.read_text(encoding="utf-8")))
                        except (OSError, ValueError):
                            continue  # file a meta' scrittura sull'altro PC: si legge al prossimo giro
        merged = {}
        for k, b in incoming.items():
            merged[k] = self.receive({k.split("\t")[0]: b})
        for pid, b in self.bundles().items():
            d = root / pid
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f".{me}.tmp"
            tmp.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, d / f"{me}.json")
        return {"ok": True, "from": len(incoming), "merged": merged}

    # ------------------------------------------------------------ PC in rete locale
    def via_peer(self, peer: dict) -> dict:
        r = request(peer["url"], "/api/sync/exchange", {"pc": self.pc(), "bundles": self.bundles()},
                    token=peer["token"], fp=peer.get("fp"))
        return {"ok": True, "merged": self.receive(r.get("bundles") or {})}


def request(url: str, path: str, body: dict, token: str | None = None, fp: str | None = None) -> dict:
    """POST HTTPS al PC remoto. Il certificato (autofirmato) si verifica con l'impronta salvata all'abbinamento,
    prima di mandare il token: un altro computer sulla rete non puo' fingersi quel PC."""
    u = urlparse(url)
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # verifica fatta qui sotto con l'impronta
    conn = http.client.HTTPSConnection(u.hostname, u.port or 8766, context=ctx, timeout=60)
    try:
        conn.connect()
        got = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
        if fp and got != fp:
            raise RuntimeError("Il certificato del PC remoto e' cambiato: abbinalo di nuovo")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = f"gs_phone={token}"
        conn.request("POST", path, json.dumps(body), headers)
        r = conn.getresponse()
        data = json.loads(r.read() or b"{}")
        if r.status >= 400:
            raise RuntimeError(data.get("detail") or f"HTTP {r.status}")
        data["_fp"] = got
        return data
    finally:
        conn.close()
