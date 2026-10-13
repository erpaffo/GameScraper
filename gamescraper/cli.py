"""Interfaccia a riga di comando.

  python -m gamescraper run        scansione + scraping + export (tutte le console trovate)
  python -m gamescraper scrape     solo scraping in cache
  python -m gamescraper export     solo export da cache (nessuna rete)
  python -m gamescraper stats      statistiche della cache
  python -m gamescraper platforms  elenco piattaforme e alias di cartella
  python -m gamescraper purge      cancella dati in cache (per sorgente / piattaforma)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from . import __version__, config as C, platforms as P
from .cache import Cache
from .export import export
from .pipeline import load_cached, scrape
from .scanner import find_platform_dirs, scan
from .sources import SOURCES

console = Console(highlight=False)


def _progress() -> Progress:
    return Progress(TextColumn("[bold]{task.description:<22}"), BarColumn(), MofNCompleteColumn(),
                    TimeElapsedColumn(), TextColumn("ETA"), TimeRemainingColumn(), console=console)


def _targets(cfg, args):
    targets = find_platform_dirs(cfg)
    if args.platform:
        want = {P.get(x.strip()).id for x in args.platform.split(",")}
        targets = [t for t in targets if t[0].id in want]
    if not targets:
        console.print(f"[red]Nessuna cartella console riconosciuta in {cfg.roms}.[/] "
                      "Le sottocartelle devono chiamarsi come la piattaforma (es. snes, psx, megadrive) "
                      "oppure vanno mappate in [platform_folders] nel config.")
        sys.exit(1)
    return targets


def cmd_scan_and(args, do_scrape: bool, do_export: bool):
    cfg = C.load(args.config)
    if args.roms:
        cfg.roms = Path(args.roms)
    if args.media:
        cfg.media = [] if args.media == "none" else [m.strip() for m in args.media.split(",")]
    cache = Cache(cfg.cache)
    t0 = time.time()
    try:
        targets = _targets(cfg, args)
        for p, folder in targets:
            rel = folder.relative_to(cfg.roms) if folder.is_relative_to(cfg.roms) else folder
            console.print(f"  [green]{p.id:<14}[/] {rel}")
        with _progress() as prog:
            games = scan(cfg, cache, targets, prog)
        if args.limit:
            games = games[: args.limit]
        counts = {}
        for g in games:
            counts[g.platform.id] = counts.get(g.platform.id, 0) + 1
        console.print(f"[bold]{len(games):,}[/] ROM in {len(counts)} console: " +
                      ", ".join(f"{k} {v:,}" for k, v in sorted(counts.items())))
        if do_scrape:
            sources = [s.strip() for s in args.sources.split(",")] if args.sources else cfg.sources
            with _progress() as prog:
                asyncio.run(scrape(cfg, cache, games, sources, args.refresh, prog, console.print,
                                   retry_missing=args.missing))
        else:
            load_cached(cfg, cache, games)
        if do_export:
            summary = export(cfg, cache, games, console.print)
            _print_summary(summary, cfg)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrotto: i progressi sono salvati in cache, rilancia per riprendere.[/]")
    finally:
        cache.close()
    console.print(f"Tempo totale: {time.time() - t0:,.1f} s")


def _print_summary(summary: dict, cfg):
    t = Table(title=f"Output: {cfg.output}")
    t.add_column("console")
    t.add_column("giochi", justify="right")
    t.add_column("trovati", justify="right")
    for m in cfg.media:
        t.add_column(m, justify="right")
    tot = {"games": 0, "found": 0, **{m: 0 for m in cfg.media}}
    for pid, s in summary.items():
        pct = f"{100 * s['found'] / s['games']:.0f}%" if s["games"] else "-"
        t.add_row(pid, f"{s['games']:,}", f"{s['found']:,} ({pct})", *(f"{s[m]:,}" for m in cfg.media))
        for k in tot:
            tot[k] += s[k]
    t.add_row("[bold]TOTALE", f"[bold]{tot['games']:,}", f"[bold]{tot['found']:,}", *(f"[bold]{tot[m]:,}" for m in cfg.media))
    console.print(t)


def cmd_stats(args):
    cfg = C.load(args.config)
    cache = Cache(cfg.cache)
    t = Table(title="Cache")
    for c in ("console", "sorgente", "giochi", "risorse"):
        t.add_column(c)
    for row in cache.stats():
        t.add_row(*map(str, row))
    console.print(t)
    cache.close()


def cmd_platforms(args):
    t = Table(title="Piattaforme")
    for c in ("id", "nome", "cartelle riconosciute", "estensioni", "SS", "IGDB", "libretro"):
        t.add_column(c)
    for p in P.PLATFORMS.values():
        t.add_row(p.id, p.name, ", ".join(p.aliases), " ".join(p.exts), str(p.ss or "-"),
                  ",".join(map(str, p.igdb)) or "-", "si" if p.libretro and not p.arcade else "-")
    console.print(t)


def cmd_purge(args):
    cfg = C.load(args.config)
    cache = Cache(cfg.cache)
    plat = P.get(args.platform).id if args.platform else None
    cache.purge(args.source, plat)
    console.print(f"Cache pulita (sorgente={args.source or 'tutte'}, console={plat or 'tutte'}).")
    cache.close()


def cmd_library(args):
    from .library.build import build
    cfg = C.load(args.config)
    with _progress() as prog:
        build(cfg, log=console.print, progress=prog)


def cmd_app(args):
    from .app.server import run
    cfg = C.load(args.config)
    run(cfg, port=args.port, browser=args.browser, bigpicture=args.bigpicture)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gamescraper", description=f"GameScraper {__version__} - erede veloce di Skyscraper")
    ap.add_argument("-c", "--config", help="file config.toml (default: ./config.toml)")
    sub = ap.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("-p", "--platform", help="solo queste console, separate da virgola (es. snes,psx)")
        sp.add_argument("-s", "--sources", help=f"ordine sorgenti, separate da virgola ({','.join(SOURCES)})")
        sp.add_argument("-m", "--media", help="tipi media da scaricare (cover,screenshot,title,wheel,marquee,video,box3d,fanart)")
        sp.add_argument("--roms", help="cartella radice delle ROM (sovrascrive il config)")
        sp.add_argument("--refresh", action="store_true", help="re-interroga le sorgenti anche per giochi gia' cercati")
        sp.add_argument("--missing", action="store_true",
                        help="completa i dati mancanti: riprova le fonti che non avevano trovato il gioco")
        sp.add_argument("--limit", type=int, help="processa solo i primi N giochi (per test)")

    for name in ("run", "scrape", "export"):
        common(sub.add_parser(name))
    sub.add_parser("stats")
    sub.add_parser("library", help="ricostruisce la libreria del frontend (giochi unificati + collezioni)")
    ap_app = sub.add_parser("app", help="avvia il frontend")
    ap_app.add_argument("--port", type=int, default=8765)
    ap_app.add_argument("--browser", action="store_true", help="apri nel browser invece che in una finestra")
    ap_app.add_argument("--bigpicture", action="store_true", help="avvia in modalita' Big Picture (schermo intero, controller)")
    sub.add_parser("platforms")
    pp = sub.add_parser("purge")
    pp.add_argument("--source")
    pp.add_argument("-p", "--platform")
    for sp in sub.choices.values():
        sp.add_argument("-c", "--config", default=argparse.SUPPRESS)

    args = ap.parse_args(argv)
    cmd = args.cmd or "run"
    if cmd == "run" and not args.cmd:
        args = ap.parse_args(["run", *(argv or sys.argv[1:])])
    try:
        if cmd == "run":
            cmd_scan_and(args, True, True)
        elif cmd == "scrape":
            cmd_scan_and(args, True, False)
        elif cmd == "export":
            cmd_scan_and(args, False, True)
        elif cmd == "library":
            cmd_library(args)
        elif cmd == "app":
            cmd_app(args)
        elif cmd == "stats":
            cmd_stats(args)
        elif cmd == "platforms":
            cmd_platforms(args)
        elif cmd == "purge":
            cmd_purge(args)
    except (FileNotFoundError, KeyError, ValueError) as e:
        console.print(f"[red]{e}[/]")
        sys.exit(1)
