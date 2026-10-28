"""Log su file (applog): attivazione con flag/percorso e scrittura delle righe."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pozzo import applog  # noqa: E402


def _reset():
    applog._path = None


def test_spento_di_default(tmp_path):
    _reset()
    assert applog.start(None, tmp_path) is None
    applog.write("store", "non deve comparire")
    assert not (tmp_path / "logs").exists()
    assert applog.enabled() is False


def test_flag_1_usa_la_cache(tmp_path):
    _reset()
    p = applog.start("1", tmp_path)
    assert p == tmp_path / "logs" / "diagnostica.log", p
    applog.write("piano", "modalita'=startup")
    applog.write("store", "fitgirl: pagina 3/147")
    righe = p.read_text(encoding="utf-8").splitlines()
    assert righe[0].startswith("[") and "avvio" in righe[0], righe
    assert any("piano" in r and "modalita'=startup" in r for r in righe), righe
    assert any("store" in r and "pagina 3/147" in r for r in righe), righe
    _reset()


def test_percorso_esplicito(tmp_path):
    _reset()
    p = applog.start(tmp_path / "mio.log", tmp_path)
    assert p == tmp_path / "mio.log" and p.exists()
    applog.write("store", "ciao")
    assert "ciao" in p.read_text(encoding="utf-8")
    _reset()


def test_rotazione_oltre_il_limite(tmp_path, monkeypatch):
    _reset()
    p = applog.start("1", tmp_path)
    monkeypatch.setattr(applog, "MAX_BYTES", 10)
    p.write_text("x" * 50, encoding="utf-8")
    p2 = applog.start("1", tmp_path)
    assert p2 == p and p.exists() and p.with_suffix(".log.old").exists()
    _reset()


def test_flag_spento_esplicito(tmp_path):
    _reset()
    for off in ("0", "false", "off"):
        assert applog.start(off, tmp_path) is None, off
