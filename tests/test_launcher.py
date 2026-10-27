"""Test di configurazione e costruzione comando di avvio per Eden (Switch). Nessun processo avviato.

Esegui: python tests/test_launcher.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamescraper import config as C  # noqa: E402
from gamescraper import platforms as P  # noqa: E402
from gamescraper.library import launcher as L  # noqa: E402


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


def test_scripts_and_close_launcher(tmp: Path):
    import time
    log = tmp / "log.txt"
    hook = tmp / "hook.py"
    hook.write_text("import os, sys\nopen(sys.argv[1], 'a').write(sys.argv[2] + ':' + os.environ['GAMESCRAPER_TITLE'] + '\\n')\n")
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


def test_shortcut(tmp: Path):
    from gamescraper.library import shortcut as SC
    assert SC.file_name('Zelda: Ocarina of Time? "3D"') == "Zelda Ocarina of Time 3D"
    assert SC.split_cmd('"C:\\a b\\cli.exe" -c "x y"') == ("C:\\a b\\cli.exe", '-c "x y"')
    if sys.platform == "win32":
        saved = SC.desktop_dir
        SC.desktop_dir = lambda: tmp
        try:
            lnk = SC.create("Mario & Luigi: 'Test'", f'"{sys.executable}" -m gamescraper', str(tmp), "snes/x.sfc", None)
            assert lnk == tmp / "Mario & Luigi 'Test'.lnk" and lnk.stat().st_size > 0
        finally:
            SC.desktop_dir = saved
    print("ok  collegamento sul desktop")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_eden_plan_and_command(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_scripts_and_close_launcher(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_shortcut(Path(t))
    print("TUTTI I TEST OK")

