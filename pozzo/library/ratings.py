"""Valutazione combinata da piu' fonti.

Ogni fonte da' un voto 0..1 e (quasi sempre) il numero di voti. Il voto finale e' una media pesata
sul numero di voti, con un piccolo "voto a priori" (media bayesiana) perche' pochi voti contino poco:
  voto = (sum(r_i * w_i) + PRIOR * M) / (sum(w_i) + M)
Il voto della critica (Metacritic via RAWG, aggregato critico di IGDB) resta separato.
"""

from __future__ import annotations

import json

# peso usato quando la fonte non dice quanti voti ha (es. ScreenScraper)
DEFAULT_WEIGHT = {"screenscraper": 25, "thegamesdb": 10}
NAMES = {"launchbox": "LaunchBox", "igdb": "IGDB", "screenscraper": "ScreenScraper", "rawg": "RAWG",
         "steam": "Recensioni Steam", "thegamesdb": "TheGamesDB"}
PRIOR, M = 0.6, 8  # media a priori e peso del "voto fantasma"
MAX_W = 5000


def _f(v):
    try:
        return float(json.loads(v)) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return None


def combine(per_source: dict[str, dict]) -> dict:
    """per_source: {fonte: {"rating": 0..1, "rating_count": n, "rating_critics": 0..1}}"""
    parts, critics = [], []
    for src, d in per_source.items():
        r = _f(d.get("rating"))
        if r is not None and 0 <= r <= 1:
            n = int(_f(d.get("rating_count")) or 0)
            w = min(n, MAX_W) if n else DEFAULT_WEIGHT.get(src, 10)
            parts.append({"source": src, "name": NAMES.get(src, src), "rating": round(r, 3), "count": n, "w": w})
        c = _f(d.get("rating_critics"))
        if c is not None and 0 < c <= 1:
            critics.append((src, c))
    if not parts and not critics:
        return {}
    out = {"ratings": [{k: p[k] for k in ("source", "name", "rating", "count")} for p in parts]}
    if parts:
        sw = sum(p["w"] for p in parts)
        out["rating"] = round((sum(p["rating"] * p["w"] for p in parts) + PRIOR * M) / (sw + M), 3)
        out["rating_count"] = sum(p["count"] for p in parts)
    if critics:
        # Metacritic (RAWG) ha la precedenza sull'aggregato critico di IGDB
        critics.sort(key=lambda x: 0 if x[0] == "rawg" else 1)
        out["rating_critics"] = round(critics[0][1], 3)
    return out
