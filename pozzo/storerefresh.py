"""Aggiornamento di tutti gli store (PC e console): elenchi dei giochi, in parallelo.

Gli elenchi letti finiscono in `cache.db` (chiavi `catalog:*`) e la libreria si ricostruisce da li':
per questo all'avvio i giochi degli store ci sono gia' e l'aggiornamento puo' girare in sottofondo.

Regole dello schema di parallelismo:
- si lavora **in parallelo fra siti diversi** (un worker per gruppo di host);
- gli elenchi dello **stesso sito** restano in sequenza, per non moltiplicare le richieste a un server solo;
- i gruppi sono bilanciati per numero di elenchi, cosi' i worker finiscono piu' o meno insieme.
"""

from __future__ import annotations

from urllib.parse import urlsplit

DEFAULT_WORKERS = 4
MAX_WORKERS = 8
MODES = ("startup", "daily", "manual")


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def label(url: str) -> str:
    """Etichetta breve per l'interfaccia: sito più ultimo pezzo di percorso."""
    parts = urlsplit(url)
    h = (parts.hostname or "").removeprefix("www.")
    tail = [p for p in (parts.path or "").split("/") if p]
    return f"{h} · {tail[-1]}" if tail else h


def settings(cfg) -> dict:
    """Impostazione dell'aggiornamento automatico: modalita', orario e quanti store in parallelo."""
    raw = (getattr(cfg, "app", None) or {}).get("store_schedule") or {}
    mode = str(raw.get("mode") or "startup").strip().lower()
    if mode not in MODES:
        mode = "startup"
    when = str(raw.get("time") or "03:00").strip()
    try:
        hh, mm = (int(x) for x in when.split(":")[:2])
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
        when = f"{hh:02d}:{mm:02d}"
    except ValueError:
        when = "03:00"
    try:
        workers = int(raw.get("workers") or DEFAULT_WORKERS)
    except (TypeError, ValueError):
        workers = DEFAULT_WORKERS
    return {"mode": mode, "time": when, "workers": max(1, min(MAX_WORKERS, workers))}


def targets(cfg) -> list[dict]:
    """Tutti gli elenchi configurati: store PC (repack/direct) e cataloghi delle console."""
    from . import platforms as P

    out: list[dict] = []
    for it in (getattr(cfg, "pc_stores", None) or []):
        url = str((it or {}).get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        kind = str(it.get("kind") or "direct").lower()
        out.append({"id": f"windows:{url}", "platform": "windows", "url": url,
                    "kind": kind if kind in ("repack", "direct") else "direct",
                    "label": label(url), "group": "pc"})
    for pid, urls in (getattr(cfg, "catalogs", None) or {}).items():
        name = getattr(P.PLATFORMS.get(pid), "name", pid)
        for url in urls or []:
            url = str(url).strip()
            if not url.startswith(("http://", "https://")):
                continue
            out.append({"id": f"{pid}:{url}", "platform": pid, "url": url, "kind": None,
                        "label": f"{name} · {label(url)}", "group": "console"})
    return out


def groups(items: list[dict], workers: int = DEFAULT_WORKERS) -> list[list[dict]]:
    """Raggruppa gli elenchi in blocchi da dare ai worker (stesso host sempre nello stesso blocco)."""
    by_host: dict[str, list[dict]] = {}
    for it in items:
        by_host.setdefault(host(it["url"]), []).append(it)
    if not by_host:
        return []
    ordered = sorted(by_host.values(), key=len, reverse=True)
    n = max(1, min(int(workers or 1), len(ordered)))
    buckets: list[list[dict]] = [[] for _ in range(n)]
    for group in ordered:  # gruppi piu' grandi prima, poi si riempie il blocco piu' scarico
        min(buckets, key=len).extend(group)
    return [b for b in buckets if b]
