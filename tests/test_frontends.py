"""Test di esportazione della libreria (library.db) verso ES-DE e Pegasus.

Esegui: python tests/test_frontends.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamescraper import frontends  # noqa: E402
from gamescraper.library.build import SCHEMA  # noqa: E402


def _make_sample_library(cache_dir: Path, roms_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Crea finte immagini in cache
    cover_rel = "snes/media/cover/smw.png"
    shot_rel = "snes/media/screenshot/smw.jpg"
    wheel_rel = "snes/media/wheel/smw.png"
    for rel, data in ((cover_rel, b"\x89PNG_FAKE_COVER"),
                      (shot_rel, b"\xff\xd8\xff_FAKE_SHOT"),
                      (wheel_rel, b"\x89PNG_FAKE_WHEEL")):
        p = cache_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    snes_rom = str(roms_dir / "snes" / "Super Mario & Luigi (Europe).sfc")
    snes_rom_us = str(roms_dir / "snes" / "Super Mario & Luigi (USA).sfc")
    gba_rom = str(roms_dir / "gba" / "Advance Wars (Europe).gba")

    db = sqlite3.connect(cache_dir / "library.db")
    db.executescript(SCHEMA)

    rows = [
        # 1. Gioco principale SNES con caratteri speciali XML (&, <, >, accenti) e descrizione multilinea
        (
            "snes/smw_eu", "snes", "Super Mario & Luigi: <Edizione Più>", "super mario & luigi",
            1991, "1991-11-21", "Nintendo EAD", "Nintendo & Co.",
            json.dumps(["Platform", "Action & Adventure"]), "1-2", 0.92,
            "Prima riga con <tag> & accenti (è, à, ù).\n\nSeconda riga dopo riga vuota.",
            "eu", 1, json.dumps([snes_rom]),
            json.dumps({"cover": cover_rel, "screenshot": shot_rel, "wheel": wheel_rel}),
            None, None, "1234ABCD", "{}", "snes/grp1", 1,
            None, None, None, None, None, None, None, 10, 0.95, "[]", 524288,
        ),
        # 2. Variante regionale non principale (is_primary=0): NON deve comparire nell'esportazione
        (
            "snes/smw_us", "snes", "Super Mario & Luigi (USA)", "super mario & luigi",
            1991, "1991-08-23", "Nintendo EAD", "Nintendo",
            json.dumps(["Platform"]), "1-2", 0.90, "Versione USA",
            "us", 1, json.dumps([snes_rom_us]), "{}",
            None, None, "5678EF90", "{}", "snes/grp1", 0,
            None, None, None, None, None, None, None, None, None, "[]", 524288,
        ),
        # 3. Gioco PC di uno store (store='steam'): NON deve comparire
        (
            "windows/steam:620", "windows", "Portal 2", "portal 2",
            2011, "2011-04-19", "Valve", "Valve",
            json.dumps(["Puzzle"]), "1-2", 0.98, "Gioco PC",
            None, 1, json.dumps(["C:/Program Files/Steam/steamapps/common/Portal 2"]), "{}",
            None, None, None, "{}", "windows/t:portal2", 1,
            "steam", 1, "{}", None, None, 0.0, None, None, None, "[]", 1000000,
        ),
        # 4. Voce di catalogo online senza file (files='[]', store='online'): NON deve comparire
        (
            "snes/online:1", "snes", "Gioco Non Posseduto", "gioco non posseduto",
            1993, "1993", "Dev", "Pub",
            "[]", "1", 0.70, None,
            "eu", 1, "[]", "{}",
            None, None, None, "{}", "snes/grp_online", 1,
            "online", 0, "{}", None, None, 0.0, None, None, None, "[]", 0,
        ),
        # 5. Gioco principale GBA
        (
            "gba/aw_eu", "gba", "Advance Wars", "advance wars",
            2001, "2001-09-10", "Intelligent Systems", "Nintendo",
            json.dumps(["Strategy"]), "1-4", 0.88, "Strategico a turni.",
            "eu", 1, json.dumps([gba_rom]), "{}",
            None, None, "AABBCCDD", "{}", "gba/grp1", 1,
            None, None, None, None, None, None, None, None, None, "[]", 8388608,
        ),
    ]
    db.executemany(
        "INSERT INTO games(key, platform, title, sort_title, year, release_date, developer, publisher,"
        " genres, players, rating, description, region, found, files, media, video_url, canonical,"
        " crc, origin, grp, is_primary, store, installed, launch, install_uri, page, store_playtime,"
        " install_dir, rating_count, rating_critics, ratings, size) VALUES(" + ",".join("?" * 33) + ")",
        rows,
    )
    db.commit()
    db.close()


def test_export_esde(tmp: Path) -> None:
    cache_dir = tmp / "cache"
    roms_dir = tmp / "roms"
    _make_sample_library(cache_dir, roms_dir)

    out_esde = tmp / "out_esde"
    summary = frontends.export_esde(cache_dir, out_esde)
    assert summary == {"gba": 1, "snes": 1}, summary
    assert "windows" not in summary

    xml_file = out_esde / "snes" / "gamelist.xml"
    assert xml_file.is_file(), "gamelist.xml non creato per snes"

    # Verifica che l'XML sia ben formato (escape corretto di &, <, > e accenti)
    root = ET.fromstring(xml_file.read_text(encoding="utf-8"))
    assert root.tag == "gameList"
    games = root.findall("game")
    assert len(games) == 1
    g = games[0]
    assert g.findtext("path") == "./Super Mario & Luigi (Europe).sfc"
    assert g.findtext("name") == "Super Mario & Luigi: <Edizione Più>"
    assert g.findtext("releasedate") == "19911121T000000"
    assert g.findtext("developer") == "Nintendo EAD"
    assert g.findtext("publisher") == "Nintendo & Co."
    assert g.findtext("genre") == "Platform, Action & Adventure"
    assert g.findtext("players") == "1-2"
    assert g.findtext("rating") == "0.92"
    assert "Prima riga con <tag> & accenti (è, à, ù)." in (g.findtext("desc") or "")

    # Verifica copia dei media in downloaded_media/snes/<tipo>/<rom_stem>.<ext>
    stem = "Super Mario & Luigi (Europe)"
    assert (out_esde / "downloaded_media" / "snes" / "covers" / f"{stem}.png").read_bytes() == b"\x89PNG_FAKE_COVER"
    assert (out_esde / "downloaded_media" / "snes" / "screenshots" / f"{stem}.jpg").read_bytes() == b"\xff\xd8\xff_FAKE_SHOT"
    assert (out_esde / "downloaded_media" / "snes" / "marquees" / f"{stem}.png").read_bytes() == b"\x89PNG_FAKE_WHEEL"

    # Verifica filtro platforms=["snes"]
    out_filtered = tmp / "out_esde_snes"
    sum_f = frontends.export_esde(cache_dir, out_filtered, platforms=["snes"])
    assert sum_f == {"snes": 1}, sum_f
    assert not (out_filtered / "gba").exists()
    print("ok  export_esde: gamelist.xml valido, escape XML/UTF-8, downloaded_media e filtro piattaforme")


def test_export_pegasus(tmp: Path) -> None:
    cache_dir = tmp / "cache"
    out_peg = tmp / "out_pegasus"
    summary = frontends.export_pegasus(cache_dir, out_peg)
    assert summary == {"gba": 1, "snes": 1}, summary

    meta_file = out_peg / "snes" / "metadata.pegasus.txt"
    assert meta_file.is_file()
    txt = meta_file.read_text(encoding="utf-8")

    assert "collection: Super Nintendo\n" in txt
    assert "shortname: snes\n" in txt
    assert "game: Super Mario & Luigi: <Edizione Più>\n" in txt
    assert "file: Super Mario & Luigi (Europe).sfc\n" in txt
    assert "developer: Nintendo EAD\n" in txt
    assert "publisher: Nintendo & Co.\n" in txt
    assert "genre: Platform\n" in txt
    assert "genre: Action & Adventure\n" in txt
    assert "players: 1-2\n" in txt
    assert "release: 1991-11-21\n" in txt
    assert "rating: 92%\n" in txt
    # Descrizione multilinea rientrata con punto per la riga vuota
    assert "description:\n  Prima riga con <tag> & accenti (è, à, ù).\n  .\n  Seconda riga dopo riga vuota.\n" in txt

    stem = "Super Mario & Luigi (Europe)"
    assert f"assets.box_front: media/cover/{stem}.png\n" in txt
    assert f"assets.screenshot: media/screenshot/{stem}.jpg\n" in txt
    assert f"assets.wheel: media/wheel/{stem}.png\n" in txt
    assert (out_peg / "snes" / "media" / "cover" / f"{stem}.png").read_bytes() == b"\x89PNG_FAKE_COVER"
    print("ok  export_pegasus: metadata.pegasus.txt, descrizione multilinea e copia assets")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        test_export_esde(tmp)
        test_export_pegasus(tmp)
    print("TUTTI I TEST OK")
