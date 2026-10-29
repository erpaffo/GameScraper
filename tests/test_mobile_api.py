"""Telefono: stato compatto (lavori in corso) e comandi remoti. Nessuna rete, nessun Discord."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from pozzo import config  # noqa: E402
from pozzo.app.server import create_app  # noqa: E402

# --------------------------------------------------------------- client HTTP usa-e-chiudi
_LIVE: list = []


def client(cfg):
    """TestClient registrato: a fine test si chiudono i database (su Windows un file aperto resta bloccato)."""
    from fastapi.testclient import TestClient

    from pozzo.app.server import create_app

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
    from pozzo import catalog

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


def test_cancello_del_telefono(tmp_path):
    """Il cancello dell'app del telefono deve lasciar passare TUTTO cio' che la pagina /m usa: libreria,
    copertine e scheda Attivita' (stato e comandi). Prima /api/mobile/* rispondeva 403 e l'Attivita' restava vuota."""
    import base64
    import sqlite3

    from pozzo.library.build import SCHEMA
    from pozzo.remote.phone import PhoneGate

    cache = tmp_path / "cache"
    (cache / "media" / "gba").mkdir(parents=True)
    png = base64.b64decode(  # PNG 1x1 trasparente: basta che il file esista
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/wF/"
        "AAB0GAf8AAAAASUVORK5CYII=")
    (cache / "media" / "gba" / "aaa.png").write_bytes(png)
    db = sqlite3.connect(cache / "library.db")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO games(key, platform, title, sort_title, year, genres, files, media, found, grp, is_primary, origin) "
               "VALUES('gba/AAA','gba','Pokemon Rubino','pokemon rubino',2003,'[]','[]',?,1,'gba/s:AXV',1,'{}')",
               (json.dumps({"cover": "media/gba/aaa.png"}),))
    db.commit()
    db.close()

    cfg = config.Config(cache=cache, output=tmp_path / "out")
    cfg.config_path = tmp_path / "config.toml"
    app = create_app(cfg)
    hub = app.state.phone_hub
    gate = TestClient(PhoneGate(app, hub), base_url="https://testserver")
    try:
        code = hub.new_code()
        token = gate.post("/m/api/pair", json={"code": code, "name": "Pixel"}).cookies.get("gs_phone")
        ck = {"gs_phone": token}

        lib = gate.get("/api/library?part=owned", cookies=ck)
        assert lib.status_code == 200, lib.text
        cols = lib.json()["games"]["cols"]
        row = lib.json()["games"]["rows"][0]
        gid = row[cols.index("id")]
        assert row[cols.index("cover")] == 1, row     # la pagina sa che c'e' la copertina da mostrare

        img = gate.get(f"/img/{gid}/cover?w=260", cookies=ck)
        assert img.status_code == 200 and img.headers["content-type"].startswith("image/"), \
            f"copertine non raggiungibili dal telefono: {img.status_code}"

        st = gate.get("/api/mobile/state", cookies=ck)
        assert st.status_code == 200, f"Attivita' non raggiungibile dal telefono: {st.status_code} {st.text}"
        assert set(st.json()) == {"running", "jobs", "counts"}

        act = gate.post("/api/mobile/action", json={"action": "stop-game"}, cookies=ck)
        assert act.status_code == 200 and "state" in act.json(), act.text

        # il cancello continua a fermare tutto il resto, e senza abbinamento non si passa
        assert gate.get("/api/settings", cookies=ck).status_code == 403
        gate.cookies.clear()  # come un browser che non ha il cookie di abbinamento
        assert gate.get("/api/mobile/state").status_code == 401
    finally:
        for handler in getattr(app.router, "on_shutdown", []):
            try:
                handler()
            except Exception:
                pass
        gate.close()
    print("ok  cancello del telefono: libreria, copertine e Attivita' passano, il resto no")


def test_attivita(tmp_path):
    api = client(make_cfg(tmp_path))
    assert api.get("/api/activity").json()["jobs"] == []
    assert api.post("/api/activity/build/stop").status_code == 400   # non interrompibile
    r = api.post("/api/activity/hltb/stop")                             # interrompibile anche se fermo
    assert r.status_code == 200 and "jobs" in r.json(), r.text
    assert "Attività" in api.get("/activity").text
    print("ok  /api/activity: elenco, stop e pagina")


def test_copertine_console_in_parallelo(tmp_path):
    """Copertine degli store online: PS4 e PS5 girano insieme e si fermano ognuna per conto suo."""
    import time as _time

    from pozzo.app import jobs as J

    def fake_run(self, cfg, after_build, on_found=None):  # niente rete: resta "in corso" finche' non lo si ferma
        while not self.stop:
            _time.sleep(0.02)
        self.status = "done"

    old, J.NetMetaJob.run = J.NetMetaJob.run, fake_run
    try:
        api = client(make_cfg(tmp_path))
        assert api.get("/api/catalogs/ps5").json()["meta"]["status"] == "idle"
        assert api.post("/api/catalogs/ps4/netmeta", json={}).json()["status"] == "running"
        assert api.post("/api/catalogs/ps5/netmeta", json={}).json()["status"] == "running"
        ids = {j["id"] for j in api.get("/api/activity").json()["jobs"] if j["status"] == "running"}
        assert {"netmeta:ps4", "netmeta:ps5"} <= ids, ids
        api.post("/api/catalogs/ps4/netmeta/stop", json={})
        _time.sleep(0.2)
        assert api.get("/api/catalogs/ps4").json()["meta"]["status"] == "done"
        assert api.get("/api/catalogs/ps5").json()["meta"]["status"] == "running"
        api.post("/api/activity/netmeta:ps5/stop")  # anche dal pannello Attivita'
        _time.sleep(0.2)
        assert api.get("/api/catalogs/ps5").json()["meta"]["status"] == "done"
    finally:
        J.NetMetaJob.run = old
    print("ok  copertine degli store: una console per lavoro, in parallelo")


def test_copertine_progressive(tmp_path):
    """I dati trovati dal lavoro arrivano subito alla libreria (memoria e library.db), prima della fine del lavoro."""
    import asyncio
    import sqlite3
    import threading
    import time as _time

    from pozzo import gametdb, storemeta
    from pozzo.cache import Cache
    from pozzo.library.build import build

    url, page = "https://example-b.test/uno/", "https://example-b.test/all"
    cfg = make_cfg(tmp_path)
    cfg.catalogs = {"switch": [page]}
    c = Cache(cfg.cache)
    c.kv_set(f"catalog:type_b:{page}", {"url": page, "index_url": page, "pid": "switch", "time": _time.time(),
                                         "entries": [{"name": "Gioco Uno Switch NSP", "size": None, "url": url,
                                                      "magnet": None, "group": "G"}]})
    c.commit()
    c.close()
    build(cfg, log=lambda m: None)
    gate = threading.Event()

    async def fake_fill(cfg, cache, tdb=None, on_found=None, **kw):
        on_found({url: {"cover": "https://art.gametdb.com/x.jpg", "description": "Descrizione trovata adesso.",
                        "genres": ["Action"]}})
        await asyncio.to_thread(gate.wait, 10)  # il lavoro resta in corso mentre si controlla
        return {}

    real_fill, real_ensure = storemeta.fill, gametdb.GameTDB.ensure
    storemeta.fill, gametdb.GameTDB.ensure = fake_fill, lambda self, force=False: False
    try:
        api = client(cfg)
        lib = api.get("/api/library").json()
        row = next(r for r in lib["games"]["rows"] if r[12] == "online")
        assert row[5] == 0, row  # ancora senza copertina
        api.post("/api/catalogs/switch/netmeta", json={})
        for _ in range(100):
            p = api.get(f"/api/library/patches?since={lib['meta_rev']}").json()
            if p.get("games"):
                break
            _time.sleep(0.05)
        assert p["games"] == [[row[0], 1, None, "", "Action"]], p
        assert api.get("/api/status").json()["meta_rev"] == p["rev"] > lib["meta_rev"]
        assert api.get(f"/api/library/patches?since={p['rev']}").json()["games"] == []
        db = sqlite3.connect(Path(cfg.cache) / "library.db")
        desc, media = db.execute("SELECT description, media FROM games WHERE id=?", (row[0],)).fetchone()
        db.close()
        assert desc == "Descrizione trovata adesso." and "art.gametdb.com" in media, (desc, media)
        assert api.get("/api/activity").json()["jobs"][0]["status"] == "running"  # non e' ancora finito

        # griglia: la copertina remota risponde subito 202, si scarica in sottofondo e il riquadro viene segnalato
        import httpx
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
        slow = threading.Event()

        def fake_get(u, **kw):
            slow.wait(5)  # host lento: prima la richiesta della griglia sarebbe rimasta appesa qui
            return httpx.Response(200, content=png)

        real_get, httpx.get = httpx.get, fake_get
        try:
            t0 = _time.time()
            assert api.get(f"/img/{row[0]}/cover?q=1").status_code == 202
            assert _time.time() - t0 < 1
            before = api.get("/api/status").json()["meta_rev"]
            slow.set()
            for _ in range(100):
                if api.get("/api/status").json()["meta_rev"] > before:
                    break
                _time.sleep(0.05)
            assert [g[0] for g in api.get(f"/api/library/patches?since={before}").json()["games"]] == [row[0]]
            assert api.get(f"/img/{row[0]}/cover?q=1").status_code == 200
        finally:
            httpx.get = real_get
    finally:
        gate.set()
        wait_idle(api)
        storemeta.fill, gametdb.GameTDB.ensure = real_fill, real_ensure
    print("ok  copertine e descrizioni: compaiono mentre il lavoro e' ancora in corso")


if __name__ == "__main__":
    for fn in (test_stato_compaito, test_comando_sconosciuto, test_aggiorna_store_senza_store,
               test_aggiorna_store_accoda, test_chiudi_gioco_senza_gioco, test_cancello_del_telefono, test_attivita):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print("TUTTI I TEST OK")
