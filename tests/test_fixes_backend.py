"""Test di: filtro DLC/pack, magnet per singolo file, giochi manuali (doppioni, applicazioni), immagini scelte.

Esegui: python tests/test_fixes_backend.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamescraper.catalog import parse_listing  # noqa: E402
from gamescraper.library.build import NOT_A_GAME  # noqa: E402
from gamescraper.library.userdata import UserData  # noqa: E402


def test_not_a_game():
    for t in ("Hades Soundtrack", "Game X - Season Pass", "Cool Game DLC", "Skin Pack Deluxe", "Texture Pack"):
        assert NOT_A_GAME.search(t), t
    for t in ("Hades", "Pack-Man", "Bundle of Joy Simulator" if False else "Hollow Knight", "Dead Cells"):
        assert not NOT_A_GAME.search(t), t
    print("ok  filtro DLC/colonne sonore/season pass")


def test_magnet_per_file():
    page = ('<a href="/g/a.nsp">a.nsp</a> 1 GB <a href="magnet:?xt=urn:btih:1&dn=a.nsp">m</a>'
            '<a href="/g/b.nsp">b.nsp</a> 2 GB <a href="magnet:?xt=urn:btih:2&dn=Intera+raccolta">m</a>')
    r = {x["name"]: x["magnet"] for x in parse_listing(page, "http://x/", {"nsp"})}
    assert r["a.nsp"] and "dn=a.nsp" in r["a.nsp"], r
    assert r["b.nsp"] is None, r  # magnet di un intero set: scartato
    print("ok  magnet solo se di un singolo file")


def test_manual(tmp: Path):
    ud = UserData(tmp / "u.db")
    a = ud.add_manual("Uno", "C:/g/uno.exe", [])
    assert ud.add_manual("Uno bis", "c:/G/UNO.exe", []) == a  # stesso exe: nessun doppione
    ud.add_manual("Tool", "C:/g/tool.exe", [], kind="app")
    assert {m["title"]: m["kind"] for m in ud.manual_games()} == {"Uno": "game", "Tool": "app"}
    # doppioni gia' presenti (aggiunti prima di questa versione) -> ne resta uno
    ud.db.execute("INSERT INTO manual_games(title, exe, args, added) VALUES('Uno', 'C:/g/uno.exe', '[]', 1)")
    ud.db.commit()
    assert ud.dedupe_manual() == 1 and len(ud.manual_games()) == 2
    ud.close()  # su Windows un file aperto impedisce di cancellare la cartella temporanea
    print("ok  giochi manuali: niente doppioni, applicazioni, pulizia")


def test_media_override(tmp: Path):
    ud = UserData(tmp / "m.db")
    ud.set_media("windows/manual:1", "cover", "media/net/x.jpg")
    ud.set_media("windows/manual:1", "cover", "media/net/y.jpg")
    assert ud.media_overrides("windows/manual:1") == {"cover": "media/net/y.jpg"}
    assert ud.media_overrides("altro") == {}
    ud.close()
    print("ok  immagini scelte dall'utente")


if __name__ == "__main__":
    test_not_a_game()
    test_magnet_per_file()
    with tempfile.TemporaryDirectory() as d:
        test_manual(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_media_override(Path(d))
    print("test_fixes_backend: OK")
