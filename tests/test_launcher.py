"""Test di configurazione e costruzione comando di avvio per Eden (Switch). Nessun processo avviato.

Esegui: python tests/test_launcher.py
"""

import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pozzo import config as C  # noqa: E402
from pozzo import platforms as P  # noqa: E402
from pozzo.library import launcher as L  # noqa: E402


def test_eden_plan_and_command(tmp: Path):
    emu_dir = tmp / "Emulatori" / "Eden"
    emu_dir.mkdir(parents=True)
    eden_exe = emu_dir / "eden.exe"
    eden_exe.write_bytes(b"")

    found = L.detect_emulators(tmp / "Emulatori")
    assert found == {"eden": str(eden_exe)}, found
    print("ok  ricerca automatica di eden.exe")

    cfg = C.Config()
    cfg.emulators = found
    switch = P.get("switch")

    # Avvio standard: usa il preset [-g, {rom}]
    pl = L.plan(cfg, switch)
    assert pl is not None and pl["emulator"] == "eden", pl
    assert pl["exe"] == str(eden_exe)
    assert pl["args"] == ["-g", "{rom}"], pl["args"]

    # Avvio con argomento extra (es. fullscreen -f): -f deve precedere -g {rom}
    pl_fs = L.plan(cfg, switch, {"extra": "-f"})
    assert pl_fs is not None and pl_fs["args"] == ["-f", "-g", "{rom}"], pl_fs

    # Gioco in cartella con base [000][v0] + update [800] (anche senza "update" nel nome del file)
    game_dir = tmp / "roms" / "switch" / "Gioco di Prova"
    upd_dir = game_dir / "Update 1.2.0"
    upd_dir.mkdir(parents=True)
    base_nsp = game_dir / "Gioco di Prova [0100ABCDEF000000][v0].nsp"
    upd_nsp = upd_dir / "Gioco di Prova [0100ABCDEF000800][1.2.0][196608].nsp"
    base_nsp.write_bytes(b"a" * 100)
    upd_nsp.write_bytes(b"b" * 200)  # anche se l'update e' piu' grande, vince il gioco base

    target = L.resolve_target(switch, game_dir)
    assert target == base_nsp, target

    cmd = [pl["exe"], *[a.replace("{rom}", str(target)) for a in pl["args"]]]
    assert cmd == [str(eden_exe), "-g", str(base_nsp)], cmd
    cmd_fs = [pl_fs["exe"], *[a.replace("{rom}", str(target)) for a in pl_fs["args"]]]
    assert cmd_fs == [str(eden_exe), "-f", "-g", str(base_nsp)], cmd_fs
    print("ok  comando di avvio Switch con Eden (standard, fullscreen e gioco in cartella)")


class FakeUser:
    def __init__(self):
        self.plays = []

    def record_play(self, key, secs):
        self.plays.append(key)

    def mark_started(self, key):
        pass


def test_window_process_tree():
    class Process:
        children_by_pid = {10: [11, 12, 14], 11: [13], 12: [], 13: [], 14: [15], 15: [],
                           20: [21], 21: []}
        names_by_pid = {10: "game.exe", 11: "launcher.exe", 12: "helper.exe", 13: "game-renderer.exe",
                        14: "explorer.exe", 15: "explorer-child.exe", 20: "custom-launcher.exe",
                        21: "game.exe"}

        def __init__(self, pid):
            self.pid = pid

        def children(self):
            return [Process(pid) for pid in self.children_by_pid[self.pid]]

        def name(self):
            return self.names_by_pid[self.pid]

    fake_psutil = SimpleNamespace(Error=RuntimeError, Process=Process)
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        assert L._process_tree(10) == {10: 0, 11: 1, 12: 1, 13: 2}
        assert L._process_tree(14) == {}
        assert L._process_tree(20) == {21: 1}
    print("ok  modalità finestra limitata al processo avviato e ai suoi figli")


def test_scripts_and_close_launcher(tmp: Path):
    import time
    log = tmp / "log.txt"
    hook = tmp / "hook.py"
    hook.write_text("import os, sys\nopen(sys.argv[1], 'a').write(sys.argv[2] + ':' + os.environ['POZZO_TITLE'] + '\\n')\n")
    run = lambda tag: f'"{sys.executable}" "{hook}" "{log}" {tag}'  # noqa: E731
    cfg = C.Config()
    cfg.app["launch"] = {"before": run("all-before"), "after": run("all-after"), "close_launcher": True}
    prof = {"before": run("game-before"), "after": run("game-after")}
    # gli script nel profilo non lo rendono un profilo di emulatore
    assert L.plan(cfg, P.get("switch"), prof) == L.plan(cfg, P.get("switch"))

    closed, open_now = [], {"steam.exe": False}
    saved = (L._procs_named, L.close_launcher, L.LAUNCHER_GRACE)
    L._procs_named = lambda name: [1] if open_now.get(name) else []
    L.close_launcher, L.LAUNCHER_GRACE = closed.append, 0
    try:
        for was_open in (False, True):
            log.write_text("")
            open_now["steam.exe"] = was_open
            user = FakeUser()
            ln = L.Launcher(cfg, user, profile_of=lambda k: prof)
            ln.launch_pc("steam:1", "Gioco", {"type": "exe", "target": sys.executable, "args": ["-c", "pass"]}, "", "Steam", "steam")
            open_now["steam.exe"] = True  # il gioco ha aperto Steam
            deadline = time.time() + 30
            while ln.state.get("status") != "finished" and time.time() < deadline:
                time.sleep(0.1)
            time.sleep(0.2)
            assert ln.state.get("status") == "finished" and user.plays == ["steam:1"], ln.state
            assert log.read_text().split() == ["all-before:Gioco", "game-before:Gioco", "game-after:Gioco", "all-after:Gioco"], log.read_text()
        assert closed == ["steam.exe"], closed  # chiuso solo quando l'aveva aperto il gioco
    finally:
        L._procs_named, L.close_launcher, L.LAUNCHER_GRACE = saved
    print("ok  script prima/dopo (tutti i giochi + gioco) e chiusura del launcher")


def test_stop_game(tmp: Path):
    import time
    cfg = C.Config()

    def wait(ln, status, secs=15):
        end = time.time() + secs
        while ln.state.get("status") != status and time.time() < end:
            time.sleep(0.05)
        return ln.state.get("status") == status

    # 1) gioco avviato direttamente: «Chiudi gioco» chiude il processo e lo stato diventa «finished»
    user = FakeUser()
    ln = L.Launcher(cfg, user)
    ln.launch_pc("manual:1", "Lungo", {"type": "exe", "target": sys.executable, "args": ["-c", "import time; time.sleep(60)"]}, "", "manual")
    assert wait(ln, "running"), ln.state
    t0 = time.time()
    assert ln.stop() and ln.stop_found
    assert wait(ln, "finished") and time.time() - t0 < 10 and user.plays == ["manual:1"], ln.state

    # 2) avviato da un launcher (uri), nessun processo da trovare: si interrompe comunque l'attesa del launcher
    saved = (L.open_uri, L._procs_named, L.hide_launcher_windows, L.Launcher._game_procs)
    # niente scansione dei processi veri (lenta sotto carico) ne' finestre da ridurre a icona
    L.open_uri, L._procs_named, L.hide_launcher_windows = (lambda target: None), (lambda name: []), (lambda name: None)
    L.Launcher._game_procs = staticmethod(lambda folder: [])
    try:
        ln = L.Launcher(cfg, FakeUser())
        ln.launch_pc("steam:2", "Da Steam", {"type": "uri", "target": "steam://rungameid/2"}, str(tmp), "Steam", "steam")
        assert wait(ln, "running"), ln.state
        t0 = time.time()
        assert ln.stop() and not ln.stop_found  # niente da chiudere, ma l'esecuzione finisce nel launcher
        assert wait(ln, "finished") and time.time() - t0 < 10, ln.state
    finally:
        L.open_uri, L._procs_named, L.hide_launcher_windows, L.Launcher._game_procs = saved
    # nessun gioco in esecuzione: niente da fermare
    assert not ln.stop()
    print("ok  Chiudi gioco: processo chiuso; se non si trova, l'esecuzione si interrompe comunque")


def test_sharpemu_ps5(tmp: Path):
    """PS5: SharpEmu avvia l'eboot.bin del gioco (cartella), non la cartella."""
    emu_dir = tmp / "SharpEmu"
    emu_dir.mkdir(parents=True)
    exe = emu_dir / "SharpEmu.exe"
    exe.write_bytes(b"")

    found = L.detect_emulators(tmp / "SharpEmu")
    assert found == {"sharpemu": str(exe)}, found

    cfg = C.Config()
    cfg.emulators = found
    ps5 = P.get("ps5")
    assert ps5.name == "PlayStation 5" and ps5.exts == ("pkg",), ps5

    pl = L.plan(cfg, ps5)
    assert pl is not None and pl["emulator"] == "sharpemu", pl
    assert pl["args"] == ["{rom}"], pl["args"]

    game = tmp / "roms" / "ps5" / "Demon's Souls"
    (game / "sce_sys").mkdir(parents=True)
    (game / "eboot.bin").write_bytes(b"a" * 300)
    (game / "sce_sys" / "param.sfo").write_bytes(b"b" * 500)  # piu' grande, ma non e' il gioco

    target = L.resolve_target(ps5, game)
    assert target == game / "eboot.bin", target
    cmd = [pl["exe"], *[a.replace("{rom}", str(target)) for a in pl["args"]]]
    assert cmd == [str(exe), str(game / "eboot.bin")], cmd

    # ogni console con un preset deve esistere davvero nella tabella delle piattaforme
    assert not [k for k in L.PRESETS if k not in P.PLATFORMS], [k for k in L.PRESETS if k not in P.PLATFORMS]
    print("ok  PS5: SharpEmu avviato con l'eboot.bin del gioco (preset e ricerca dell'eseguibile)")


def test_shortcut(tmp: Path):
    from pozzo.library import shortcut as SC
    assert SC.file_name('Zelda: Ocarina of Time? "3D"') == "Zelda Ocarina of Time 3D"
    assert SC.split_cmd('"C:\\a b\\cli.exe" -c "x y"') == ("C:\\a b\\cli.exe", '-c "x y"')
    if sys.platform == "win32":
        saved = SC.desktop_dir
        SC.desktop_dir = lambda: tmp
        try:
            lnk = SC.create("Mario & Luigi: 'Test'", f'"{sys.executable}" -m pozzo', str(tmp), "snes/x.sfc", None)
            assert lnk == tmp / "Mario & Luigi 'Test'.lnk" and lnk.stat().st_size > 0
        finally:
            SC.desktop_dir = saved
    print("ok  collegamento sul desktop")


def test_battlenet_launch_non_aspetta_il_launcher(tmp_path: Path):
    """Battle.net: il comando di avvio parte subito (niente pre-apertura del launcher + 40/8 s di attesa) e
    viene ripetuto mentre il client si carica, finché il gioco non compare."""
    import time
    cfg = C.Config()
    launcher_exe = tmp_path / "battle.net launcher.exe"
    launcher_exe.write_bytes(b"")
    sent = []

    class FakeProc:
        returncode = 0

        def poll(self):
            return 0

    class FakeSub:
        CREATE_NO_WINDOW = 0

        @staticmethod
        def Popen(cmd, **kw):  # noqa: N802 - stesso nome di subprocess.Popen
            sent.append(cmd)
            return FakeProc()

    saved = (L.subprocess, L.Launcher._game_procs, L._procs_named, L.hide_launcher_windows, L.LAUNCH_RESEND)
    L.subprocess, L._procs_named, L.hide_launcher_windows, L.LAUNCH_RESEND = (
        FakeSub, (lambda name: []), (lambda *a, **k: None), 0.05)
    L.Launcher._game_procs = staticmethod(lambda folder: [])  # il gioco non compare mai: decide stop()
    try:
        ln = L.Launcher(cfg, FakeUser())
        t0 = time.time()
        ln.launch_pc("battlenet:hs_beta", "Hearthstone",
                     {"type": "exe", "target": str(launcher_exe), "args": ["--exec=launch WTCG"]},
                     str(tmp_path), "Battle.net", "battlenet")
        time.sleep(2.5)  # una sola pausa del ciclo di controllo (2 s) piu' un margine
        inviati = list(sent)
        ln.stop()
        deadline = time.time() + 10
        while ln.state.get("status") != "finished" and time.time() < deadline:
            time.sleep(0.05)
    finally:
        L.subprocess, L.Launcher._game_procs, L._procs_named, L.hide_launcher_windows, L.LAUNCH_RESEND = saved
    # il primo processo avviato e' gia' il comando del gioco, non Battle.net «vuoto»
    assert inviati[0] == [str(launcher_exe), "--exec=launch WTCG"], inviati
    assert len(inviati) >= 2, inviati  # comando ripetuto: il client in caricamento lo aveva ignorato
    assert time.time() - t0 < 8 and ln.state.get("status") == "finished", ln.state
    print("ok  Battle.net: comando di avvio subito e ripetuto, nessuna attesa fissa del launcher")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_eden_plan_and_command(Path(t))
    test_window_process_tree()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_scripts_and_close_launcher(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_stop_game(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_battlenet_launch_non_aspetta_il_launcher(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_sharpemu_ps5(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_shortcut(Path(t))
    print("TUTTI I TEST OK")
