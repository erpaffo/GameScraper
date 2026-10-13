"""Test di giochi gratis (allkeyshop), HowLongToBeat, stato dei giochi e lista desideri. Nessuna richiesta online."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamescraper import freegames, hltb  # noqa: E402
from gamescraper.library.userdata import UserData  # noqa: E402
from gamescraper.wishlist import _alert  # noqa: E402

HERE = Path(__file__).parent


def test_allkeyshop():
    items = freegames.parse_aks((HERE / "fixtures" / "aks_free.html").read_text(encoding="utf-8"))
    titles = [i["title"] for i in items]
    assert titles == ["F1 25", "TerraScape", "Wartales"], titles  # la copia del carosello e' scartata
    t = {i["title"]: i for i in items}
    assert t["TerraScape"]["drm"] == "epic" and t["TerraScape"]["kind"] == "Free to keep"
    assert t["F1 25"]["console"] == "playstation" and t["F1 25"]["kind"] == "Free with PS+"
    assert t["Wartales"]["url"].startswith("https://www.xbox.com/")
    assert freegames._GP_SUFFIX.sub("", "Dwarven Realms (Steam) Key") == "Dwarven Realms"


class _R:
    def __init__(self, name, sim, plats, main):
        self.game_name, self.similarity, self.profile_platforms = name, sim, plats
        self.main_story, self.main_extra, self.completionist = main, main * 1.5, main * 3
        self.game_web_link, self.game_id = "https://howlongtobeat.com/game/1", 1


def test_hltb():
    res = [_R("Doom", 1.0, ["PC"], 12), _R("Doom", 1.0, ["Super Nintendo"], 8), _R("Doom II", 0.6, ["PC"], 9)]
    r = hltb.lookup("Doom", "snes", search=lambda t: res)
    assert r["main"] == 8, r  # a parita' di nome vince la piattaforma giusta
    assert hltb.lookup("Qualcosa", "snes", search=lambda t: [_R("Altro", 0.3, [], 5)]) == {}
    # capitolo diverso: mai i tempi di "Civilization V" per "Civilization VI"
    assert hltb.lookup("Civilization VI", "windows", search=lambda t: [_R("Civilization V", 0.98, ["PC"], 40)]) == {}
    assert hltb.lookup("Final Fantasy VII", "psx", search=lambda t: [_R("Final Fantasy 7", 0.8, [], 38)])["main"] == 38
    assert hltb._COMPILATION.search("2 Games in 1 - Finding Nemo") and not hltb._COMPILATION.search("Super Mario 64")


def test_status_and_wishlist(tmp):
    u = UserData(Path(tmp) / "u.db")
    u.set_flag("snes/a", "status", "backlog")
    u.record_play("snes/a", 600)
    assert u.games()["snes/a"]["status"] == "playing"  # avviato: da "da giocare" a "in corso"
    u.set_flag("snes/a", "status", "done")
    u.record_play("snes/a", 60)
    assert u.games()["snes/a"]["status"] == "done"  # un gioco finito resta finito
    assert len(u.sessions()) == 2
    u.record_play("snes/b", 5)
    assert u.games()["snes/b"]["status"] == "playing"
    u.wish_add("steam:1", "Gioco", "1", target=10)
    u.wish_update("steam:1", price=8.0, cut=20)
    w = u.wishlist()[0]
    assert w["price"] == 8.0 and w["target"] == 10
    assert _alert(w, 8.0, 10.0, 20, None, 50).startswith("Sotto il tuo prezzo")
    assert _alert({"target": None}, 5.0, 20.0, 75, None, 50) == "Sconto del 75%"
    assert _alert({"target": None}, 0, 20.0, 100, None, 50) == "Gratis!"
    assert _alert({"target": None}, 15.0, 20.0, 25, None, 50) == ""
    u.wish_delete("steam:1")
    assert not u.wishlist()
    u.db.close()


if __name__ == "__main__":
    test_allkeyshop()
    test_hltb()
    with tempfile.TemporaryDirectory() as tmp:
        test_status_and_wishlist(tmp)
    print("TUTTI I TEST OK")
