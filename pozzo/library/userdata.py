"""Dati dell'utente, separati dalla libreria (che viene ricostruita): preferiti, tempo di gioco,
collezioni personali e modifiche manuali a quelle automatiche.

Le collezioni automatiche si ricalcolano a ogni ricostruzione; le modifiche dell'utente sono
memorizzate come "aggiungi"/"togli" per gioco, quindi sopravvivono alle ricostruzioni.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_games(key TEXT PRIMARY KEY, favorite INT DEFAULT 0, hidden INT DEFAULT 0,
                                      last_played REAL, play_count INT DEFAULT 0, playtime REAL DEFAULT 0,
                                      preferred TEXT);
CREATE TABLE IF NOT EXISTS user_collections(slug TEXT PRIMARY KEY, name TEXT, custom INT DEFAULT 0,
                                            hidden INT DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS collection_edits(slug TEXT, key TEXT, action TEXT, PRIMARY KEY(slug, key));
CREATE TABLE IF NOT EXISTS play_sessions(key TEXT, start REAL, seconds REAL);
CREATE INDEX IF NOT EXISTS play_sessions_start ON play_sessions(start);
CREATE TABLE IF NOT EXISTS wishlist(id TEXT PRIMARY KEY, title TEXT, steam_appid TEXT, itad_id TEXT, target REAL,
                                    added REAL, price REAL, regular REAL, cut INT, shop TEXT, url TEXT, low REAL,
                                    currency TEXT, checked REAL, alert TEXT, seen INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS wish_prices(id TEXT, day TEXT, price REAL, key_price REAL, PRIMARY KEY(id, day));
-- ora dell'ultima modifica di ogni dato personale (tbl, id, campo): serve alla sincronizzazione tra PC.
-- field "__deleted__" = cancellato a quell'ora (cosi' la cancellazione arriva anche sull'altro PC)
CREATE TABLE IF NOT EXISTS sync_times(tbl TEXT, id TEXT, field TEXT, t REAL, PRIMARY KEY(tbl, id, field));
"""
# impostazioni del PC, comuni a tutti i profili: stanno sempre in userdata.db (il profilo principale)
SHARED_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_profiles(key TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS manual_games(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, exe TEXT, args TEXT,
                                        cwd TEXT, added REAL, kind TEXT DEFAULT 'game');
-- immagini scelte dall'utente (percorso nella cache), valide al posto di quelle delle fonti
CREATE TABLE IF NOT EXISTS media_overrides(key TEXT, kind TEXT, path TEXT, PRIMARY KEY(key, kind));
"""
DELETED = "__deleted__"


class UserData:
    def __init__(self, path: Path, shared: Path | None = None):
        """shared: userdata.db del profilo principale, per i dati comuni a tutti i profili (profili di avvio,
        giochi aggiunti a mano). Le tabelle allegate si trovano anche senza prefisso, quindi le query restano uguali."""
        self.path = Path(path)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        if shared and Path(shared).resolve() != self.path.resolve():
            self.db.execute("ATTACH DATABASE ? AS shared", (str(shared),))
            self.db.executescript(SHARED_SCHEMA.replace("EXISTS ", "EXISTS shared."))
        else:
            self.db.executescript(SHARED_SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(user_games)")}
        if "status" not in cols:  # stato del gioco: backlog / playing / done / dropped
            self.db.execute("ALTER TABLE user_games ADD COLUMN status TEXT")
        for col, typ in (("note", "TEXT"), ("my_rating", "INT")):  # nota personale e voto (1-5) dell'utente
            if col not in cols:
                self.db.execute(f"ALTER TABLE user_games ADD COLUMN {col} {typ}")
        if "kind" not in {r[1] for r in self.db.execute("PRAGMA table_info(manual_games)")}:  # game | app
            self.db.execute("ALTER TABLE manual_games ADD COLUMN kind TEXT DEFAULT 'game'")
        wcols = {r[1] for r in self.db.execute("PRAGMA table_info(wishlist)")}
        # prezzo migliore sui negozi di key (GG.deals): attuale, minimo storico, pagina di confronto;
        # aks = dettaglio prezzi (JSON: Steam, key, offerte ITAD; nome storico, prima veniva da allkeyshop), cover = copertina
        for col, typ in (("key_price", "REAL"), ("key_low", "REAL"), ("key_url", "TEXT"), ("aks", "TEXT"),
                         ("aks_checked", "REAL"), ("cover", "TEXT")):
            if col not in wcols:
                self.db.execute(f"ALTER TABLE wishlist ADD COLUMN {col} {typ}")
        self.db.commit()
        self.lock = threading.Lock()
        self.rev = 0  # cresce a ogni modifica: chi mostra i dati (app sul PC) sa quando ricaricarli

    def close(self):
        """Chiude la connessione (all'uscita dall'app: su Windows un file aperto blocca la cartella dati)."""
        with self.lock:
            try:
                self.db.close()
            except sqlite3.Error:
                pass

    def _q(self, sql, args=()) -> list[tuple]:
        """Lettura protetta dal lock: la connessione e' condivisa tra i thread del server."""
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def _exec(self, sql, args=()):
        with self.lock:
            self.db.execute(sql, args)
            self.db.commit()
            self.rev += 1

    def _stamp(self, tbl: str, id_: str, *fields: str):
        """Ora della modifica di un dato personale (per la sincronizzazione: vince la modifica piu' recente)."""
        now = time.time()
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO sync_times VALUES(?, ?, ?, ?)",
                                [(tbl, id_, f, now) for f in fields])
            self.db.commit()

    # ------------------------------------------------------------ giochi
    def games(self) -> dict[str, dict]:
        cur = self._q("SELECT key, favorite, hidden, last_played, play_count, playtime, preferred, status, note, my_rating "
                      "FROM user_games")
        return {r[0]: {"favorite": r[1], "hidden": r[2], "last_played": r[3], "play_count": r[4],
                       "playtime": r[5], "preferred": r[6], "status": r[7], "note": r[8], "my_rating": r[9]} for r in cur}

    def set_flag(self, key: str, field: str, value):
        assert field in ("favorite", "hidden", "preferred", "status", "last_played", "note", "my_rating")
        self._exec(f"INSERT INTO user_games(key, {field}) VALUES(?, ?) "
                   f"ON CONFLICT(key) DO UPDATE SET {field}=excluded.{field}", (key, value))
        self._stamp("game", key, field)

    def mark_started(self, key: str):
        """Partita appena avviata (dal PC, dal telefono o da Moonlight): subito in «Giocati di recente» e «In corso»,
        senza aspettare la fine della sessione (tempo e avvii si contano in record_play)."""
        self._exec("INSERT INTO user_games(key, last_played, status) VALUES(?, ?, 'playing') "
                   "ON CONFLICT(key) DO UPDATE SET last_played=excluded.last_played, "
                   "status=CASE WHEN status IS NULL OR status='' OR status='backlog' THEN 'playing' ELSE status END",
                   (key, time.time()))
        self._stamp("game", key, "last_played", "status")

    def record_play(self, key: str, seconds: float):
        now = time.time()
        with self.lock:
            self.db.execute("INSERT INTO play_sessions VALUES(?, ?, ?)", (key, now - max(0.0, seconds), max(0.0, seconds)))
            # un gioco avviato senza stato diventa "in corso"
            self.db.execute("UPDATE user_games SET status='playing' WHERE key=? AND (status IS NULL OR status='' "
                            "OR status='backlog')", (key,))
            self.db.commit()
        self._exec("INSERT INTO user_games(key, last_played, play_count, playtime) VALUES(?, ?, 1, ?) "
                   "ON CONFLICT(key) DO UPDATE SET last_played=excluded.last_played, "
                   "play_count=play_count+1, playtime=playtime+excluded.playtime",
                   (key, time.time(), max(0.0, seconds)))
        with self.lock:
            self.db.execute("UPDATE user_games SET status='playing' WHERE key=? AND (status IS NULL OR status='')", (key,))
            self.db.commit()
        self._stamp("game", key, "last_played", "status")

    def sessions(self, since: float = 0) -> list[tuple[str, float, float]]:
        return self._q("SELECT key, start, seconds FROM play_sessions WHERE start >= ?", (since,))

    # ------------------------------------------------------------ profili di avvio per gioco
    def profile(self, key: str) -> dict | None:
        import json
        r = self._q("SELECT data FROM game_profiles WHERE key=?", (key,))
        return json.loads(r[0][0]) if r else None

    def set_profile(self, keys: list[str], data: dict | None):
        """Profilo per tutte le versioni del gioco (None = torna alle impostazioni della console)."""
        import json
        with self.lock:
            for k in keys:
                if data:
                    self.db.execute("INSERT OR REPLACE INTO game_profiles VALUES(?, ?)", (k, json.dumps(data)))
                else:
                    self.db.execute("DELETE FROM game_profiles WHERE key=?", (k,))
            self.db.commit()

    def media_overrides(self, key: str) -> dict[str, str]:
        return dict(self._q("SELECT kind, path FROM media_overrides WHERE key=?", (key,)))

    def set_media(self, key: str, kind: str, path: str):
        self._exec("INSERT OR REPLACE INTO media_overrides VALUES(?,?,?)", (key, kind, path))

    # ------------------------------------------------------------ lista dei desideri
    WISH_COLS = ("id", "title", "steam_appid", "itad_id", "target", "added", "price", "regular", "cut", "shop", "url",
                 "low", "currency", "checked", "alert", "seen", "key_price", "key_low", "key_url", "aks", "aks_checked", "cover")

    def wishlist(self) -> list[dict]:
        cur = self._q(f"SELECT {', '.join(self.WISH_COLS)} FROM wishlist ORDER BY title COLLATE NOCASE")
        return [dict(zip(self.WISH_COLS, r)) for r in cur]

    def wish_add(self, wid: str, title: str, steam_appid: str = "", itad_id: str = "", target: float | None = None,
                 cover: str = ""):
        self._exec("INSERT INTO wishlist(id, title, steam_appid, itad_id, target, added, cover) VALUES(?,?,?,?,?,?,?) "
                   "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
                   "steam_appid=COALESCE(NULLIF(excluded.steam_appid,''), steam_appid), "
                   "itad_id=COALESCE(NULLIF(excluded.itad_id,''), itad_id), "
                   "cover=COALESCE(NULLIF(excluded.cover,''), cover)",
                   (wid, title, steam_appid or "", itad_id or "", target, time.time(), cover or ""))
        self._stamp("wish", wid, "")

    def wish_update(self, wid: str, **f):
        f = {k: v for k, v in f.items() if k in self.WISH_COLS and k != "id"}
        if f:
            self._exec(f"UPDATE wishlist SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), wid))
            if f.keys() & {"title", "target"}:  # scelte dell'utente (i prezzi si riscaricano su ogni PC)
                self._stamp("wish", wid, "")

    def wish_delete(self, wid: str):
        self._exec("DELETE FROM wishlist WHERE id=?", (wid,))
        self._exec("DELETE FROM wish_prices WHERE id=?", (wid,))
        self._stamp("wish", wid, DELETED)

    def wish_price_point(self, wid: str, price: float | None, key_price: float | None, day: str | None = None):
        """Un punto al giorno dello storico (il prezzo piu' basso visto quel giorno)."""
        if price is None and key_price is None:
            return
        self._exec("INSERT INTO wish_prices VALUES(?,?,?,?) ON CONFLICT(id, day) DO UPDATE SET "
                   "price=CASE WHEN excluded.price IS NULL THEN price WHEN price IS NULL THEN excluded.price "
                   "ELSE MIN(price, excluded.price) END, "
                   "key_price=CASE WHEN excluded.key_price IS NULL THEN key_price WHEN key_price IS NULL THEN excluded.key_price "
                   "ELSE MIN(key_price, excluded.key_price) END",
                   (wid, day or time.strftime("%Y-%m-%d"), price, key_price))

    def wish_history(self, days: int = 365) -> dict[str, list[tuple[str, float | None, float | None]]]:
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
        out: dict[str, list] = {}
        for wid, day, p, k in self._q("SELECT id, day, price, key_price FROM wish_prices WHERE day >= ? ORDER BY day", (since,)):
            out.setdefault(wid, []).append((day, p, k))
        return out

    # ------------------------------------------------------------ collezioni
    def collections(self) -> dict[str, dict]:
        cur = self._q("SELECT slug, name, custom, hidden FROM user_collections")
        return {r[0]: {"name": r[1], "custom": r[2], "hidden": r[3]} for r in cur}

    def edits(self) -> list[tuple[str, str, str]]:
        return self._q("SELECT slug, key, action FROM collection_edits")

    def create_collection(self, name: str) -> str:
        base = "u-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "collezione")
        slug, i = base, 2
        existing = self.collections()
        while slug in existing:
            slug, i = f"{base}-{i}", i + 1
        self._exec("INSERT INTO user_collections(slug, name, custom, created) VALUES(?, ?, 1, ?)",
                   (slug, name.strip(), time.time()))
        self._stamp("coll", slug, "")
        return slug

    def update_collection(self, slug: str, name: str | None = None, hidden: int | None = None):
        self._exec("INSERT INTO user_collections(slug) VALUES(?) ON CONFLICT(slug) DO NOTHING", (slug,))
        if name is not None:
            self._exec("UPDATE user_collections SET name=? WHERE slug=?", (name.strip(), slug))
        if hidden is not None:
            self._exec("UPDATE user_collections SET hidden=? WHERE slug=?", (int(hidden), slug))
        self._stamp("coll", slug, "")

    def delete_collection(self, slug: str):
        with self.lock:
            self.db.execute("DELETE FROM user_collections WHERE slug=? AND custom=1", (slug,))
            self.db.execute("DELETE FROM collection_edits WHERE slug=?", (slug,))
            self.db.commit()
        self._stamp("coll", slug, DELETED)

    # ------------------------------------------------------------ giochi PC aggiunti a mano
    def manual_games(self) -> list[dict]:
        import json
        cur = self._q("SELECT id, title, exe, args, cwd, kind FROM manual_games ORDER BY title")
        return [{"id": r[0], "title": r[1], "exe": r[2], "args": json.loads(r[3] or "[]"), "cwd": r[4],
                 "kind": r[5] or "game"} for r in cur]

    def add_manual(self, title: str, exe: str, args: list[str], cwd: str = "", kind: str = "game") -> int:
        """Lo stesso exe con gli stessi argomenti non si aggiunge due volte: si riusa la voce esistente."""
        import json
        with self.lock:
            row = self.db.execute("SELECT id FROM manual_games WHERE lower(exe)=lower(?) AND args=?",
                                  (exe, json.dumps(args))).fetchone()
            if row:
                return row[0]
            cur = self.db.execute("INSERT INTO manual_games(title, exe, args, cwd, added, kind) VALUES(?,?,?,?,?,?)",
                                  (title.strip(), exe, json.dumps(args), cwd, time.time(), kind))
            self.db.commit()
            return cur.lastrowid

    def dedupe_manual(self) -> int:
        """Tiene la voce piu' vecchia per ogni exe+argomenti e cancella le altre. Ritorna quante ne ha tolte."""
        with self.lock:
            ids = [r[0] for r in self.db.execute(
                "SELECT id FROM manual_games WHERE id NOT IN "
                "(SELECT MIN(id) FROM manual_games GROUP BY lower(exe), args)")]
            for i in ids:
                self.db.execute("DELETE FROM manual_games WHERE id=?", (i,))
            self.db.commit()
        return len(ids)

    def delete_manual(self, mid: int):
        self._exec("DELETE FROM manual_games WHERE id=?", (mid,))

    def edit_membership(self, slug: str, key: str, action: str):
        assert action in ("add", "remove")
        self._exec("INSERT OR REPLACE INTO collection_edits VALUES(?, ?, ?)", (slug, key, action))
        self._stamp("edit", f"{slug}	{key}", "")
