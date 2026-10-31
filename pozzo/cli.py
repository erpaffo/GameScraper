"""Interfaccia a riga di comando.

  python -m pozzo run        scansione + scraping + export (tutte le console trovate)
  python -m pozzo scrape     solo scraping in cache
  python -m pozzo export     solo export da cache (nessuna rete)
  python -m pozzo stats      statistiche della cache
  python -m pozzo platforms  elenco piattaforme e alias di cartella
  python -m pozzo purge      cancella dati in cache (per sorgente / piattaforma)
"""

from __future__ import annotations

import argparse
import asyncio
import os
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


def cmd_relink(args):
    from . import relink
    from .library.build import build
    cfg = C.load(args.config)
    pairs = relink.plan(cfg, log=console.print)
    for old, new, title, path in pairs:
        console.print(f"  {title}  ->  {path}")
    console.print(f"{len(pairs)} giochi da riassociare.")
    if args.apply and pairs:
        relink.apply(cfg, pairs, log=console.print)
        with _progress() as prog:
            build(cfg, log=console.print, progress=prog)
    elif pairs:
        console.print("Anteprima: rilancia con --apply per eseguire.")


def cmd_export_frontend(args):
    """Libreria per ES-DE (gamelist.xml), Pegasus (metadata.pegasus.txt) o pagina web statica."""
    from . import frontends
    cfg = C.load(args.config)
    out = Path(args.out) if args.out else Path(cfg.output) / args.format
    plats = [p.strip() for p in args.platform.split(",")] if args.platform else None
    if args.format == "html":
        summary = frontends.export_html(Path(cfg.cache), out, plats, title=args.title or "La mia libreria")
        for pid, n in sorted(summary.items()):
            console.print(f"  {pid}: {n} giochi")
        console.print(f"Pagina web: {(out / 'index.html')} · {sum(summary.values())} giochi")
        return
    fn = frontends.export_esde if args.format == "esde" else frontends.export_pegasus
    summary = fn(Path(cfg.cache), out, plats)
    for pid, n in sorted(summary.items()):
        console.print(f"  {pid}: {n} giochi")
    console.print(f"Esportati {sum(summary.values())} giochi in {out}")


def cmd_app(args):
    from . import applog
    from .app.server import run
    cfg = C.load(args.config)
    # log su file: --log (predefinito <cache>/logs/diagnostica.log) oppure POZZO_LOG=<percorso>
    applog.start(getattr(args, "log", None) or os.environ.get("POZZO_LOG"), cfg.cache)
    run(cfg, port=args.port, browser=args.browser, bigpicture=args.bigpicture)


def _local_server(cfg):
    """URL di un PozzoLauncher gia' aperto, altrimenti ne avvia uno senza finestra in questo processo."""
    from .app.server import _is_pozzo, _port_free
    port = int(cfg.app.get("port", 8765))
    if _is_pozzo(port):
        return f"http://127.0.0.1:{port}", None
    import threading

    import uvicorn

    from .app.server import LocalGuard, create_app
    free = port if _port_free(port) else next(q for q in range(port + 1, port + 50) if _port_free(q))
    srv = uvicorn.Server(uvicorn.Config(LocalGuard(create_app(cfg), free), host="127.0.0.1", port=free, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{free}", srv


def cmd_play(args):
    """Avvia un gioco e resta aperto finche' si gioca (per Sunshine/Moonlight, scorciatoie, script).

    Usa il client dell'API pubblica (`/api/v1`), lo stesso che usano script e plugin: se il client funziona
    qui, funziona anche fuori."""
    from .api_client import ApiError, Client
    cfg = C.load(args.config)
    url, srv = _local_server(cfg)
    c = Client(base_url=url, cfg=cfg)
    try:
        g = c.find(args.key)
        if not g:
            console.print(f"[red]Gioco non trovato in libreria: {args.key}[/]")
            sys.exit(1)
        c.play(g["id"], idem=f"play-{args.key}")
        console.print(f"Avviato: {g['title']}")
        c.wait_end()
    except ApiError as e:
        console.print(f"[red]{e.detail}[/]")
        sys.exit(1)
    finally:
        if srv:
            srv.should_exit = True


def cmd_bigpicture(args):
    """Big Picture a schermo intero (per lo streaming con Moonlight): usa l'app gia' aperta se c'e'."""
    cfg = C.load(args.config)
    from .app.server import _is_pozzo, run
    port = int(cfg.app.get("port", 8765))
    if not _is_pozzo(port):
        run(cfg, port=port, bigpicture=True)
        return
    try:
        import webview
    except ImportError:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}/bigpicture")
        return
    webview.create_window("PozzoLauncher Big Picture", f"http://127.0.0.1:{port}/bigpicture", fullscreen=True,
                          background_color="#0b0d12")
    webview.start()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pozzo", description=f"PozzoLauncher {__version__} - la tua libreria di giochi")
    ap.add_argument("-c", "--config", help="file config.toml (default: POZZO_CONFIG, poi la cartella dati dell'utente)")
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
    rl = sub.add_parser("relink", help="riassocia i dati dei giochi rinominati/spostati (anteprima; --apply per eseguire)")
    rl.add_argument("--apply", action="store_true")
    xf = sub.add_parser("export-frontend", help="esporta la libreria per ES-DE, Pegasus o come pagina web statica")
    xf.add_argument("--format", choices=("esde", "pegasus", "html"), default="esde")
    xf.add_argument("--out", help="cartella di destinazione (default: <output>/<formato>)")
    xf.add_argument("-p", "--platform", help="solo queste console, separate da virgola")
    xf.add_argument("--title", help="titolo della pagina web (solo --format html)")
    ap_app = sub.add_parser("app", help="avvia il frontend")
    ap_app.add_argument("--port", type=int, default=8765)
    ap_app.add_argument("--browser", action="store_true", help="apri nel browser invece che in una finestra")
    ap_app.add_argument("--bigpicture", action="store_true", help="avvia in modalita' Big Picture (schermo intero, controller)")
    ap_app.add_argument("--log", nargs="?", const="1", default=None,
                        help="scrive i log su file (predefinito <cache>/logs/diagnostica.log; puoi indicare un percorso)")
    sub.add_parser("platforms")
    pl = sub.add_parser("play", help="avvia un gioco e attende che finisca (usato da Sunshine/Moonlight)")
    pl.add_argument("--key", required=True, help="chiave del gioco (es. snes/...)")
    sub.add_parser("bigpicture", help="Big Picture a schermo intero (usa l'app gia' aperta se c'e')")
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
        elif cmd == "relink":
            cmd_relink(args)
        elif cmd == "export-frontend":
            cmd_export_frontend(args)
        elif cmd == "app":
            cmd_app(args)
        elif cmd == "play":
            cmd_play(args)
        elif cmd == "bigpicture":
            cmd_bigpicture(args)
        elif cmd == "stats":
            cmd_stats(args)
        elif cmd == "platforms":
            cmd_platforms(args)
        elif cmd == "purge":
            cmd_purge(args)
    except (FileNotFoundError, KeyError, ValueError) as e:
        console.print(f"[red]{e}[/]")
        sys.exit(1)
