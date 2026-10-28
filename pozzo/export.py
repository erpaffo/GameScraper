"""Esportazione per console: games.json, games.csv, missing.txt e cartella media/.

I media vengono collegati con hardlink dalla cache (istantaneo e senza occupare spazio extra
se cache e output sono sullo stesso disco), altrimenti copiati. Non c'e' compositing:
le immagini sono quelle originali delle sorgenti.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import ALL_MEDIA
from .scanner import Game

COLUMNS = ["file", "title", "platform", "release_date", "developer", "publisher", "genres", "players",
           "rating", "region", "ages", "description", "crc", "canonical", "video_url"]


def _retry(fn, tries: int = 5):
    """Su Windows un file appena scritto puo' essere bloccato per qualche istante
    (OneDrive, antivirus, indicizzazione): si riprova con attese crescenti."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.2 * 2 ** i)


def _place(src: Path, dst: Path, link: bool) -> str | None:
    """Mette un media nell'output (hardlink o copia). Ritorna un messaggio d'errore o None."""
    try:
        if dst.exists():
            if dst.stat().st_size == src.stat().st_size:
                return None
            _retry(dst.unlink)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if link:
            try:
                os.link(src, dst)
                return None
            except OSError:
                pass
        tmp = dst.with_name(dst.name + ".tmp")
        _retry(lambda: shutil.copyfile(src, tmp))
        _retry(lambda: os.replace(tmp, dst))
        return None
    except OSError as e:
        return f"{dst.name}: {e.strerror or e}"


def _write_text(path: Path, text: str, log):
    try:
        _retry(lambda: path.write_text(text, encoding="utf-8"))
    except PermissionError:
        log(f"[red]Impossibile scrivere {path}: e' aperto in un altro programma (es. Excel)? "
            f"Chiudilo e rilancia 'export'.[/]")


def export(cfg, cache, games: list[Game], log) -> dict:
    by_plat: dict[str, list[Game]] = defaultdict(list)
    for g in games:
        by_plat[g.platform.id].append(g)
    summary = {}
    jobs: dict[Path, Path] = {}  # destinazione -> sorgente: ogni file di output scritto UNA volta
    for pid, lst in sorted(by_plat.items()):
        out = cfg.output / pid
        out.mkdir(parents=True, exist_ok=True)
        records, missing = [], []
        for g in sorted(lst, key=lambda x: x.path.name.lower()):
            d = g.data
            found = "title" in g.origin
            rec = {
                "file": g.path.name,
                "path": str(g.path),
                "platform": pid,
                "found": found,
                "title": d.get("title") or g.search_name,
                **{k: d.get(k) for k in ("release_date", "developer", "publisher", "genres", "players",
                                         "rating", "region", "ages", "description", "video_url")},
                "crc": g.best_crc, "canonical": g.canonical,
                "media": {}, "sources": dict(g.origin),
            }
            for m in ALL_MEDIA:
                rel = d.get(f"media:{m}")
                if rel and m in cfg.media:
                    src = cache.folder / rel
                    if src.exists():
                        dst = out / "media" / m / (g.stem + src.suffix)
                        # stesso gioco presente due volte nella console (es. cartella HITS + set completo)
                        jobs.setdefault(dst, src)
                        rec["media"][m] = dst.relative_to(out).as_posix()
            records.append(rec)
            if not found:
                missing.append(g.path.name)

        _write_text(out / "games.json", json.dumps(records, ensure_ascii=False, indent=1), log)
        buf = io.StringIO(newline="")
        buf.write("﻿")  # BOM: Excel riconosce l'UTF-8 (accenti corretti)
        w = csv.writer(buf, delimiter=";")
        w.writerow(COLUMNS + [f"media_{m}" for m in cfg.media])
        for r in records:
            row = []
            for c in COLUMNS:
                v = r.get(c)
                row.append("; ".join(v) if isinstance(v, list) else ("" if v is None else v))
            w.writerow(row + [r["media"].get(m, "") for m in cfg.media])
        _write_text(out / "games.csv", buf.getvalue(), log)
        _write_text(out / "missing.txt", "\n".join(missing), log)
        media_count = {m: sum(1 for r in records if m in r["media"]) for m in cfg.media}
        summary[pid] = {"games": len(records), "found": len(records) - len(missing), **media_count}

    with ThreadPoolExecutor(16) as ex:
        errors = [e for e in ex.map(lambda d: _place(jobs[d], d, cfg.link_media), jobs) if e]
    if errors:
        log(f"[yellow]{len(errors)} media non copiati nell'output (rilancia 'export' per riprovare):[/]")
        for e in errors[:10]:
            log(f"  [yellow]{e}[/]")
    _write_text(cfg.output / "summary.json", json.dumps(summary, indent=1), log)
    return summary
