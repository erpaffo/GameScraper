"""Test del database offline GameTDB (indice, ricerca per titolo, copertine). Nessuna rete.

Esegui: PYTHONUTF8=1 python tests/catalog/test_gametdb.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.pop("GAMESCRAPER_GAMETDB", None)  # i test usano solo il file locale che creano

from gamescraper.gametdb import GameTDB, clean_title, parse_xml_text, title_key  # noqa: E402

XML = """<?xml version="1.0" encoding="UTF-8"?>
<datafile>
  <SwitchTDB version="20260101000000" games="2"/>
  <game name="Zelda di Prova (EN,FR,DE,IT)">
    <id>AAAAA</id>
    <region>USA,EUR</region>
    <locale lang="EN"><title>Zelda di Prova</title><synopsis>English synopsis of the game.</synopsis></locale>
    <locale lang="IT"><title>Zelda di Prova</title><synopsis>Sinossi italiana del gioco.</synopsis></locale>
    <publisher>Nintendo</publisher>
    <developer>Nintendo EPD</developer>
    <date year="2023" month="5" day="12"/>
    <genre>action,adventure</genre>
    <input players="2"/>
  </game>
  <game name="Altro Gioco Switch NSP">
    <id>BBBBB</id>
    <region>EUR</region>
    <locale lang="EN"><title>Altro Gioco</title><synopsis>Un altro gioco di prova.</synopsis></locale>
    <publisher>Studio</publisher>
    <genre>puzzle</genre>
    <input players="1"/>
  </game>
</datafile>
"""


def test_titles():
    assert clean_title("Zelda di Prova Switch NSP (eShop)") == "Zelda di Prova"
    assert title_key("Zelda di Prova!") == "zeldadiprova"
    assert title_key("Pokémon") == "pokemon"
    print("ok  titoli: pulizia e chiave senza accenti")


def test_index_and_lookup():
    index = parse_xml_text(XML)
    assert len(index["games"]) == 2 and index["version"] == "20260101000000", index
    games = index["games"]
    assert games["AAAAA"]["players"] == 2 and games["AAAAA"]["developer"] == "Nintendo EPD", games["AAAAA"]
    assert games["AAAAA"]["release_date"] == "2023-05-12" and games["AAAAA"]["genres"] == ["action", "adventure"]
    # l'italiano ha la precedenza, altrimenti inglese
    assert games["AAAAA"]["synopsis"] == "Sinossi italiana del gioco.", games["AAAAA"]["synopsis"]
    # copertine: prima la regione del gioco (USA -> US), poi le altre
    assert games["AAAAA"]["covers"][0] == "https://art.gametdb.com/switch/coverHQ/US/AAAAA.jpg", games["AAAAA"]["covers"]
    assert "https://art.gametdb.com/switch/coverHQ/EN/AAAAA.jpg" in games["AAAAA"]["covers"]
    assert "https://art.gametdb.com/switch/cover/EN/AAAAA.jpg" in games["AAAAA"]["covers"]
    print("ok  indice: giocatori, editore, data, generi, sinossi IT e copertine per regione")


def test_find_by_noisy_title():
    tdb = GameTDB.__new__(GameTDB)  # indice gia' pronto, senza toccare il disco
    tdb._index = parse_xml_text(XML)
    assert tdb.find("Zelda di Prova Switch NSP (eShop)")["id"] == "AAAAA"
    assert tdb.find("Zelda di Prova (EN,FR,DE,IT)")["id"] == "AAAAA"
    assert tdb.find("Altro Gioco Switch NSP")["id"] == "BBBBB"
    assert tdb.find("Gioco Inesistente") is None
    assert tdb.by_id("bbbbb")["id"] == "BBBBB"
    assert tdb.lookup("Gioco Inesistente", title_id="") is None
    print("ok  ricerca: titoli con «Switch NSP», parentesi, ID e casi assenti")


def test_ensure_with_local_xml(tmp_path):
    folder = tmp_path / "gametdb"
    folder.mkdir(parents=True)
    (folder / "switchtdb.xml").write_text(XML, encoding="utf-8")
    tdb = GameTDB(tmp_path, log=lambda m: None)
    assert not tdb.ready()
    assert tdb.ensure() is True, "con il file locale non serve scaricare"
    assert tdb.ready() and tdb.index_path.exists()
    assert tdb.find("Altro Gioco")["id"] == "BBBBB"
    # il secondo avvio riusa l'indice JSON appena creato
    again = GameTDB(tmp_path, log=lambda m: None)
    assert again.ensure() is True and again.find("Zelda di Prova")["id"] == "AAAAA"
    print("ok  indice: costruito dal file locale e riusato senza rileggere l'XML")


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    test_titles()
    test_index_and_lookup()
    test_find_by_noisy_title()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_ensure_with_local_xml(Path(t))
    print("TUTTI I TEST OK")
