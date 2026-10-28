"""Test di giochi gratis (allkeyshop), HowLongToBeat, stato dei giochi e lista desideri. Nessuna richiesta online."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pozzo import freegames, hltb  # noqa: E402
from pozzo.library.userdata import UserData  # noqa: E402
from pozzo.wishlist import _alert  # noqa: E402

HERE = Path(__file__).parent


def test_allkeyshop_live_widget_e_blocco():
    """Widget reale (2026-10-24, con Prime) e comportamento quando allkeyshop non risponde."""
    items = freegames.parse_aks((HERE / "fixtures" / "aks_widget_live.html").read_text(encoding="utf-8"))
    prime = [i for i in items if i["kind"] == "Free with Prime"]
    assert len(items) == 16 and len(prime) == 9 and prime[0]["url"].startswith("https://luna.amazon.com/")

    class KV:
        d = {"freegames_aks": {"items": [{"title": "Hue", "url": "u", "kind": "Free with Prime"}], "next": 0}}
        kv_get = lambda self, k: self.d.get(k)  # noqa: E731
        kv_set = lambda self, k, v: self.d.__setitem__(k, v)  # noqa: E731

    def boom(*a, **k):
        raise freegames.httpx.ConnectTimeout("blocco")
    saved = freegames.fetch_aks, freegames.fetch_prime, freegames.fetch_steam, freegames.fetch_gamerpower
    freegames.fetch_aks, freegames.fetch_prime, freegames.fetch_steam, freegames.fetch_gamerpower = boom, lambda c: [], lambda c: [], lambda c: []
    try:
        kv = KV()
        r = freegames.get(kv, force=True)
        assert [i["title"] for i in r["items"]] == ["Hue"] and r["errors"], r  # elenco precedente mantenuto
        assert kv.d["freegames_aks"]["next"] > 0  # e non si riprova subito
    finally:
        freegames.fetch_aks, freegames.fetch_prime, freegames.fetch_steam, freegames.fetch_gamerpower = saved


def test_prime_feed():
    """Feed Atom di LootScraper per Prime Gaming + unione senza duplicati con allkeyshop."""
    items = freegames.parse_prime((HERE / "fixtures" / "prime_feed.xml").read_text(encoding="utf-8"))
    titles = [i["title"] for i in items]
    assert titles == ["Vampire: The Masquerade: Swansong", "Time Travel: Escape Room Game", "Ad Infinitum"], titles
    ad = items[2]
    assert ad["kind"] == "Free with Prime" and ad["drm"] == "amazon" and ad["console"] == "pc", ad
    assert ad["url"].startswith("https://luna.amazon.com/claims/"), ad["url"]
    assert ad["cover"].startswith("https://m.media-amazon.com/images/"), ad["cover"]
    assert ad["end"] == "2026-11-25 00:00" and ad["worth"] == "19.99 EUR", ad

    class KV:
        def __init__(self):
            self.d = {}
        def kv_get(self, k):
            return self.d.get(k)
        def kv_set(self, k, v):
            self.d[k] = v

    saved = freegames.fetch_aks, freegames.fetch_prime, freegames.fetch_steam, freegames.fetch_gamerpower
    # allkeyshop restituisce gia' "Ad Infinitum" (senza data di fine), Prime restituisce i 3 giochi del fixture
    freegames.fetch_aks = lambda c: [{"title": "Ad Infinitum", "full_title": "Ad Infinitum", "url": "https://luna.amazon.com/claims/x",
                                      "cover": None, "kind": "Free with Prime", "console": "pc", "drm": "amazon", "source": "allkeyshop"}]
    freegames.fetch_prime = lambda c: [dict(x) for x in items]
    freegames.fetch_steam = lambda c: []
    freegames.fetch_gamerpower = lambda c: []
    try:
        kv = KV()
        r = freegames.get(kv, force=True)
        got = {i["title"]: i for i in r["items"]}
        assert len(r["items"]) == 3 and set(got) == set(titles), r["items"]  # nessun duplicato per Ad Infinitum
        assert got["Ad Infinitum"]["source"] == "allkeyshop" and got["Ad Infinitum"]["end"] == "2026-11-25 00:00"
        assert kv.d["freegames_prime"]["next"] > 0
    finally:
        freegames.fetch_aks, freegames.fetch_prime, freegames.fetch_steam, freegames.fetch_gamerpower = saved


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


def test_profiles_and_shots(tmp):
    from PIL import Image

    from pozzo import config as C
    from pozzo import platforms as P
    from pozzo.library import launcher as L
    from pozzo.library.screens import Shots, parse_hotkey
    d = Path(tmp)
    for n in ("pcsx2.exe", "pcsx2-old.exe"):
        (d / n).write_text("x")
    cfg = C.Config()
    cfg.emulators = {"pcsx2": str(d / "pcsx2.exe")}
    ps2 = P.PLATFORMS["ps2"]
    assert L.plan(cfg, ps2)["exe"].endswith("pcsx2.exe") and not L.plan(cfg, ps2)["profile"]
    pl = L.plan(cfg, ps2, {"exe": str(d / "pcsx2-old.exe"), "extra": "-fullscreen"})
    assert pl["exe"].endswith("pcsx2-old.exe") and pl["args"] == ["-batch", "-fullscreen", "{rom}"] and pl["profile"]
    try:
        L.plan(cfg, ps2, {"exe": str(d / "manca.exe")})
        raise AssertionError("eseguibile mancante accettato")
    except L.LaunchError:
        pass
    sh = Shots(d, d / "u.db")
    sh.add_image("ps2/x", Image.new("RGB", (40, 30)))
    f = d / "emu.png"
    Image.new("RGB", (8, 8)).save(f)
    assert sh.add_file("ps2/x", f, "pcsx2") and sh.add_file("ps2/x", f, "pcsx2") is None  # niente doppioni
    assert sh.counts() == {"ps2/x": 2} and sh.path(sh.list()[0]["id"]).exists()
    sh.delete(sh.list()[0]["id"])
    assert sh.counts() == {"ps2/x": 1}
    sh.db.close()
    assert parse_hotkey("ctrl+shift+s") and parse_hotkey("f10") and parse_hotkey("ctrl+boh") is None


def test_remote():
    import struct
    import zlib

    from pozzo.remote import phone, sunshine
    from pozzo.remote.dsu import DSUServer
    # cancello del telefono: solo le funzioni previste
    ok = lambda m, p: any(mm == m and r.match(p) for mm, r in phone._ALLOWED)  # noqa: E731
    assert ok("GET", "/api/library") and ok("POST", "/api/game/12/play") and ok("WS", "/m/ws")
    assert not ok("GET", "/api/phone") and not ok("POST", "/api/open") and not ok("POST", "/api/game/1/open-folder")
    assert not ok("POST", "/api/scan") and not ok("GET", "/api/settings") and not ok("POST", "/api/play-key")
    # DSU: risposta alla richiesta di versione con CRC valido
    srv = DSUServer(port=0)
    body = struct.pack("<I", 0x100000)
    pk = bytearray(struct.pack("<4sHHII", b"DSUC", 1001, len(body), 0, 1) + body)
    struct.pack_into("<I", pk, 8, zlib.crc32(pk))
    (r,) = srv.handle(bytes(pk), ("127.0.0.1", 1))
    chk = bytearray(r)
    crc = struct.unpack_from("<I", chk, 8)[0]
    struct.pack_into("<I", chk, 8, 0)
    assert r[:4] == b"DSUS" and zlib.crc32(chk) == crc and struct.unpack_from("<H", r, 20)[0] == 1001
    pk[-1] ^= 1  # CRC sbagliato: ignorato
    assert srv.handle(bytes(pk), ("127.0.0.1", 1)) == []
    # Sunshine: si riconoscono solo le app di PozzoLauncher
    assert sunshine.ours({"cmd": '"x.exe" play --key "snes/a"'}) and sunshine.ours({"cmd": '"x.exe" bigpicture'})
    assert not sunshine.ours({"cmd": "steam://open/bigpicture"}) and not sunshine.ours({"name": "Desktop"})


if __name__ == "__main__":
    test_allkeyshop()
    test_allkeyshop_live_widget_e_blocco()
    test_prime_feed()
    test_hltb()
    with tempfile.TemporaryDirectory() as tmp:
        test_status_and_wishlist(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        test_profiles_and_shots(tmp)
    test_remote()
    print("TUTTI I TEST OK")
