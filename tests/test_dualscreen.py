"""Telefono come schermo touch per DS/3DS: configurazione degli emulatori (scritta e rimessa com'era), decisione,
ritaglio dello schermo, ciclo nel server e cancello di rete. Nessun emulatore vero e nessuna finestra."""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gamescraper.remote import dualscreen as D  # noqa: E402
from gamescraper.remote.emusetup import ini_get  # noqa: E402

MELON = """[Instance0]
JoystickID = 0

[Instance0.Window1]
Enabled = false

[Instance0.Window0]
Enabled = true
ScreenSizing = 0
ScreenLayout = 0
"""
CITRA = """[Layout]
render_3d\\default=true
render_3d=0
layout_option\\default=true
layout_option=0
"""


def test_config_roundtrip():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        d = Path(t)
        # melonDS portatile: melonDS.toml accanto all'exe
        (d / "melon").mkdir()
        exe = d / "melon" / "melonDS.exe"
        toml = d / "melon" / "melonDS.toml"
        toml.write_text(MELON, encoding="utf-8")
        st = D.apply("melonds", str(exe))
        txt = toml.read_text(encoding="utf-8")
        assert ini_get(txt, "Instance0.Window0", "ScreenSizing") == "4" and ini_get(txt, "Instance0.Window1", "Enabled") == "true"
        assert ini_get(txt, "Instance0.Window1", "ScreenSizing") == "5", txt
        # l'emulatore, uscendo, riscrive il file a modo suo (ordine diverso, chiavi in piu')
        toml.write_text(txt.replace("[Instance0]\nJoystickID = 0\n", "") + "\n[Instance0]\nJoystickID = 1\n", encoding="utf-8")
        D.restore(st)
        txt = toml.read_text(encoding="utf-8")
        assert ini_get(txt, "Instance0.Window0", "ScreenSizing") == "0" and ini_get(txt, "Instance0.Window1", "Enabled") == "false"
        assert ini_get(txt, "Instance0.Window1", "ScreenSizing") is None, "una chiave che prima non c'era viene tolta"
        assert ini_get(txt, "Instance0", "JoystickID") == "1", "il resto salvato dall'emulatore resta"

        # Citra portatile (cartella user): qt-config.ini scritto come fa Qt, senza spazi attorno a =
        (d / "citra" / "user" / "config").mkdir(parents=True)
        cexe = d / "citra" / "citra-qt.exe"
        ini = d / "citra" / "user" / "config" / "qt-config.ini"
        ini.write_text(CITRA, encoding="utf-8")
        st = D.apply("citra", str(cexe))
        txt = ini.read_text(encoding="utf-8")
        assert "layout_option=4" in txt and "layout_option\\default=false" in txt and " = " not in txt, txt
        D.restore(st)
        assert ini.read_text(encoding="utf-8").splitlines() == CITRA.splitlines()
    print("ok  configurazione di melonDS e Citra scritta e rimessa com'era (anche dopo che l'emulatore riscrive il file)")


def test_decision_and_geometry():
    real = D.supported
    D.supported = lambda: True
    try:
        assert D.wanted("nds", "melonds", True, True)
        assert D.wanted("3ds", "citra", True, True) and D.wanted("3ds", "Azahar", True, True)
        assert not D.wanted("nds", "melonds", True, False), "senza telefono: i due schermi sul PC"
        assert not D.wanted("nds", "melonds", False, True), "opzione spenta"
        assert not D.wanted("nds", "retroarch", True, True), "i core di RetroArch non hanno finestre separate"
        assert not D.wanted("3ds", "melonds", True, True) and not D.wanted("gba", "melonds", True, True)
    finally:
        D.supported = real
    # dove sta lo schermo nella finestra: sotto la barra dei menu, 4:3 centrato
    assert D.screen_rect(640, 502, 22) == (0, 22, 640, 502)
    assert D.screen_rect(800, 600, 0) == (0, 0, 800, 600)
    assert D.screen_rect(1000, 500, 0) == (167, 0, 833, 500), "finestra larga: bande ai lati"
    assert D.screen_rect(400, 700, 0) == (0, 200, 400, 500), "finestra alta: bande sopra e sotto"
    assert D.client_point(1.0, 1.0, 640, 480) == (639, 479) and D.client_point(0.5, 0.25, 640, 480) == (320, 120)
    print("ok  quando usare il telefono e dove cade il tocco sullo schermo dell'emulatore")


def test_nomi_emulatore_misti_e_casi_limite():
    """Il nome dell'emulatore in config può essere scritto come vuole l'utente; un errore non deve impedire di giocare."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        d = Path(t)
        (d / "melon").mkdir()
        exe, toml = d / "melon" / "melonDS.exe", d / "melon" / "melonDS.toml"
        toml.write_text(MELON, encoding="utf-8")
        for name in ("melonDS", " MelonDS ", "MELONDS"):
            assert D.family(name) == "melon", name
            st = D.apply(name, str(exe))          # nessun KeyError con maiuscole e spazi
            assert st["emulator"] == "melonds" and st["family"] == "melon", st
            D.restore(st)
        assert D.family("retroarch") is None
        try:
            D.apply("retroarch", str(exe))
        except ValueError as e:
            assert "non gestito" in str(e), e
        else:
            raise AssertionError("doveva rifiutare un emulatore non gestito")
        # config non scrivibile (cartella inesistente e permessi negati): errore chiaro, non un crash silenzioso
        assert D.config_file("melonds", str(exe)) == toml
        azahar = D.config_file("Azahar", str(d / "azahar" / "azahar.exe"))
        assert azahar.name == "qt-config.ini" and "Azahar" in str(azahar), azahar
    print("ok  nomi emulatore con maiuscole/spazi e casi limite")


def test_telefono_collegato_anche_senza_controller():
    """«Collegato» = controller aperto oppure app del telefono vista da poco: non serve il pad aperto."""
    from fastapi.testclient import TestClient

    from gamescraper import config
    from gamescraper.app.server import create_app
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        d = Path(t)
        (d / "melon").mkdir()
        exe, toml = d / "melon" / "melonDS.exe", d / "melon" / "melonDS.toml"
        toml.write_text(MELON, encoding="utf-8")
        cfg = config.Config(cache=d / "cache", output=d / "out")
        cfg.config_path = d / "config.toml"
        app = create_app(cfg)
        with TestClient(app) as c:  # chiuso a fine test (database liberi)
            launcher, hub = app.state.launcher, app.state.phone_hub
            st = {"key": "nds/abc", "emulator_id": "melonDS", "exe": str(exe)}
            real = D.supported
            D.supported = lambda: True
            try:
                # dispositivo abbinato e visto adesso, ma nessun controller aperto
                hub._save(devices=[{"id": "a", "name": "Pixel", "kind": "phone", "token": "x", "last": time.time()}])
                assert launcher.prelaunch(dict(st)) == ["-f"], "telefono collegato senza il pad aperto"
                launcher.on_event("game_stopped", {})
                # visto molto tempo fa: non è collegato
                hub._save(devices=[{"id": "a", "name": "Pixel", "kind": "phone", "token": "x",
                                    "last": time.time() - 3600}])
                assert launcher.prelaunch(dict(st)) == [], "telefono non collegato: due schermi sul PC"
                assert ini_get(toml.read_text(encoding="utf-8"), "Instance0.Window0", "ScreenSizing") == "0"
                # emulatore non gestito: si gioca normalmente (nessuna eccezione che blocchi l'avvio)
                bad = {"key": "nds/abc", "emulator_id": "retroarch", "exe": str(exe)}
                assert launcher.prelaunch(dict(bad)) == []
                assert c.get("/api/running").json()["dual"] is False
            finally:
                D.supported = real
    print("ok  telefono collegato = controller o app aperta; emulatore non gestito non blocca il gioco")


def test_server_cycle_and_gate():
    from fastapi.testclient import TestClient

    from gamescraper import config
    from gamescraper.app.server import create_app
    from gamescraper.remote import phone as PH
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        d = Path(t)
        (d / "melon").mkdir()
        exe, toml = d / "melon" / "melonDS.exe", d / "melon" / "melonDS.toml"
        toml.write_text(MELON, encoding="utf-8")
        cfg = config.Config(cache=d / "cache", output=d / "out")
        cfg.config_path = d / "config.toml"
        app = create_app(cfg)
        c = TestClient(app)
        launcher, hub = app.state.launcher, app.state.phone_hub
        st = {"key": "nds/abc", "emulator_id": "melonds", "exe": str(exe)}
        real = D.supported
        D.supported = lambda: True
        try:
            assert launcher.prelaunch(dict(st)) == [], "nessun telefono collegato: avvio normale"
            assert ini_get(toml.read_text(encoding="utf-8"), "Instance0.Window0", "ScreenSizing") == "0"
            hub.slots[0] = {"cid": "x", "device": "d", "pad": None, "motion": False, "name": "Telefono"}
            assert launcher.prelaunch(dict(st)) == ["-f"]
            assert ini_get(toml.read_text(encoding="utf-8"), "Instance0.Window0", "ScreenSizing") == "4"
            assert c.get("/api/running").json()["dual"] is True
            assert config.read_settings(cfg)["app"]["dualscreen"]["file"] == str(toml), "salvato per il ripristino"
            launcher.on_event("game_stopped", {})
            assert ini_get(toml.read_text(encoding="utf-8"), "Instance0.Window0", "ScreenSizing") == "0"
            assert "dualscreen" not in (config.read_settings(cfg).get("app") or {})
            assert c.get("/api/running").json()["dual"] is False
            # app chiusa a meta' partita: al prossimo avvio la configurazione torna com'era
            launcher.prelaunch(dict(st))
            create_app(cfg)
            assert ini_get(toml.read_text(encoding="utf-8"), "Instance0.Window0", "ScreenSizing") == "0"
            # opzione spenta dal PC
            c.post("/api/phone", json={"touchscreen": False})
            assert launcher.prelaunch(dict(st)) == []
        finally:
            D.supported = real
            hub.slots.clear()
    ok = lambda rules, p: any(m == "WS" and r.match(p) for m, r in rules)  # noqa: E731
    assert ok(PH._ALLOWED, "/m/screen") and not ok(PH._PC_ALLOWED, "/m/screen"), "solo i telefoni abbinati"
    print("ok  ciclo nel server (configura, gioca, ripristina; anche dopo una chiusura a meta') e cancello di rete")


if __name__ == "__main__":
    test_config_roundtrip()
    test_decision_and_geometry()
    test_nomi_emulatore_misti_e_casi_limite()
    test_telefono_collegato_anche_senza_controller()
    test_server_cycle_and_gate()
    print("TUTTI I TEST OK")
