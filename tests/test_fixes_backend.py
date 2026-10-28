"""Test di: filtro DLC/pack, magnet per singolo file, giochi manuali (doppioni, applicazioni), immagini scelte.

Esegui: python tests/test_fixes_backend.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pozzo.catalog import parse_listing  # noqa: E402
from pozzo.library.build import NOT_A_GAME  # noqa: E402
from pozzo.library.userdata import UserData  # noqa: E402


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


def test_update_manual(tmp: Path):
    import sqlite3

    from pozzo import config as C
    from pozzo.cache import Cache
    from pozzo.library import build as B
    from pozzo.stores.base import StoreGame
    cfg = C.Config()
    cfg.cache = tmp
    assert B.update_manual(cfg) is False  # libreria non ancora costruita
    con = sqlite3.connect(tmp / "library.db")
    con.executescript(B.SCHEMA)
    con.close()
    cache = Cache(tmp)
    cache.kv_set("store:manual", [{**StoreGame(store="manual", id="7", title="Mio Gioco", installed=True,
                                               launch={"type": "exe", "target": "C:/g/mio.exe"}).to_dict(), "media": {}}])
    cache.close()
    assert B.update_manual(cfg) and B.update_manual(cfg)  # due volte: nessun doppione
    db = sqlite3.connect(tmp / "library.db")
    rows = db.execute("SELECT key, title, store, is_primary FROM games").fetchall()
    db.close()
    assert rows == [("windows/manual:7", "Mio Gioco", "manual", 1)], rows
    print("ok  aggiornamento rapido dei soli giochi manuali")


def test_replace_retry(tmp: Path):
    from pozzo import config as C
    real, calls = os.replace, []

    def flaky(a, b):  # come Windows con il file aperto da un altro thread: due rifiuti, poi riesce
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(5, "Access is denied")
        return real(a, b)
    (tmp / "a.tmp").write_text("nuovo", encoding="utf-8")
    C.os.replace = flaky
    try:
        C._replace(tmp / "a.tmp", tmp / "a.json")
    finally:
        C.os.replace = real
    assert (tmp / "a.json").read_text(encoding="utf-8") == "nuovo" and len(calls) == 3
    print("ok  settings.json: riprova se il file e' aperto da un altro thread")


if __name__ == "__main__":
    test_not_a_game()
    test_magnet_per_file()
    with tempfile.TemporaryDirectory() as d:
        test_manual(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_media_override(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_update_manual(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_replace_retry(Path(d))
    print("test_fixes_backend: OK")
