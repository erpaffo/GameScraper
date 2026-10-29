"""Minerva: magnet del set con so=<indice> per scaricare un solo gioco."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pozzo import catalog  # noqa: E402

PAGE = ('<script>{"torrents":"x.torrent","so_id":17,"magnet":"magnet:?xt=urn:btih:abc&dn=Minerva_Myrient"}</script>'
        '<a href="magnet:?xt=urn:btih:abc&amp;dn=Minerva_Myrient&amp;so=3&amp;tr=udp%3A%2F%2Ft%3A1">Open Magnet Link</a>')


def test_single_file_magnet():
    m = catalog.single_file_magnet(PAGE)
    assert m.endswith("&so=17") and m.count("so=") == 1, m   # indice della pagina, un vecchio so= si toglie
    assert "&tr=" in m, m                                        # magnet completo, con i tracker
    assert catalog.single_file_magnet("<p>niente</p>") is None
    assert catalog.MINERVA_ROM.match("https://minerva-archive.org/rom?id=2895799")
    assert not catalog.MINERVA_ROM.match("https://minerva-archive.org/browse/No-Intro/")


if __name__ == "__main__":
    test_single_file_magnet()
    print("ok")
