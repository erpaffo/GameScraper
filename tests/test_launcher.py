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


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_eden_plan_and_command(Path(t))
    print("TUTTI I TEST OK")

