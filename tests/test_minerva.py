"""Test del plugin Store Minerva: lettura di r-roms e assegnazione dei link alle console. Nessuna rete.

Esegui: python tests/test_minerva.py
"""

import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("minerva", ROOT / "gamescraper/app/plugins/minerva/backend.py")
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

INDEX = '<a href="./_app/immutable/x.js"></a><a href="./Sega/sega-dreamcast">DC</a><a href="./Retro &amp; Arcade/retro-games">R</a>'
PAGES = {
    "https://r-roms.github.io/Sega/sega-dreamcast": (
        '<a href="https://minerva-archive.org/browse/Redump/Sega%20-%20Dreamcast/">set</a>'
        '<a href="https://minerva-archive.org/browse/Internet%20Archive/chadmaster/dc-chd-zstd/">chd</a>'
        '<a href="https://minerva-archive.org/browse/Redump/Sega%20-%20Dreamcast/Crazy%20Taxi%20(USA).zip">file</a>'
        '<a href="https://archive.org/details/altro">non minerva</a>'),
    "https://r-roms.github.io/Retro%20&%20Arcade/retro-games": (
        '<a href="https://minerva-archive.org/browse/No-Intro/Nintendo%20-%20Game%20Boy/">gb</a>'
        '<a href="https://minerva-archive.org/browse/Redump/Philips%20-%20CD-i/">console sconosciuta</a>'
        '<a href="https://minerva-archive.org/browse/Redump/Nintendo%20-%20Wii%20-%20NKit%20RVZ%20[zstd-19-128k]/">a</a>'
        '<a href="https://minerva-archive.org/browse/Redump/Nintendo%20-%20Wii%20-%20NKit%20RVZ%20%5Bzstd-19-128k%5D/">b</a>'),
}


def test_collect():
    def get(u):
        return INDEX if u == M.RROMS else PAGES[u.replace(" ", "%20")]
    found = M.collect(get)
    assert found["dreamcast"] == [  # la cartella IA senza nome di set prende la console dalla pagina r-roms
        "https://minerva-archive.org/browse/Redump/Sega%20-%20Dreamcast/",
        "https://minerva-archive.org/browse/Internet%20Archive/chadmaster/dc-chd-zstd/"], found
    assert found["gb"] == ["https://minerva-archive.org/browse/No-Intro/Nintendo%20-%20Game%20Boy/"], found
    assert len(found["wii"]) == 1, found  # stesso link con codifica diversa
    assert set(found) == {"dreamcast", "gb", "wii"}, found  # niente file singoli, archive.org o CD-i
    print("ok  r-roms: solo cartelle Minerva, assegnate alla console giusta")


def test_platform_hints():
    pl = M.platform_of
    b = "https://minerva-archive.org/browse/"
    assert pl(b + "Redump/Panasonic%20-%203DO%20Interactive%20Multiplayer/", "./Retro/retro-games") == "3do"
    assert pl(b + "Internet%20Archive/chadmaster/pcecd-chd-zstd-redump/pcecd-chd-zstd/", "./Retro/retro-games") == "pcenginecd"
    assert pl(b + "Internet%20Archive/chadmaster/ngcd-chd-zstd-redump/ngcd-chd-zstd/", "./Retro/retro-games") == "neogeocd"
    assert pl(b + "Internet%20Archive/chadmaster/jagcd-chd-zstd/jagcd-chd-zstd/", "./Retro/retro-games") == "atarijaguar"
    assert pl(b + "Internet%20Archive/sketch_the_cow/Total_DOS_Collection_Release_16_March_2019/", "./PC/pc-games") == "dos"
    print("ok  console non riconosciute dal nome del set")


if __name__ == "__main__":
    test_collect()
    test_platform_hints()
    print("TUTTI I TEST OK")
