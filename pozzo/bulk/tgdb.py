"""TheGamesDB, copia completa (cdn.thegamesdb.net/json/database-latest.json, ~150 MB, 121.000 giochi).

Sostituisce le API di TheGamesDB, che hanno una quota mensile molto bassa: un download al mese e poi
nessuna richiesta. Generi/sviluppatori/editori sono codici numerici: i nomi si leggono dalle tabelle
gia' in cache (scaricate una volta dalle API, 3 richieste in tutto).
"""

from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path

from .. import platforms as P
from ..matching import normalize
from ..platforms import norm as pnorm
from .base import BulkDB

SCHEMA = """
CREATE TABLE games(id INTEGER PRIMARY KEY, pid TEXT, title TEXT, norm TEXT, release TEXT, overview TEXT, players TEXT,
                   esrb TEXT, youtube TEXT, developers TEXT, publishers TEXT, genres TEXT, cover TEXT);
CREATE TABLE alt(norm TEXT, id INT);
"""


def platform_map(platforms: dict) -> dict[str, str]:
    """id piattaforma TheGamesDB -> id PozzoLauncher (per id noto o per nome/alias)."""
    by_tgdb = {str(p.tgdb): p.id for p in P.PLATFORMS.values() if p.tgdb}
    by_name = {}
    for p in P.PLATFORMS.values():
        for n in (p.name, p.id, *p.aliases):
            by_name.setdefault(pnorm(n), p.id)
    out = {}
    for pid, info in platforms.items():
        if str(pid) in by_tgdb:
            out[str(pid)] = by_tgdb[str(pid)]
            continue
        for n in (info.get("name", ""), info.get("alias", "")):
            if pnorm(n) in by_name:
                out[str(pid)] = by_name[pnorm(n)]
                break
    return out


class TgdbDB(BulkDB):
    id = "tgdb"
    name = "TheGamesDB (copia completa)"
    url = "https://cdn.thegamesdb.net/json/database-latest.json"
    filename = "tgdb.json"
    refresh_days = 30
    description = "121.000 giochi con descrizioni, giocatori, ESRB e copertine, senza quota"

    def build_index(self, raw: Path, out: Path, log) -> int:
        d = json.loads(raw.read_text(encoding="utf-8"))
        inc = d.get("include", {})
        plats = (inc.get("platform") or {}).get("data") or {}
        pmap = platform_map(plats if isinstance(plats, dict) else {})
        box = inc.get("boxart") or {}
        base = ((box.get("base_url") or {}).get("original")) or "https://cdn.thegamesdb.net/images/original/"
        covers = {}
        for gid, imgs in (box.get("data") or {}).items():
            for im in imgs or []:
                if im.get("type") == "boxart" and im.get("side") == "front":
                    covers[str(gid)] = base + im["filename"]
                    break
        db = sqlite3.connect(out)
        db.executescript(SCHEMA)
        rows, alts = [], []
        for g in d.get("data", {}).get("games", []):
            gid = str(g.get("id"))
            title = html.unescape(g.get("game_title") or "")
            pid = pmap.get(str(g.get("platform")))
            rows.append((int(gid), pid, title, normalize(title), g.get("release_date"), html.unescape(g.get("overview") or ""),
                         str(g.get("players") or "") or None, g.get("rating"), g.get("youtube"),
                         str(g.get("developers") or ""), str(g.get("publishers") or ""), str(g.get("genres") or ""),
                         covers.get(gid)))
            for a in (g.get("alternates") or []) if isinstance(g.get("alternates"), list) else []:
                alts.append((normalize(a), int(gid)))
        db.executemany("INSERT OR IGNORE INTO games VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.executemany("INSERT INTO alt VALUES(?,?)", alts)
        db.executescript("CREATE INDEX g ON games(pid, norm); CREATE INDEX a ON alt(norm);")
        db.commit()
        db.close()
        return len(rows)

    def find(self, pid: str, titles: list[str]) -> dict | None:
        c = self.con()
        for t in titles:
            n = normalize(t or "")
            if not n:
                continue
            r = c.execute("SELECT * FROM games WHERE pid=? AND norm=?", (pid, n)).fetchone() or \
                c.execute("SELECT g.* FROM alt a JOIN games g ON g.id=a.id WHERE a.norm=? AND g.pid=?", (n, pid)).fetchone()
            if r:
                cols = [x[0] for x in c.execute("SELECT * FROM games LIMIT 0").description]
                return dict(zip(cols, r))
        return None
