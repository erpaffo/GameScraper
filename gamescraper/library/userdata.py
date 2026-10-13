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
CREATE TABLE IF NOT EXISTS game_profiles(key TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS manual_games(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, exe TEXT, args TEXT,
                                        cwd TEXT, added REAL);
"""


class UserData:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(user_games)")}
        if "status" not in cols:  # stato del gioco: backlog / playing / done / dropped
            self.db.execute("ALTER TABLE user_games ADD COLUMN status TEXT")
            self.db.commit()
        self.lock = threading.Lock()

    def _q(self, sql, args=()) -> list[tuple]:
        """Lettura protetta dal lock: la connessione e' condivisa tra i thread del server."""
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def _exec(self, sql, args=()):
        with self.lock:
            self.db.execute(sql, args)
            self.db.commit()

    # ------------------------------------------------------------ giochi
    def games(self) -> dict[str, dict]:
        cur = self._q("SELECT key, favorite, hidden, last_played, play_count, playtime, preferred, status "
                              "FROM user_games")
        return {r[0]: {"favorite": r[1], "hidden": r[2], "last_played": r[3], "play_count": r[4],
                       "playtime": r[5], "preferred": r[6], "status": r[7]} for r in cur}

    def set_flag(self, key: str, field: str, value):
        assert field in ("favorite", "hidden", "preferred", "status")
        self._exec(f"INSERT INTO user_games(key, {field}) VALUES(?, ?) "
                   f"ON CONFLICT(key) DO UPDATE SET {field}=excluded.{field}", (key, value))

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

    # ------------------------------------------------------------ lista dei desideri
    WISH_COLS = ("id", "title", "steam_appid", "itad_id", "target", "added", "price", "regular", "cut", "shop", "url",
                 "low", "currency", "checked", "alert", "seen")

    def wishlist(self) -> list[dict]:
        cur = self._q(f"SELECT {', '.join(self.WISH_COLS)} FROM wishlist ORDER BY title COLLATE NOCASE")
        return [dict(zip(self.WISH_COLS, r)) for r in cur]

    def wish_add(self, wid: str, title: str, steam_appid: str = "", itad_id: str = "", target: float | None = None):
        self._exec("INSERT INTO wishlist(id, title, steam_appid, itad_id, target, added) VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
                   "steam_appid=COALESCE(NULLIF(excluded.steam_appid,''), steam_appid), "
                   "itad_id=COALESCE(NULLIF(excluded.itad_id,''), itad_id)",
                   (wid, title, steam_appid or "", itad_id or "", target, time.time()))

    def wish_update(self, wid: str, **f):
        f = {k: v for k, v in f.items() if k in self.WISH_COLS and k != "id"}
        if f:
            self._exec(f"UPDATE wishlist SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), wid))

    def wish_delete(self, wid: str):
        self._exec("DELETE FROM wishlist WHERE id=?", (wid,))

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
        return slug

    def update_collection(self, slug: str, name: str | None = None, hidden: int | None = None):
        self._exec("INSERT INTO user_collections(slug) VALUES(?) ON CONFLICT(slug) DO NOTHING", (slug,))
        if name is not None:
            self._exec("UPDATE user_collections SET name=? WHERE slug=?", (name.strip(), slug))
        if hidden is not None:
            self._exec("UPDATE user_collections SET hidden=? WHERE slug=?", (int(hidden), slug))

    def delete_collection(self, slug: str):
        with self.lock:
            self.db.execute("DELETE FROM user_collections WHERE slug=? AND custom=1", (slug,))
            self.db.execute("DELETE FROM collection_edits WHERE slug=?", (slug,))
            self.db.commit()

    # ------------------------------------------------------------ giochi PC aggiunti a mano
    def manual_games(self) -> list[dict]:
        import json
        cur = self._q("SELECT id, title, exe, args, cwd FROM manual_games ORDER BY title")
        return [{"id": r[0], "title": r[1], "exe": r[2], "args": json.loads(r[3] or "[]"), "cwd": r[4]} for r in cur]

    def add_manual(self, title: str, exe: str, args: list[str], cwd: str = "") -> int:
        import json
        with self.lock:
            cur = self.db.execute("INSERT INTO manual_games(title, exe, args, cwd, added) VALUES(?,?,?,?,?)",
                                  (title.strip(), exe, json.dumps(args), cwd, time.time()))
            self.db.commit()
            return cur.lastrowid

    def delete_manual(self, mid: int):
        self._exec("DELETE FROM manual_games WHERE id=?", (mid,))

    def edit_membership(self, slug: str, key: str, action: str):
        assert action in ("add", "remove")
        self._exec("INSERT OR REPLACE INTO collection_edits VALUES(?, ?, ?)", (slug, key, action))
