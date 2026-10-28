"""Presenza su Discord: frame del protocollo, contenuto dell'activity e servizio collegato agli eventi.

Nessuna rete e nessun Discord reale: si inietta un canale finto che registra i byte scritti.
"""
import json
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pozzo import config as C  # noqa: E402
from pozzo import discordrpc as D  # noqa: E402


class FakeChannel:
    """Sostituto del canale IPC: risponde al handshake con READY e registra i frame inviati.

    Come un named pipe vero, la lettura resta in attesa quando non c'? nulla da leggere
    (cos? il thread lettore non vede una connessione chiusa a ogni giro).
    """

    def __init__(self, ready=True):
        self.sent = []
        self.closed = False
        self._ready = ready
        self._in = b""
        self._cond = threading.Condition()

    def write(self, data: bytes):
        if self.closed:
            raise OSError("chiuso")
        op, size = struct.unpack("<ii", data[:8])
        payload = json.loads(data[8:8 + size].decode("utf-8"))
        with self._cond:
            self.sent.append((op, payload))
            if op == D.OP_HANDSHAKE and self._ready:
                body = json.dumps({"cmd": "DISPATCH", "evt": "READY", "data": {}}).encode()
                self._in += struct.pack("<ii", D.OP_FRAME, len(body)) + body
            self._cond.notify_all()

    def read(self, n: int) -> bytes:
        with self._cond:
            while len(self._in) < n:
                if self.closed:
                    out, self._in = self._in, b""
                    return out
                self._cond.wait(0.2)
            out, self._in = self._in[:n], self._in[n:]
            return out

    def close(self):
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    # comodit? per i test
    def activities(self) -> list[dict]:
        return [p["args"]["activity"] for op, p in self.sent if op == D.OP_FRAME and p.get("cmd") == "SET_ACTIVITY"]


def make_service(tmp_path: Path, conf: dict, channel=None):
    cfg = C.Config(cache=tmp_path / "cache")
    cfg.config_path = tmp_path / "config.toml"
    cfg.app["discord"] = dict(conf)
    saved = {}
    ch = channel if channel is not None else FakeChannel()

    def save(values):
        saved.update(values)
        cfg.app.update(values)

    svc = D.PresenceService(cfg, save=save, connect=lambda path: ch)
    return cfg, svc, saved, ch


def test_frame_e_lettura():
    data = D.frame(D.OP_HANDSHAKE, {"v": 1, "client_id": "abc"})
    op, size = struct.unpack("<ii", data[:8])
    assert op == D.OP_HANDSHAKE and size == len(data) - 8
    body = data[8:]
    it = iter([data[:8], body])
    op2, payload = D.read_frame(lambda n: next(it)[:n])
    assert op2 == D.OP_HANDSHAKE and payload == {"v": 1, "client_id": "abc"}
    print("ok  frame: opcode, lunghezza e JSON si leggono correttamente")


def test_percorsi_canali():
    paths = D.pipe_paths(3)
    assert len(paths) == 3
    if sys.platform == "win32":
        assert paths[0].lower().startswith("\\\\?\\pipe\\discord-ipc-0")
    else:
        assert paths[0].endswith("discord-ipc-0")
    print("ok  percorsi dei canali IPC:", paths[0])


def test_activity_pulita():
    a = D.activity("Zelda", "Switch · su PozzoLauncher", started=1700000000.7, large_text="Zelda")
    assert a["details"] == "Zelda" and a["state"] == "Switch · su PozzoLauncher"
    assert a["timestamps"] == {"start": 1700000000}
    assert a["assets"]["large_text"] == "Zelda" and a["instance"] is False
    # niente campi vuoti: Discord li rifiuta
    b = D.activity("Gioco")
    assert "state" not in b and "assets" not in b and "timestamps" not in b, b
    # testi lunghi tagliati a 128 caratteri
    assert len(D.activity("x" * 500)["details"]) == 128
    print("ok  activity: campi vuoti omessi, testi tagliati a 128 caratteri")


def test_handshake_e_presenza(tmp_path):
    cfg, svc, saved, ch = make_service(tmp_path, {"enabled": True, "client_id": "123456789"})
    assert svc.game_started  # metodo presente
    svc.game_started({"key": "switch/Zelda", "title": "Zelda", "started": 1700000000})
    handshakes = [p for op, p in ch.sent if op == D.OP_HANDSHAKE]
    assert handshakes and handshakes[0]["client_id"] == "123456789", ch.sent
    acts = ch.activities()
    assert acts and acts[-1]["details"] == "Zelda", acts
    assert acts[-1]["state"].startswith("Nintendo Switch"), acts[-1]
    assert acts[-1]["timestamps"]["start"] == 1700000000
    st = svc.state()
    assert st["enabled"] and st["connected"] and st["playing"] == "Zelda", st
    print("ok  gioco avviato: handshake con l'ID e activity con titolo, console e tempo")


def test_tempo_disattivabile(tmp_path):
    cfg, svc, saved, ch = make_service(tmp_path, {"enabled": True, "client_id": "1", "show_time": False})
    svc.game_started({"key": "snes/Mario", "title": "Mario", "started": 1700000000})
    assert "timestamps" not in ch.activities()[-1], ch.activities()[-1]
    print("ok  «mostra il tempo» spento: nessun timestamp nell'activity")


def test_fine_gioco_togli_presenza(tmp_path):
    cfg, svc, saved, ch = make_service(tmp_path, {"enabled": True, "client_id": "1"})
    svc.game_started({"key": "snes/Mario", "title": "Mario"})
    svc.game_stopped({"key": "snes/Mario", "title": "Mario"})
    assert ch.activities()[-1] is None, ch.activities()  # activity None = presenza rimossa
    assert svc.state()["playing"] is None
    print("ok  fine partita: la presenza viene tolta")


def test_spento_non_contatta_discord(tmp_path):
    cfg, svc, saved, ch = make_service(tmp_path, {"enabled": False, "client_id": "1"})
    svc.game_started({"key": "snes/Mario", "title": "Mario"})
    assert ch.sent == [], ch.sent
    assert svc.state()["enabled"] is False
    print("ok  integrazione spenta: nessun frame inviato")


def test_salvataggio_impostazioni(tmp_path):
    cfg, svc, saved, ch = make_service(tmp_path, {"enabled": False, "client_id": ""})
    st = svc.apply({"enabled": True, "client_id": " 999 ", "show_time": False})
    assert saved["discord"] == {"enabled": True, "client_id": "999", "show_time": False}, saved
    assert st["enabled"] and st["client_id"] == "999" and st["show_time"] is False, st
    st2 = svc.apply({"enabled": False})
    assert st2["enabled"] is False
    print("ok  impostazioni: ID ripulito dagli spazi, valori salvati e applicati")


def test_senza_id_errore_chiaro(tmp_path):
    cfg, svc, saved, ch = make_service(tmp_path, {"enabled": True, "client_id": ""})
    svc.game_started({"key": "snes/Mario", "title": "Mario"})
    st = svc.state()
    assert st["connected"] is False and "ID" in (st["error"] or ""), st
    print("ok  senza ID applicazione: errore comprensibile, nessun crash")


def test_discord_non_aperto(tmp_path):
    def no_pipe(path):
        raise FileNotFoundError(path)
    cfg = C.Config(cache=tmp_path / "cache")
    cfg.config_path = tmp_path / "config.toml"
    cfg.app["discord"] = {"enabled": True, "client_id": "1"}
    svc = D.PresenceService(cfg, save=lambda v: None, connect=no_pipe)
    svc.game_started({"key": "snes/Mario", "title": "Mario"})
    st = svc.state()
    assert st["connected"] is False and st["error"], st
    print("ok  Discord chiuso: nessuna eccezione, solo lo stato «non collegato»")


if __name__ == "__main__":
    import tempfile
    test_frame_e_lettura()
    test_percorsi_canali()
    test_activity_pulita()
    for fn in (test_handshake_e_presenza, test_tempo_disattivabile, test_fine_gioco_togli_presenza,
               test_spento_non_contatta_discord, test_salvataggio_impostazioni, test_senza_id_errore_chiaro,
               test_discord_non_aperto):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print("TUTTI I TEST OK")
