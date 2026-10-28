"""Test del plugin Store Minerva: lettura di r-roms e assegnazione dei link alle console. Nessuna rete.

Esegui: python tests/test_minerva.py
"""

import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("minerva", ROOT / "extensions/minerva/backend.py")
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


def test_store_preimpostati():
    """I preset: Switch (tre elenchi completi), PS4 e PS5 (superpsx) — e una copia indipendente dalla costante."""
    pre = M.presets()
    assert pre["switch"] == [
        "https://nxbrew.net/game-index/",
        "https://www.ziperto.com/nintendo-switch-nsp/",
        "https://www.ziperto.com/nintendo-switch-xci/",
    ], pre
    assert pre["ps4"] == ["https://www.superpsx.com/category/ps4/"], pre
    assert pre["ps5"] == ["https://www.superpsx.com/category/ps5/"], pre
    pre["switch"].append("https://esempio.test/")  # il chiamante non deve modificare PRESETS
    assert "https://esempio.test/" not in M.PRESETS["switch"], M.PRESETS
    print("ok  preset: Switch (NXBrew, Ziperto NSP/XCI), PS4 e PS5 (superpsx), copia indipendente")


def test_merge_salta_store_presenti():
    """Uno store già presente non si raddoppia né si rilegge: si legge solo quello nuovo."""
    cats = {"switch": ["https://nxbrew.net/game-index/"],
            "ps5": {"urls": ["https://www.superpsx.com/category/ps5/"], "type": "type_c"}}
    found = {"switch": ["https://nxbrew.net/game-index/", "https://www.ziperto.com/nintendo-switch-xci/"],
             "ps5": ["https://www.superpsx.com/category/ps5"],  # stessa pagina scritta senza la barra finale
             "ps4": ["https://www.superpsx.com/category/ps4/"]}
    cats, added, todo = M.merge_catalogs(cats, found, lambda pid, u: True)
    assert added == {"switch": ["https://www.ziperto.com/nintendo-switch-xci/"],
                     "ps4": ["https://www.superpsx.com/category/ps4/"]}, added
    assert todo == added, todo
    assert cats["ps5"] == {"urls": ["https://www.superpsx.com/category/ps5/"], "type": "type_c"}, cats["ps5"]
    assert cats["switch"] == ["https://nxbrew.net/game-index/", "https://www.ziperto.com/nintendo-switch-xci/"], cats

    # presente ma mai letto (nessun gioco in cache): non si aggiunge, però si legge
    cats2, added2, todo2 = M.merge_catalogs({"ps5": ["https://www.superpsx.com/category/ps5/"]},
                                            {"ps5": ["https://www.superpsx.com/category/ps5/"]},
                                            lambda pid, u: False)
    assert added2 == {} and todo2 == {"ps5": ["https://www.superpsx.com/category/ps5/"]}, (added2, todo2)
    assert M.store_key("https://www.Esempx.test/a/b/") == M.store_key("http://esempx.test/a/b"), M.store_key("x")
    # stessa pagina scritta in due modi tra le novità: si aggiunge una volta sola
    _, added3, todo3 = M.merge_catalogs({}, {"ps5": ["https://esempx.test/p/", "https://www.esempx.test/p"]},
                                        lambda pid, u: True)
    assert added3 == {"ps5": ["https://esempx.test/p/"]} == todo3, (added3, todo3)
    print("ok  merge: store già presenti saltati, si legge solo il nuovo (e quello mai letto)")


if __name__ == "__main__":
    test_collect()
    test_platform_hints()
    test_store_preimpostati()
    test_merge_salta_store_presenti()
    print("TUTTI I TEST OK")
