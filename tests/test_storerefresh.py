"""Aggiornamento di tutti gli store (PC + console): pianificazione, parallelismo, job. Nessuna rete."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamescraper import config  # noqa: E402
from gamescraper import storerefresh as SR  # noqa: E402

PC1 = "https://repack.example.test/a-z/"
PC2 = "https://repack.example.test/altro/"      # stesso sito di PC1
PC3 = "https://direct.example.test/elenco/"
CONSOLE = "https://switch.example.test/list/"


def make_env(tmp: Path):
    (tmp / "roms").mkdir()
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n',
                                     encoding="utf-8")
    cfg = config.load(tmp / "config.toml")
    cfg.pc_stores = [{"url": PC1, "kind": "repack"}, {"url": PC2, "kind": "repack"},
                     {"url": PC3, "kind": "direct"}]
    cfg.catalogs = {"switch": [CONSOLE]}
    return cfg


def test_impostazioni_predefinite(tmp_path):
    cfg = make_env(tmp_path)
    s = SR.settings(cfg)
    assert s == {"mode": "startup", "time": "03:00", "workers": 4}, s


def test_impostazioni_salvate_e_corrette(tmp_path):
    cfg = make_env(tmp_path)
    config.save_settings(cfg, app={"store_schedule": {"mode": "daily", "time": "7:5", "workers": 99}})
    s = SR.settings(cfg)
    assert s == {"mode": "daily", "time": "07:05", "workers": SR.MAX_WORKERS}, s
    json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))  # file valido
    config.save_settings(cfg, app={"store_schedule": {"mode": "boh", "time": "99:99", "workers": 0}})
    assert SR.settings(cfg) == {"mode": "startup", "time": "03:00", "workers": 4}, SR.settings(cfg)


def test_elenchi_pc_e_console(tmp_path):
    cfg = make_env(tmp_path)
    rows = SR.targets(cfg)
    assert len(rows) == 4, rows
    pc = [r for r in rows if r["group"] == "pc"]
    assert {r["kind"] for r in pc} == {"repack", "direct"}
    assert all(r["platform"] == "windows" for r in pc)
    console = [r for r in rows if r["group"] == "console"]
    assert console[0]["platform"] == "switch" and console[0]["kind"] is None
    assert "Switch" in console[0]["label"], console[0]["label"]
    assert len({r["id"] for r in rows}) == 4


def test_stesso_sito_nello_stesso_gruppo(tmp_path):
    cfg = make_env(tmp_path)
    rows = SR.targets(cfg)
    buckets = SR.groups(rows, 4)
    assert len(buckets) == 3, buckets  # tre siti diversi -> tre worker
    for b in buckets:
        hosts = {SR.host(r["url"]) for r in b}
        assert len(hosts) == 1, b
    assert sorted(r["id"] for b in buckets for r in b) == sorted(r["id"] for r in rows)


def test_parallelismo_limitato(tmp_path):
    cfg = make_env(tmp_path)
    buckets = SR.groups(SR.targets(cfg), 1)
    assert len(buckets) == 1 and len(buckets[0]) == 4


def test_job_in_parallelo(tmp_path, monkeypatch):
    from gamescraper import catalog
    from gamescraper.app import jobs as J
    from gamescraper.library import build as BUILD

    cfg = make_env(tmp_path)
    seen: set[str] = set()
    threads: set[str] = set()

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        if progress:
            progress(1, 1)
        time.sleep(0.15)  # tempo finto: due host devono sovrapporsi
        seen.add(url)
        threads.add(threading.current_thread().name)
        return [{"name": f"Gioco {url}", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)
    monkeypatch.setattr(BUILD, "build", lambda cfg_, log=None: {"online": 4})

    job = J.StoreRefreshJob()
    built = []
    job.run(cfg, built.append)
    st = job.state()
    assert st["status"] == "done", st
    assert st["total"] == 4 and st["done"] == 4, st
    assert st["games"] == 4, st
    assert seen == {PC1, PC2, PC3, CONSOLE}, seen
    assert st["parallel"] == 4, st          # limite impostato (qui il valore predefinito)
    assert len(threads) == 4, threads       # 4 store letti (uno per thread, mai due sullo stesso sito)
    assert built and built[0]["online"] == 4
    assert all(t["status"] == "done" for t in st["targets"]), st["targets"]


def test_job_errore_non_ferma_gli_altri(tmp_path, monkeypatch):
    from gamescraper import catalog
    from gamescraper.app import jobs as J
    from gamescraper.library import build as BUILD

    cfg = make_env(tmp_path)

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        if url == PC3:
            raise RuntimeError("sito giù")
        if progress:
            progress(1, 1)
        return [{"name": "Gioco", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)
    monkeypatch.setattr(BUILD, "build", lambda cfg_, log=None: {})
    job = J.StoreRefreshJob()
    job.run(cfg, lambda stats: None)
    st = job.state()
    assert st["status"] == "done", st
    stati = {t["url"]: t["status"] for t in st["targets"]}
    assert stati[PC3] == "error" and stati[CONSOLE] == "done", stati
    assert "non leggibili" in st["phase"], st["phase"]


def test_job_interrotto(tmp_path, monkeypatch):
    from gamescraper import catalog
    from gamescraper.app import jobs as J
    from gamescraper.library import build as BUILD

    cfg = make_env(tmp_path)
    job = J.StoreRefreshJob()

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        job.stop = True  # stop richiesto durante la lettura
        return [{"name": "Gioco", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)
    monkeypatch.setattr(BUILD, "build", lambda cfg_, log=None: {})
    job.run(cfg, lambda stats: None)
    st = job.state()
    assert st["done"] == 4, st
    assert any(t["status"] == "skip" for t in st["targets"]), st["targets"]


def test_log_e_pagine_per_store(tmp_path, monkeypatch):
    from gamescraper import catalog
    from gamescraper.app import jobs as J
    from gamescraper.library import build as BUILD

    cfg = make_env(tmp_path)
    cfg.pc_stores, cfg.catalogs = [{"url": PC1, "kind": "repack"}], {}

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        for p in range(1, 4):
            log(f"Elenco: pagina {p}/4 — 10 giochi")
            progress(p, 4)
        return [{"name": "Gioco", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)
    monkeypatch.setattr(BUILD, "build", lambda cfg_, log=None: {})
    job = J.StoreRefreshJob()
    job.run(cfg, lambda stats: None)
    t = job.state()["targets"][0]
    assert t["status"] == "done" and t["page"] == 3 and t["pages"] == 4, t
    assert len(t["log"]) == 3 and "pagina 3/4" in t["log"][-1], t["log"]
    assert t["elapsed"] >= 0 and t["rate"] is not None and t["rate"] >= 0, t
    assert job.state()["log"], "il log generale deve riportare la fine dello store"


def test_fetch_segnala_la_pagina(tmp_path, monkeypatch):
    import asyncio

    from gamescraper import catalog, platforms as P
    from gamescraper.cache import Cache

    cfg = make_env(tmp_path)
    cache = Cache(cfg.cache)

    async def fake_page(cfg_, cache_, client, pid, url, log=print):
        return '<pre><a href="Gioco.Uno.zip">Gioco.Uno.zip</a>   1.0G</pre>'

    monkeypatch.setattr(catalog, "_get_page", fake_page)
    ticks = []
    asyncio.run(catalog.fetch(cfg, cache, None, P.PLATFORMS["switch"], "https://x.test/elenco/",
                              log=lambda m: None, progress=lambda p, t, *n: ticks.append((p, t))))
    cache.close()
    assert ticks == [(1, 1)], ticks  # elenco di file: una sola pagina


def test_avvio_di_un_solo_store(tmp_path, monkeypatch):
    from gamescraper import catalog
    from gamescraper.app import jobs as J
    from gamescraper.library import build as BUILD

    cfg = make_env(tmp_path)
    seen: list[str] = []

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        seen.append(url)
        return [{"name": "Gioco", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)
    monkeypatch.setattr(BUILD, "build", lambda cfg_, log=None: {})
    job = J.StoreRefreshJob(only=[PC3])
    job.run(cfg, lambda stats: None)
    st = job.state()
    assert seen == [PC3], seen                      # solo lo store scelto
    assert st["total"] == 1 and st["done"] == 1, st


def test_interruzione_durante_le_pagine(tmp_path, monkeypatch):
    import asyncio

    from gamescraper import catalog, platforms as P
    from gamescraper.cache import Cache

    cfg = make_env(tmp_path)
    cfg.catalog_options["switch"] = {"type": "type_c"}
    calls = {"pages": 0}

    async def fake_page(cfg_, cache_, client, pid, url, log=print):
        calls["pages"] += 1
        num = calls["pages"]
        return (f'<article><h2 class="post-title"><a href="/g{num}/">Gioco {num}</a></h2></article>'
                f'<a class="next page-numbers" href="https://x.test/page/{num + 1}/">»</a>')

    monkeypatch.setattr(catalog, "_get_page", fake_page)
    cache = Cache(cfg.cache)
    stops = {"n": 0}

    def should_stop():
        stops["n"] += 1
        return stops["n"] > 1  # dopo la prima verifica: interrompi

    asyncio.run(catalog.fetch(cfg, cache, None, P.PLATFORMS["switch"], "https://x.test/list/",
                              log=lambda m: None, should_stop=should_stop))
    cache.close()
    assert calls["pages"] == 2, calls  # pagina 1 + una successiva, poi stop (non tutte le 100)


def test_api_avvia_un_solo_store(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from gamescraper import catalog
    from gamescraper.app.server import create_app

    cfg = make_env(tmp_path)

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        return [{"name": "Gioco", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)
    api = TestClient(create_app(cfg))
    d = api.post("/api/store-refresh/run", json={"ids": [CONSOLE]}).json()
    assert d["job"]["total"] == 1 and d["job"]["targets"][0]["url"] == CONSOLE, d["job"]
    assert api.post("/api/store-refresh", json={"mode": "manual"}).json()["schedule"]["mode"] == "manual"
    # id sconosciuto: 404 (e nessun job avviato)
    assert api.post("/api/store-refresh/run", json={"ids": ["boh"]}).status_code in (404, 409)


def _sample(job, stop_evt, seen):
    """Campiona lo stato del job: quante righe in corso e quanti elenchi per sito."""
    while not stop_evt.is_set():
        st = job.state()
        running = [t for t in st["targets"] if t["status"] == "running"]
        seen["max_running"] = max(seen["max_running"], len(running))
        hosts = [t["url"].split("/")[2] for t in running]
        seen["dup_host"] = seen["dup_host"] or (len(hosts) != len(set(hosts)))
        time.sleep(0.01)


def test_coda_rispetta_il_limite(tmp_path, monkeypatch):
    from gamescraper import catalog
    from gamescraper.app import jobs as J
    from gamescraper.library import build as BUILD

    cfg = make_env(tmp_path)
    cfg.pc_stores = [{"url": f"https://s{i}.example.test/list/", "kind": "direct"} for i in range(5)]
    cfg.catalogs = {}
    cfg.app["store_schedule"] = {"mode": "manual", "time": "03:00", "workers": 3}

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        time.sleep(0.25)
        return [{"name": "Gioco", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)
    monkeypatch.setattr(BUILD, "build", lambda cfg_, log=None: {})
    job = J.StoreRefreshJob()
    stop_evt, seen = threading.Event(), {"max_running": 0, "dup_host": False}
    sampler = threading.Thread(target=_sample, args=(job, stop_evt, seen), daemon=True)
    sampler.start()
    job.run(cfg, lambda stats: None)
    stop_evt.set()
    st = job.state()
    assert st["status"] == "done" and st["done"] == 5, st
    assert seen["max_running"] <= 3, seen     # mai pi? di 3 in parallelo (limite impostato)
    assert seen["max_running"] >= 2, seen     # ma davvero in parallelo


def test_stesso_sito_mai_insieme(tmp_path, monkeypatch):
    from gamescraper import catalog
    from gamescraper.app import jobs as J
    from gamescraper.library import build as BUILD

    cfg = make_env(tmp_path)
    cfg.pc_stores = [{"url": "https://uno.test/a/", "kind": "direct"},
                     {"url": "https://uno.test/b/", "kind": "direct"},
                     {"url": "https://due.test/c/", "kind": "direct"}]
    cfg.catalogs = {}
    cfg.app["store_schedule"] = {"workers": 4}

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        time.sleep(0.25)
        return [{"name": "Gioco", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)
    monkeypatch.setattr(BUILD, "build", lambda cfg_, log=None: {})
    job = J.StoreRefreshJob()
    stop_evt, seen = threading.Event(), {"max_running": 0, "dup_host": False}
    sampler = threading.Thread(target=_sample, args=(job, stop_evt, seen), daemon=True)
    sampler.start()
    job.run(cfg, lambda stats: None)
    stop_evt.set()
    assert job.state()["done"] == 3
    assert seen["dup_host"] is False, seen    # due elenchi dello stesso sito mai insieme


def test_aggiunta_alla_coda_mentre_gira(tmp_path, monkeypatch):
    from gamescraper import catalog
    from gamescraper.app import jobs as J
    from gamescraper.library import build as BUILD
    from gamescraper.storerefresh import targets as collect

    cfg = make_env(tmp_path)
    cfg.pc_stores = [{"url": "https://uno.test/a/", "kind": "direct"},
                     {"url": "https://due.test/b/", "kind": "direct"}]
    cfg.catalogs = {}
    cfg.app["store_schedule"] = {"workers": 1}
    started = threading.Event()

    async def fake_fetch(cfg_, cache, client, plat, url, log=None, progress=None, should_stop=None):
        started.set()
        time.sleep(0.3)
        return [{"name": "Gioco", "size": None, "url": url, "magnet": None}]

    monkeypatch.setattr(catalog, "fetch", fake_fetch)
    monkeypatch.setattr(BUILD, "build", lambda cfg_, log=None: {})
    job = J.StoreRefreshJob()
    plan = collect(cfg)
    assert job.add(cfg, plan[:1], lambda stats: None) == 1
    assert started.wait(3)
    # secondo store aggiunto mentre il primo è in corso: va in coda, non viene rifiutato
    added = job.add(cfg, plan[1:], lambda stats: None)
    assert added == 1, added
    st = job.state()
    assert st["queued"] + st["running"] >= 1, st
    job.idle_evt.wait(10)
    st = job.state()
    assert st["status"] == "done" and st["done"] == 2, st


def test_api_store_refresh(tmp_path):
    from fastapi.testclient import TestClient
    from gamescraper.app.server import create_app

    cfg = make_env(tmp_path)
    api = TestClient(create_app(cfg))
    d = api.get("/api/store-refresh").json()
    assert d["schedule"] == {"mode": "startup", "time": "03:00", "workers": 4}, d
    assert d["stores"] == 4 and d["job"]["status"] == "idle", d
    # a riposo l'elenco degli store è comunque disponibile (per aggiornarli uno alla volta)
    assert len(d["list"]) == 4 and d["job"]["targets"] == [], d
    assert d["job"]["stoppable"] is False, d["job"]
    # i file statici hanno la versione nell'indirizzo: il browser non riusa una copia vecchia
    assert "?v=" in api.get("/").text, "app.js/core.js devono avere ?v=<versione>"

    d = api.post("/api/store-refresh", json={"mode": "daily", "time": "04:30", "workers": 3}).json()
    assert d["schedule"] == {"mode": "daily", "time": "04:30", "workers": 3}, d
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["app"]["store_schedule"]
    assert saved["mode"] == "daily" and saved["time"] == "04:30", saved


def test_api_senza_store(tmp_path):
    from fastapi.testclient import TestClient
    from gamescraper.app.server import create_app

    cfg = make_env(tmp_path)
    cfg.pc_stores, cfg.catalogs = [], {}
    api = TestClient(create_app(cfg))
    r = api.post("/api/store-refresh/run")
    assert r.status_code == 400, r.text
