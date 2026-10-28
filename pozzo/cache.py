"""Cache delle risorse su SQLite (WAL).

Sostituisce db.xml + quickid.xml di Skyscraper: quelli venivano tenuti interamente in RAM
e riscritti solo a fine esecuzione (un crash = tutto perso). Qui ogni risultato viene
salvato subito (commit a blocchi), quindi un'esecuzione interrotta riprende da dove era.

Tabelle:
  files      path -> hash calcolati (evita di ri-hashare file invariati)
  resources  (game_key, source, field) -> valore   (media: percorso relativo del file in cache)
  lookups    (game_key, source) -> found / notfound  (non ri-interroga una sorgente gia' provata)
  kv         chiavi varie (token, mappe piattaforme, ...)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INT, mtime REAL, data TEXT);
CREATE TABLE IF NOT EXISTS resources(key TEXT, source TEXT, field TEXT, value TEXT, ts REAL,
                                     PRIMARY KEY(key, source, field));
CREATE TABLE IF NOT EXISTS lookups(key TEXT, source TEXT, status TEXT, ts REAL, PRIMARY KEY(key, source));
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT, ts REAL);
"""


class Cache:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.media_dir = self.folder / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        # timeout: app e riga di comando possono scrivere insieme (es. scraping in corso + aggiornamento store)
        self.db = sqlite3.connect(self.folder / "cache.db", timeout=60)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self._pending = 0
        self._last_commit = time.monotonic()

    # ------------------------------------------------------------ commit a blocchi
    def _touch(self, immediate: bool = True):
        # I risultati si salvano subito: una transazione lasciata aperta mentre si aspetta la rete
        # bloccherebbe le scritture degli altri processi (es. app + riga di comando). Con WAL e
        # synchronous=NORMAL un commit costa meno di un millisecondo. Gli hash dei file, scritti
        # a migliaia durante la scansione, restano a blocchi.
        self._pending += 1
        if immediate or self._pending >= 500 or time.monotonic() - self._last_commit > 3:
            self.commit()

    def commit(self):
        self.db.commit()
        self._pending = 0
        self._last_commit = time.monotonic()

    def close(self):
        self.commit()
        self.db.close()

    # ------------------------------------------------------------ files
    def get_file(self, path: str, size: int, mtime: float) -> dict | None:
        r = self.db.execute("SELECT size, mtime, data FROM files WHERE path=?", (path,)).fetchone()
        if r and r[0] == size and abs(r[1] - mtime) < 1:
            return json.loads(r[2])
        return None

    def put_file(self, path: str, size: int, mtime: float, data: dict):
        self.db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?)", (path, size, mtime, json.dumps(data)))
        self._touch(immediate=False)

    # ------------------------------------------------------------ resources
    def put(self, key: str, source: str, field: str, value):
        if value in (None, "", [], {}):
            return
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        self.db.execute("INSERT OR REPLACE INTO resources VALUES(?,?,?,?,?)", (key, source, field, value, time.time()))
        self._touch()

    def resources(self, key: str) -> dict[str, dict[str, str]]:
        """source -> {field: value}"""
        out: dict[str, dict[str, str]] = {}
        for source, field, value in self.db.execute(
                "SELECT source, field, value FROM resources WHERE key=?", (key,)):
            out.setdefault(source, {})[field] = value
        return out

    def all_resources(self, keys: list[str]) -> dict[str, dict[str, dict[str, str]]]:
        out: dict[str, dict[str, dict[str, str]]] = {}
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            q = f"SELECT key, source, field, value FROM resources WHERE key IN ({','.join('?' * len(chunk))})"
            for key, source, field, value in self.db.execute(q, chunk):
                out.setdefault(key, {}).setdefault(source, {})[field] = value
        return out

    def purge(self, source: str | None = None, platform: str | None = None):
        cond, args = [], []
        if source:
            cond.append("source=?")
            args.append(source)
        if platform:
            cond.append("key LIKE ?")
            args.append(platform + "/%")
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        self.db.execute("DELETE FROM resources" + where, args)
        self.db.execute("DELETE FROM lookups" + where, args)
        self.commit()

    # ------------------------------------------------------------ lookups
    def lookup_status(self, keys: list[str], source: str) -> dict[str, str]:
        out = {}
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            q = f"SELECT key, status FROM lookups WHERE source=? AND key IN ({','.join('?' * len(chunk))})"
            out.update(dict(self.db.execute(q, [source, *chunk])))
        return out

    def set_lookup(self, key: str, source: str, status: str):
        self.db.execute("INSERT OR REPLACE INTO lookups VALUES(?,?,?,?)", (key, source, status, time.time()))
        self._touch()

    # ------------------------------------------------------------ kv
    def kv_get(self, k: str, max_age: float | None = None):
        r = self.db.execute("SELECT v, ts FROM kv WHERE k=?", (k,)).fetchone()
        if not r or (max_age is not None and time.time() - r[1] > max_age):
            return None
        return json.loads(r[0])

    def kv_set(self, k: str, v):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?,?)", (k, json.dumps(v), time.time()))
        self.commit()

    # ------------------------------------------------------------ stats
    def stats(self) -> list[tuple]:
        return self.db.execute(
            "SELECT substr(key,1,instr(key,'/')-1) AS plat, source, COUNT(DISTINCT key), COUNT(*) "
            "FROM resources GROUP BY plat, source ORDER BY plat, source").fetchall()
