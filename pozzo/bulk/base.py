"""Database scaricati in blocco: un download ogni tanto, poi tutte le ricerche sono locali.

E' il modo piu' rispettoso (e piu' veloce) di ottenere dati per decine di migliaia di giochi:
una sola richiesta al sito invece di una per gioco. Il download avviene solo se il file e'
cambiato (ETag / Last-Modified) e al massimo ogni `refresh_days`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path


class BulkDB:
    id = "bulk"
    name = "Database"
    url = ""
    filename = "data.bin"
    refresh_days = 7
    description = ""

    def __init__(self, cache_folder):
        self.dir = Path(cache_folder) / "bulk"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / f"{self.id}.db"
        self.meta_path = self.dir / f"{self.id}.json"
        self._con: sqlite3.Connection | None = None

    # ------------------------------------------------------------ stato
    def meta(self) -> dict:
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def ready(self) -> bool:
        return self.db_path.exists()

    def status(self) -> dict:
        m = self.meta()
        return {"id": self.id, "name": self.name, "description": self.description, "ready": self.ready(),
                "updated": m.get("indexed", 0), "remote_date": m.get("last_modified", ""), "games": m.get("games", 0),
                "size_mb": round(self.db_path.stat().st_size / 2**20, 1) if self.ready() else 0,
                "refresh_days": self.refresh_days}

    def stale(self) -> bool:
        return not self.ready() or time.time() - self.meta().get("checked", 0) > self.refresh_days * 86400

    # ------------------------------------------------------------ download + indice
    async def ensure(self, client, log=print, force: bool = False) -> bool:
        """Scarica e indicizza se serve. Ritorna True se il database e' pronto."""
        if not force and not self.stale():
            return True
        m = self.meta()
        headers = {}
        if self.ready() and not force:
            if m.get("etag"):
                headers["If-None-Match"] = m["etag"]
            if m.get("last_modified"):
                headers["If-Modified-Since"] = m["last_modified"]
        raw = self.dir / self.filename
        part = raw.with_suffix(raw.suffix + ".part")
        try:
            log(f"{self.name}: controllo aggiornamenti…")
            async with client.stream("GET", self.url, headers=headers, timeout=None) as r:
                if r.status_code == 304:
                    m["checked"] = time.time()
                    self._save_meta(m)
                    log(f"{self.name}: gia' aggiornato")
                    return True
                if r.status_code != 200:
                    log(f"{self.name}: download non riuscito (HTTP {r.status_code})")
                    return self.ready()
                total = int(r.headers.get("content-length") or 0)
                done, last = 0, 0.0
                with open(part, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 3:
                            last = time.time()
                            log(f"{self.name}: scaricati {done / 2**20:.0f}" + (f" / {total / 2**20:.0f} MB" if total else " MB"))
                m.update({"etag": r.headers.get("etag", ""), "last_modified": r.headers.get("last-modified", "")})
            os.replace(part, raw)
        except Exception as e:
            part.unlink(missing_ok=True)
            log(f"{self.name}: errore nel download: {type(e).__name__}: {e}")
            return self.ready()
        log(f"{self.name}: indicizzazione (una volta sola, circa un minuto)…")
        tmp = self.db_path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        n = await asyncio.to_thread(self.build_index, raw, tmp, log)
        self.close()
        os.replace(tmp, self.db_path)
        if not getattr(self, "keep_raw", False):
            raw.unlink(missing_ok=True)
        m.update({"checked": time.time(), "indexed": time.time(), "games": n})
        self._save_meta(m)
        log(f"{self.name}: {n:,} giochi pronti per la ricerca locale")
        return True

    def _save_meta(self, m: dict):
        self.meta_path.write_text(json.dumps(m), encoding="utf-8")

    def build_index(self, raw: Path, out: Path, log) -> int:
        raise NotImplementedError

    # ------------------------------------------------------------ lettura
    def con(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        return self._con

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None
