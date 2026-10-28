"""Metadati dei giochi degli store PC presi SOLO dalle copie offline (LaunchBox, TheGamesDB).

Nessuna richiesta alle API online: si consultano i database locali gia' scaricati.
Il risultato di ogni gioco resta in cache ("storemeta:<chiave>") e viene usato dalla libreria.
"""

from __future__ import annotations

import re

from ..bulk import LaunchBoxDB
from ..bulk.tgdb import TgdbDB


def _key(s: dict) -> str:
    return f"windows/{s['store']}:{s['id']}"


def search(cfg, cache, log=print, progress=None, only_missing: bool = False, cancelled=lambda: False,
           only_keys: set | None = None) -> dict:
    from . import load_snapshots
    snaps = [s for s in load_snapshots(cache)
             if not (not s.get("installed") and re.fullmatch(r"[0-9a-fA-F]{16,}", s.get("title") or s["id"]))]
    if only_keys is not None:  # solo questi giochi (chiavi come in _key)
        snaps = [s for s in snaps if _key(s) in only_keys]
    if only_missing:
        snaps = [s for s in snaps if not (cache.kv_get("storemeta:" + _key(s)) or {}).get("found")]
    lb, tg = LaunchBoxDB(cfg.cache), TgdbDB(cfg.cache)
    names = {k: {str(a): (b.get("name") if isinstance(b, dict) else b) for a, b in (cache.kv_get(f"tgdb_{k}") or {}).items()}
             for k in ("genres", "developers", "publishers")}
    if not lb.ready() and not tg.ready():
        log("Nessun database offline disponibile: scarica LaunchBox o TheGamesDB da Impostazioni › Fonti")
        return {"games": 0, "found": 0}
    log(f"Database offline: {'LaunchBox ' if lb.ready() else ''}{'TheGamesDB' if tg.ready() else ''}")
    task = progress.add_task("Metadati giochi PC (offline)", total=len(snaps)) if progress else None
    found = 0
    try:
        for s in snaps:
            if cancelled():
                log("Interrotto")
                break
            title = s.get("title") or s["id"]
            m: dict = {}
            if lb.ready():
                r = lb.find("windows", [title], steam_appid=s["id"] if s["store"] == "steam" else None, fuzzy=92)
                if r:
                    m = {"source": "LaunchBox", "description": r["overview"], "developer": r["developer"],
                         "publisher": r["publisher"], "release_date": r["release"], "players": r["players"],
                         "genres": [x.strip() for x in (r["genres"] or "").split(";") if x.strip()]}
                    if r["rating"] is not None and r["rating_count"]:
                        m["rating"] = {"rating": r["rating"] / 5, "rating_count": r["rating_count"]}
            if tg.ready() and (not m or not m.get("description")):
                r = tg.find("windows", [title])
                if r:
                    ids = lambda k: [names[k][i] for i in re.findall(r"\d+", r[k] or "") if names[k].get(i)]  # noqa: E731
                    t = {"description": r["overview"], "developer": ", ".join(ids("developers")),
                         "publisher": ", ".join(ids("publishers")), "release_date": r["release"], "genres": ids("genres"),
                         "players": r["players"]}
                    for k, v in t.items():
                        if v and not m.get(k):
                            m[k] = v
                    m["source"] = (m.get("source", "") + " + TheGamesDB").lstrip(" +")
            m = {k: v for k, v in m.items() if v not in (None, "", [])}
            m["found"] = bool(m.get("source"))
            found += m["found"]
            cache.kv_set("storemeta:" + _key(s), m)
            if task is not None:
                progress.advance(task)
    finally:
        lb.close()
        tg.close()
    log(f"Trovati {found:,} giochi su {len(snaps):,}")
    return {"games": len(snaps), "found": found}
