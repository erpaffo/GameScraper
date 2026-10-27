"""Controller speciali del telefono: DSU con i tasti, configurazione di PCSX2/Dolphin, mouse, microfono, sala giocatori.

Esegui: python tests/test_controllers.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gamescraper.remote import emusetup, mic, mouse  # noqa: E402
from gamescraper.remote.dsu import pad_bytes  # noqa: E402
from gamescraper.remote.phone import PhoneHub  # noqa: E402
from gamescraper.remote.vigem import BUTTONS as B  # noqa: E402


def test_dsu_pad():
    rest = pad_bytes()
    assert len(rest) == 20 and rest[4:8] == bytes([128] * 4) and rest[:4] == bytes(4)
    p = pad_bytes(B["a"] | B["up"] | B["back"] | B["guide"] | B["lb"], lt=1.0, lx=1.0, ly=-1.0)
    assert p[0] == 0x10 | 0x01, p[0]          # su + Share
    assert p[1] == 0x20 | 0x04 | 0x01, p[1]   # Croce + L1 + L2
    assert p[2] == 1 and p[4] == 255 and p[5] == 1
    assert p[11] == 255 and p[14] == 255 and p[19] == 255 and p[18] == 0  # analogici: su, Croce, L2
    print("ok  DSU: tasti Xbox nel formato DualShock")


def test_ini():
    text = "; commento\n[A]\nx = 1\ny = 2\n\n[B]\nz = 3\nw = 4\n"
    out = emusetup.ini_update(text, {"A": {"y": "9", "n": "new"}, "B": {"q": "1"}, "C": {"k": "v"}}, replace={"B"})
    assert out.startswith("; commento\n[A]\nx = 1\ny = 9\nn = new\n"), out
    assert "[B]\nq = 1\n" in out and "z = 3" not in out and out.rstrip().endswith("[C]\nk = v"), out
    assert emusetup.ini_get(out, "A", "y") == "9" and emusetup.ini_get(out, "B", "z") is None
    print("ok  ini: chiavi cambiate, resto intatto, sezioni sostituite")


def test_pcsx2_dolphin_setup():
    ch, rep = emusetup.pcsx2_changes("buzz", 2)
    assert ch["USB1"]["Type"] == "BuzzDevice" and ch["USB1"]["BuzzDevice_Red1"] == "XInput-0/A"
    assert ch["USB1"]["BuzzDevice_Yellow2"] == "XInput-1/RightShoulder" and "BuzzDevice_Red3" not in ch["USB1"]
    assert ch["InputSources"] == {"XInput": "true"} and rep == {"USB1"}
    ch, rep = emusetup.pcsx2_changes("guitar", 1)
    assert ch["Pad1"]["Type"] == "Guitar" and ch["Pad1"]["Orange"] == "XInput-0/LeftShoulder" and rep == {"Pad1"}
    assert emusetup.dsu_entries("DS4:192.168.1.5:26760;") == "DS4:192.168.1.5:26760;GameScraper:127.0.0.1:26760;"
    assert emusetup.dsu_entries("X:127.0.0.1:26760;") == "X:127.0.0.1:26760;"  # server gia' presente
    k = emusetup.wiimote_keys(1)
    assert k["Device"] == "DSUClient/1/GameScraper" and k["Buttons/A"] == "Cross" and k["IMUGyroscope/Yaw Left"] == "`Gyro Yaw Left`"

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "pcsx2").mkdir()
        (td / "pcsx2" / "portable.txt").write_text("")
        ini = td / "pcsx2" / "inis" / "PCSX2.ini"
        ini.parent.mkdir()
        ini.write_text("[InputSources]\nSDL = true\nXInput = false\n\n[USB1]\nType = None\n\n[Pad1]\nType = DualShock2\n", encoding="utf-8")
        emus = {"pcsx2": str(td / "pcsx2" / "pcsx2-qt.exe"), "dolphin": str(td / "dolphin" / "Dolphin.exe")}
        r = emusetup.setup("pcsx2_buzz", emus, 4)
        t = ini.read_text(encoding="utf-8")
        assert "XInput = true" in t and "SDL = true" in t and "Type = BuzzDevice" in t and "BuzzDevice_Green4 = XInput-3/B" in t, t
        assert "Type = DualShock2" in t and len(r["backups"]) == 1 and Path(r["backups"][0]).read_text().count("XInput = false") == 1
        emusetup.restore("pcsx2_buzz", emus)
        assert "BuzzDevice" not in ini.read_text(encoding="utf-8")

        (td / "dolphin").mkdir()
        (td / "dolphin" / "portable.txt").write_text("")
        cfgd = td / "dolphin" / "User" / "Config"
        cfgd.mkdir(parents=True)
        (cfgd / "WiimoteNew.ini").write_text("[Wiimote1]\nDevice = DInput/0/Keyboard Mouse\nButtons/A = `Click 0`\n[Wiimote2]\nDevice = X\n")
        r = emusetup.setup("dolphin_wii", emus, 1)
        wm = (cfgd / "WiimoteNew.ini").read_text(encoding="utf-8")
        assert "Device = DSUClient/0/GameScraper" in wm and "Click 0" not in wm and "Source = 1" in wm and "[Wiimote2]\nDevice = X" in wm, wm
        assert "Entries = GameScraper:127.0.0.1:26760;" in (cfgd / "DSUClient.ini").read_text(encoding="utf-8")
        assert (cfgd / "Profiles" / "Wiimote" / "GameScraper Telefono 1.ini").read_text(encoding="utf-8").startswith("[Profile]\nDevice = DSUClient/0/")
    print("ok  PCSX2 (Buzz, chitarra) e Dolphin (Wii Remote via DSU) configurati, con copia e ripristino")


def test_mouse_mic():
    assert mouse.to_abs(0) == 0 and mouse.to_abs(1) == 65535 and mouse.to_abs(2) == 65535
    assert mouse.events(True, False, (False, False)) == [mouse.MOVE | mouse.ABSOLUTE, mouse.LEFTDOWN]
    assert mouse.events(False, False, (True, True)) == [mouse.MOVE | mouse.ABSOLUTE, mouse.LEFTUP, mouse.RIGHTUP]
    assert mouse.events(True, False, (True, False)) == [mouse.MOVE | mouse.ABSOLUTE]
    names = ["Cuffie", "Altoparlanti (Steam Streaming M", "CABLE Input (VB-Audio Virtual C"]
    assert mic.pick(names) == 2 and mic.pick(names, "cuffie") == 0 and mic.pick(["Cuffie"]) is None
    print("ok  mouse (eventi assoluti) e scelta dell'uscita audio per il microfono")


class FakePad:
    def __init__(self, idx):
        self.idx, self.closed = idx, False

    def user_index(self):
        return self.idx

    def close(self):
        self.closed = True


def test_swap():
    class Cfg:
        app: dict = {}
    hub = PhoneHub(Cfg(), lambda *a, **k: None)
    a, b = hub.open_slot("d1", "Anna"), hub.open_slot("d2", "Bruno")
    pa, pb = FakePad(0), FakePad(1)
    hub.slots[a]["pad"], hub.slots[b]["pad"] = pa, pb
    cid_a = hub.slots[a]["cid"]
    assert [(p["player"], p["name"]) for p in hub.players()] == [(1, "Anna"), (2, "Bruno")]
    rev = hub.rev
    hub.swap(a, b)
    assert [(p["player"], p["name"]) for p in hub.players()] == [(1, "Bruno"), (2, "Anna")] and hub.rev > rev
    assert hub.slots[0]["pad"] is pa and hub.slot_of(cid_a) == 1  # il controller virtuale resta al suo posto
    hub.swap(1, 3)  # Anna va al posto 4 (libero): il controller del posto 2 si scollega
    assert sorted(hub.slots) == [0, 3] and pb.closed and hub.slots[3]["pad"] is None and hub.slot_of(cid_a) == 3
    try:
        hub.swap(1, 2)
        raise AssertionError("posti vuoti accettati")
    except ValueError:
        pass
    print("ok  sala giocatori: scambio dei posti senza ricollegarsi")


if __name__ == "__main__":
    test_dsu_pad()
    test_ini()
    test_pcsx2_dolphin_setup()
    test_mouse_mic()
    test_swap()
    print("test_controllers: OK")
