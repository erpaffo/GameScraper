"""Versione installata, changelog e controllo degli aggiornamenti (ultima release su GitHub).

Il controllo fa al massimo una richiesta ogni 12 ore (risposta in cache.db) e non scarica nulla:
dice solo se c'e' una versione piu' nuova e dove trovarla.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx

from . import __version__

REPO = "erpaffo/GameScraper"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
TTL = 12 * 3600


def parse(v: str) -> tuple[int, ...]:
    """'v0.2.1' -> (0, 2, 1); parti non numeriche (es. '-dev') ignorate."""
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("-")[0])[:3]) or (0,)


def changelog() -> str:
    """changelog.md accanto al pacchetto (sorgenti) o dentro l'eseguibile (PyInstaller)."""
    bases = [Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parent.parent]
    for b in bases:
        p = b / "changelog.md"
        if str(b) and p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


def check(cache, force: bool = False) -> dict:
    hit = cache.kv_get("update_check")
    if not hit or force or time.time() - hit.get("time", 0) > TTL:
        try:
            r = httpx.get(LATEST, timeout=httpx.Timeout(10, connect=5), follow_redirects=True,
                          headers={"Accept": "application/vnd.github+json", "User-Agent": f"GameScraper/{__version__}"})
            if r.status_code == 404:  # nessuna release pubblicata
                hit = {"time": time.time(), "latest": None}
            else:
                r.raise_for_status()
                j = r.json()
                hit = {"time": time.time(), "latest": j.get("tag_name"), "url": j.get("html_url"),
                       "published": j.get("published_at"), "notes": (j.get("body") or "")[:4000]}
            cache.kv_set("update_check", hit)
        except (httpx.HTTPError, ValueError) as e:
            return {"current": __version__, "error": f"{type(e).__name__}: {e}"[:200], **(hit or {})}
    latest = hit.get("latest")
    return {"current": __version__, **hit, "newer": bool(latest) and parse(latest) > parse(__version__)}
