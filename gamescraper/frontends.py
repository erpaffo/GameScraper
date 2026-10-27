"""Esportazione della libreria costruita (library.db) verso i frontend ES-DE e Pegasus.

Legge `<cache>/library.db` (solo giochi con file locali, niente store PC ne' cataloghi online,
solo versioni principali `is_primary = 1`) e scrive tutto dentro `out_dir` usando solo la
libreria standard.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from . import platforms as P

# Mappa dai tipi media di GameScraper alle sottocartelle di downloaded_media/<console>/ in ES-DE
ESDE_MEDIA_DIRS: dict[str, str] = {
    "cover": "covers",
    "screenshot": "screenshots",
    "title": "titlescreens",
    "wheel": "marquees",
    "marquee": "marquees",
    "video": "videos",
    "box3d": "3dboxes",
    "fanart": "fanart",
}

# Mappa dai tipi media di GameScraper alle chiavi assets.* di Pegasus
PEGASUS_ASSETS: dict[str, str] = {
    "cover": "assets.box_front",
    "screenshot": "assets.screenshot",
    "title": "assets.titlescreen",
    "wheel": "assets.wheel",
    "marquee": "assets.marquee",
    "video": "assets.video",
    "box3d": "assets.box_full",
    "fanart": "assets.background",
}


def _load_primary_games(cfg_cache: Path, platforms: list[str] | None = None) -> dict[str, list[dict]]:
    """Carica da `<cfg_cache>/library.db` i giochi principali con file locali, raggruppati per console."""
    db_path = Path(cfg_cache) / "library.db"
    if not db_path.is_file():
        return {}
    allowed = {p.lower() for p in platforms} if platforms else None
    by_plat: dict[str, list[dict]] = defaultdict(list)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT key, platform, title, sort_title, year, release_date, developer, publisher, "
            "genres, players, rating, description, files, media, is_primary, store "
            "FROM games WHERE is_primary = 1 ORDER BY platform, sort_title, title"
        )
        for row in cur:
            pid = (row["platform"] or "").strip()
            if not pid or pid == "windows":
                continue
            if allowed is not None and pid.lower() not in allowed:
                continue
            if row["store"]:  # salta store PC e voci di catalogo online
                continue
            try:
                files = json.loads(row["files"] or "[]")
            except (TypeError, ValueError):
                files = []
            if not files:
                continue
            try:
                genres = json.loads(row["genres"] or "[]")
                if not isinstance(genres, list):
                    genres = [str(genres)] if genres else []
            except (TypeError, ValueError):
                genres = [row["genres"]] if row["genres"] else []
            try:
                media = json.loads(row["media"] or "{}")
                if not isinstance(media, dict):
                    media = {}
            except (TypeError, ValueError):
                media = {}

            by_plat[pid].append({
                "key": row["key"],
                "platform": pid,
                "title": row["title"] or Path(files[0]).stem,
                "sort_title": row["sort_title"] or "",
                "year": row["year"],
                "release_date": row["release_date"] or "",
                "developer": row["developer"] or "",
                "publisher": row["publisher"] or "",
                "genres": [str(g).strip() for g in genres if str(g).strip()],
                "players": str(row["players"] or "").strip(),
                "rating": row["rating"],
                "description": row["description"] or "",
                "files": [str(f) for f in files if f],
                "media": media,
            })
    finally:
        con.close()
    return by_plat


def _resolve_media_src(cfg_cache: Path, rel: str) -> Path | None:
    if not rel:
        return None
    p = Path(rel[5:]) if rel.startswith("file:") else (Path(cfg_cache) / rel)
    return p if p.is_file() else None


def _copy_media(src: Path, dst: Path) -> None:
    """Copia (o collega via hardlink se possibile) un file media dentro `out_dir`."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            if dst.stat().st_size == src.stat().st_size:
                return
            dst.unlink()
        except OSError:
            pass
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _rom_stem(file_path: str) -> str:
    p = Path(file_path)
    # Per i giochi in cartella (es. Switch/PS3) o file senza estensione usa il nome completo
    return p.stem if p.suffix else p.name


def _esde_release_date(release_date: str, year: int | None) -> str:
    """Converte una data in formato ES-DE `YYYYMMDDT000000`."""
    s = (release_date or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}T000000"
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        return f"{m.group(1)}{m.group(2)}01T000000"
    m = re.match(r"^(\d{4})$", s)
    if m:
        return f"{m.group(1)}0101T000000"
    if year and 1970 <= int(year) <= 2100:
        return f"{int(year):04d}0101T000000"
    return ""


def _pegasus_release_date(release_date: str, year: int | None) -> str:
    """Converte una data in formato ISO `YYYY-MM-DD` (o `YYYY`) per Pegasus."""
    s = (release_date or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    m = re.match(r"^(\d{4}-\d{2})$", s)
    if m:
        return m.group(1)
    m = re.match(r"^(\d{4})", s)
    if m:
        return m.group(1)
    if year and 1970 <= int(year) <= 2100:
        return f"{int(year):04d}"
    return ""


def _common_rom_root(games: list[dict]) -> Path | None:
    """Individua la cartella base comune dei file ROM di una console per calcolare i percorsi relativi."""
    parents = []
    for g in games:
        if g["files"]:
            p = Path(g["files"][0])
            if p.parent and str(p.parent) not in ("", "."):
                parents.append(str(p.parent))
    if not parents:
        return None
    try:
        common = os.path.commonpath(parents)
        return Path(common) if common else None
    except ValueError:
        return None


def _esde_rom_path(file_path: str, rom_root: Path | None) -> str:
    p = Path(file_path)
    if rom_root is not None:
        try:
            rel = p.relative_to(rom_root).as_posix()
            return f"./{rel}"
        except ValueError:
            pass
    return f"./{p.name}"


def _xml_tag(indent: str, tag: str, value: str) -> str:
    return f"{indent}<{tag}>{xml_escape(str(value))}</{tag}>"


def export_esde(cfg_cache: Path, out_dir: Path, platforms: list[str] | None = None) -> dict[str, int]:
    """Esporta la libreria nel formato di ES-DE (EmulationStation Desktop Edition).

    Per ogni console scrive `out_dir/<console>/gamelist.xml` e copia i media disponibili in
    `out_dir/downloaded_media/<console>/<tipo>/<rom_stem>.<ext>`.
    Ritorna `{console: numero_di_giochi}`.
    """
    cfg_cache = Path(cfg_cache)
    out_dir = Path(out_dir)
    by_plat = _load_primary_games(cfg_cache, platforms)
    summary: dict[str, int] = {}

    for pid, games in sorted(by_plat.items()):
        if not games:
            continue
        rom_root = _common_rom_root(games)
        lines = ['<?xml version="1.0"?>', "<gameList>"]
        for g in games:
            first_file = g["files"][0]
            stem = _rom_stem(first_file)
            lines.append("\t<game>")
            lines.append(_xml_tag("\t\t", "path", _esde_rom_path(first_file, rom_root)))
            lines.append(_xml_tag("\t\t", "name", g["title"]))
            if g["description"]:
                lines.append(_xml_tag("\t\t", "desc", g["description"]))
            rdate = _esde_release_date(g["release_date"], g["year"])
            if rdate:
                lines.append(_xml_tag("\t\t", "releasedate", rdate))
            if g["developer"]:
                lines.append(_xml_tag("\t\t", "developer", g["developer"]))
            if g["publisher"]:
                lines.append(_xml_tag("\t\t", "publisher", g["publisher"]))
            if g["genres"]:
                lines.append(_xml_tag("\t\t", "genre", ", ".join(g["genres"])))
            if g["players"]:
                lines.append(_xml_tag("\t\t", "players", g["players"]))
            if g["rating"] is not None:
                try:
                    rval = max(0.0, min(1.0, float(g["rating"])))
                    lines.append(_xml_tag("\t\t", "rating", f"{rval:.2f}"))
                except (TypeError, ValueError):
                    pass
            lines.append("\t</game>")

            # Copia dei media nelle sottocartelle downloaded_media/<console>/<tipo>/
            copied_folders: set[str] = set()
            for mtype, rel in g["media"].items():
                esde_folder = ESDE_MEDIA_DIRS.get(mtype)
                if not esde_folder or esde_folder in copied_folders:
                    continue
                src = _resolve_media_src(cfg_cache, rel)
                if src is None:
                    continue
                dst = out_dir / "downloaded_media" / pid / esde_folder / f"{stem}{src.suffix}"
                _copy_media(src, dst)
                copied_folders.add(esde_folder)

        lines.append("</gameList>")
        xml_path = out_dir / pid / "gamelist.xml"
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        summary[pid] = len(games)

    return summary


def _clean_single_line(val: str) -> str:
    return re.sub(r"[\r\n]+", " ", str(val)).strip()


def export_pegasus(cfg_cache: Path, out_dir: Path, platforms: list[str] | None = None) -> dict[str, int]:
    """Esporta la libreria nel formato di Pegasus (`out_dir/<console>/metadata.pegasus.txt`).

    Copia i media in `out_dir/<console>/media/<tipo>/<rom_stem>.<ext>` e li collega con
    percorsi relativi nei campi `assets.*`. Ritorna `{console: numero_di_giochi}`.
    """
    cfg_cache = Path(cfg_cache)
    out_dir = Path(out_dir)
    by_plat = _load_primary_games(cfg_cache, platforms)
    summary: dict[str, int] = {}

    for pid, games in sorted(by_plat.items()):
        if not games:
            continue
        plat = P.PLATFORMS.get(pid)
        plat_name = plat.name if plat else pid
        plat_dir = out_dir / pid
        plat_dir.mkdir(parents=True, exist_ok=True)
        rom_root = _common_rom_root(games)

        blocks: list[str] = []
        header = [f"collection: {_clean_single_line(plat_name)}", f"shortname: {pid}"]
        if plat and plat.all_exts:
            header.append("extensions: " + ", ".join(plat.all_exts))
        blocks.append("\n".join(header))

        for g in games:
            first_file = g["files"][0]
            stem = _rom_stem(first_file)
            fp = Path(first_file)
            if rom_root is not None:
                try:
                    file_val = fp.relative_to(rom_root).as_posix()
                except ValueError:
                    file_val = fp.as_posix()
            else:
                file_val = fp.as_posix()

            entry = [
                f"game: {_clean_single_line(g['title'])}",
                f"file: {file_val}",
            ]
            if g["sort_title"]:
                entry.append(f"sort-by: {_clean_single_line(g['sort_title'])}")
            if g["developer"]:
                entry.append(f"developer: {_clean_single_line(g['developer'])}")
            if g["publisher"]:
                entry.append(f"publisher: {_clean_single_line(g['publisher'])}")
            for genre in g["genres"]:
                entry.append(f"genre: {_clean_single_line(genre)}")
            if g["players"]:
                entry.append(f"players: {_clean_single_line(g['players'])}")
            rdate = _pegasus_release_date(g["release_date"], g["year"])
            if rdate:
                entry.append(f"release: {rdate}")
            if g["rating"] is not None:
                try:
                    rval = max(0.0, min(1.0, float(g["rating"])))
                    entry.append(f"rating: {round(rval * 100)}%")
                except (TypeError, ValueError):
                    pass
            if g["description"]:
                desc_lines = g["description"].replace("\r\n", "\n").replace("\r", "\n").split("\n")
                entry.append("description:")
                for dl in desc_lines:
                    stripped = dl.strip()
                    entry.append(f"  {stripped}" if stripped else "  .")

            for mtype, rel in g["media"].items():
                asset_key = PEGASUS_ASSETS.get(mtype)
                if not asset_key:
                    continue
                src = _resolve_media_src(cfg_cache, rel)
                if src is None:
                    continue
                rel_dst = Path("media") / mtype / f"{stem}{src.suffix}"
                _copy_media(src, plat_dir / rel_dst)
                entry.append(f"{asset_key}: {rel_dst.as_posix()}")

            blocks.append("\n".join(entry))

        meta_path = plat_dir / "metadata.pegasus.txt"
        meta_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        summary[pid] = len(games)

    return summary
