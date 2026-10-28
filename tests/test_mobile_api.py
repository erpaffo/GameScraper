"""Telefono: stato compatto (lavori in corso) e comandi remoti. Nessuna rete, nessun Discord."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from gamescraper import config  # noqa: E402
from gamescraper.app.server import create_app  # noqa: E402

# --------------------------------------------------------------- client HTTP usa-e-chiudi
_LIVE: list = []


def client(cfg):
    """TestClient registrato: a fine test si chiudono i database (su Windows un file aperto resta bloccato)."""
    from fastapi.testclient import TestClient

    from gamescraper.app.server import create_app

    api = TestClient(create_app(cfg))
    _LIVE.append(api)
    return api


def teardown_function(_=None):
    """Eseguito da pytest dopo ogni test: spegnimento dell'app e chiusura delle connessioni."""
    while _LIVE:
        api = _LIVE.pop()
        for handler in getattr(api.app.router, "on_shutdown", []):
            try:
                handler()
            except Exception:
                pass
        try:
            api.close()
        except Exception:
            pass


def make_cfg(tmp: Path):
    (tmp / "roms").mkdir(exist_ok=True)
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                     encoding="utf-8")
    return config.load(tmp / "config.toml")


def wait_idle(api, timeout: float = 15.0):
    """Attende la fine dei lavori avviati in background: un test non deve sporcare il successivo."""
    import time as _time

    end = _time.time() + timeout
    _time.sleep(0.2)  # il thread del lavoro parte subito dopo la risposta: si d? il tempo di risultare attivo
    while _time.time() < end:
        jobs = api.get("/api/mobile/state").json().get("jobs") or []
        if not any(j.get("status") == "running" for j in jobs):
            return
        _time.sleep(0.05)


def test_stato_compaito(tmp_path):
    api = client(make_cfg(tmp_path))
    d = api.get("/api/mobile/state").json()
    assert set(d) == {"running", "jobs", "counts"}, d
    assert d["jobs"] == [], d          # niente in corso al primo avvio
    assert d["counts"]["games"] == 0 and d["counts"]["online"] == 0
    print("ok  /api/mobile/state: gioco, lavori e conteggi")


def test_comando_sconosciuto(tmp_path):
    api = client(make_cfg(tmp_path))
    r = api.post("/api/mobile/action", json={"action": "formattare-il-disco"})
    assert r.status_code == 400, r.text
    print("ok  comando sconosciuto rifiutato (400)")


def test_aggiorna_store_senza_store(tmp_path):
    api = client(make_cfg(tmp_path))
    r = api.post("/api/mobile/action", json={"action": "stores"})
    assert r.status_code == 400, r.text   # nessuno store configurato: errore chiaro, non un crash
    print("ok  «Aggiorna store» senza store: 400 con messaggio")


def test_aggiorna_store_accoda(tmp_path, monkeypatch):
    from gamescraper import catalog

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        return [{"name": "Gioco", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)  # nessuna rete e fine immediata, niente job lasciati in giro
    cfg = make_cfg(tmp_path)
    config.set_setting(cfg, "pc_stores", [{"url": "https://esempio.test/elenco/", "kind": "direct"}])
    api = client(cfg)
    r = api.post("/api/mobile/action", json={"action": "stores"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["queued"] >= 1, d
    # il lavoro compare subito nello stato, con nome e stato
    st = d["state"]
    cats = [j for j in st["jobs"] if j["id"] == "catalogs"]
    assert cats and cats[0]["status"] in ("running", "done"), st["jobs"]
    print("ok  «Aggiorna store» dal telefono: accodato e visibile tra i lavori")


def test_chiudi_gioco_senza_gioco(tmp_path):
    api = client(make_cfg(tmp_path))
    r = api.post("/api/mobile/action", json={"action": "stop-game"})
    assert r.status_code == 200 and r.json()["ok"] is False  # niente da chiudere, nessun errore
    print("ok  «Chiudi gioco» senza gioco in corso: nessun errore")


if __name__ == "__main__":
    for fn in (test_stato_compaito, test_comando_sconosciuto, test_aggiorna_store_senza_store,
               test_aggiorna_store_accoda, test_chiudi_gioco_senza_gioco):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print("TUTTI I TEST OK")
