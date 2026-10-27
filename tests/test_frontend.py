"""Test delle API del frontend su una libreria finta (nessun file reale toccato).

Copre: caricamento libreria, versioni regionali raggruppate, preferiti a livello di gioco,
collezioni personali, modifiche alle collezioni automatiche che sopravvivono a una
ricostruzione, versione principale scelta dall'utente, avvio con un finto emulatore e
registrazione del tempo di gioco.

Esegui: python tests/test_frontend.py
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from gamescraper import config  # noqa: E402
from gamescraper.app.server import create_app  # noqa: E402
from gamescraper.library.build import SCHEMA  # noqa: E402


def make_library(cache: Path, rom: Path):
    db = sqlite3.connect(cache / "library.db")
    db.executescript(SCHEMA)
    games = [  # (key, platform, title, grp, primary)
        ("gba/AAA", "gba", "Pokemon - Versione Rubino", "gba/s:AXV", 1),
        ("gba/BBB", "gba", "Pocket Monsters - Ruby", "gba/s:AXV", 0),
        ("gba/CCC", "gba", "Pokemon - Versione Smeraldo", "gba/s:BPE", 1),
        ("psp/DDD", "psp", "Kingdom Hearts - Birth by Sleep", "psp/i:khbbs", 1),
    ]
    for key, plat, title, grp, prim in games:
        db.execute("INSERT INTO games(key, platform, title, sort_title, year, genres, files, media, found, grp, "
                   "is_primary, origin) VALUES(?,?,?,?,2003,'[]',?, '{}', 1, ?, ?, '{}')",
                   (key, plat, title, title.lower(), json.dumps([str(rom)]), grp, prim))
    db.execute("INSERT INTO collections VALUES('pokemon', 'Pokémon')")
    db.executemany("INSERT INTO collection_games VALUES('pokemon', ?)", [("gba/AAA",), ("gba/CCC",)])
    db.execute("INSERT INTO meta VALUES('built_at', '1')")
    db.commit()
    db.close()


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        rom = tmp / "game.gba"
        rom.write_bytes(b"x" * 100)
        make_library(tmp, rom)
        cfg = config.Config(cache=tmp, output=tmp / "out")
        cfg.config_path = tmp / "config.toml"  # settings.json nella cartella temporanea, non in quella corrente
        # finto emulatore: python che dorme 1,2 s
        cfg.emulators = {"melonds": sys.executable}
        cfg.launch = {"gba": {"emulator": "melonds", "args": ["-c", "import time; time.sleep(1.2)", "{rom}"]}}
        app = create_app(cfg)
        with TestClient(app) as c:
            lib = c.get("/api/library").json()
            rows = {r[1]: r for r in lib["games"]["rows"]}
            groups = lib["groups"]["rows"]
            ruby, ruby_jp, emerald = rows["Pokemon - Versione Rubino"], rows["Pocket Monsters - Ruby"], rows["Pokemon - Versione Smeraldo"]
            assert ruby[6] == ruby_jp[6], "le versioni regionali devono stare nello stesso gruppo"
            assert groups[ruby[6]][0] == ruby[0] and groups[ruby[6]][7] == 2
            print("ok  versioni regionali raggruppate, principale = italiana")

            # preferito: vale per tutto il gioco (tutte le versioni)
            c.post(f"/api/game/{ruby_jp[0]}/flag", json={"field": "favorite", "value": True})
            lib = c.get("/api/library").json()
            assert lib["groups"]["rows"][ruby[6]][1] == 1
            print("ok  preferito a livello di gioco")

            # collezione automatica: togliere Smeraldo, poi ricaricare la libreria (simula ricostruzione)
            c.post("/api/collections/pokemon/members", json={"game": emerald[0], "action": "remove"})
            # collezione personale con un gioco PSP
            kh = rows["Kingdom Hearts - Birth by Sleep"]
            slug = c.post("/api/collections", json={"name": "Da finire", "game": kh[0]}).json()["slug"]
            app_after = create_app(cfg)  # nuova istanza = come dopo un riavvio/ricostruzione
            with TestClient(app_after) as c2:
                lib = c2.get("/api/library").json()
                colls = {x["slug"]: x for x in lib["collections"]}
                assert colls["pokemon"]["count"] == 1, colls["pokemon"]
                assert colls[slug]["count"] == 1 and colls[slug]["custom"]
                d = c2.get(f"/api/game/{kh[0]}").json()
                assert [x["slug"] for x in d["collections"]] == [slug]
                print("ok  modifiche alle collezioni persistenti (automatiche e personali)")

                # versione principale scelta dall'utente
                c2.post(f"/api/game/{ruby_jp[0]}/preferred")
                lib = c2.get("/api/library").json()
                assert lib["groups"]["rows"][ruby[6]][0] == ruby_jp[0]
                print("ok  versione principale scelta dall'utente")

                # avvio + tempo di gioco
                r = c2.post(f"/api/game/{ruby[0]}/play")
                assert r.status_code == 200, r.text
                for _ in range(50):
                    st = c2.get("/api/running").json()
                    if st.get("status") in ("finished", "error"):
                        break
                    time.sleep(0.1)
                assert st["status"] == "finished", st
                d = c2.get(f"/api/game/{ruby[0]}").json()
                assert d["user"]["play_count"] == 1 and d["user"]["playtime"] >= 1.0, d["user"]
                print(f"ok  avvio con emulatore, tempo registrato {d['user']['playtime']:.1f} s")
                assert d["user"]["status"] == "playing" and c2.get("/api/status").json()["user_rev"] > 0

                # tasto destro > "Rimuovi da Giocati di recente": azzera l'ultima partita, il tempo resta
                assert c2.get("/api/library").json()["groups"]["rows"][ruby[6]][3] > 0
                assert c2.post(f"/api/game/{ruby[0]}/flag", json={"field": "last_played", "value": 123}).status_code == 200
                row = c2.get("/api/library").json()["groups"]["rows"][ruby[6]]
                assert row[3] == 0 and row[4] >= 1.0, row
                assert c2.get("/api/status").json()["built_at"] == 1.0  # il client lo confronta per accorgersi di una nuova libreria
                print("ok  rimozione da Giocati di recente e built_at nello stato")

                # immagini remote: un host che non risponde viene saltato per un po' (non tiene appese le richieste)
                import httpx
                calls = []

                def dead(*a, **k):
                    calls.append(a[0])
                    raise httpx.ConnectTimeout("x")
                real_get, httpx.get = httpx.get, dead
                try:
                    u = "https://cdn.allkeyshop.com/images/a.webp"
                    assert c2.get("/remote-img", params={"u": u}).status_code == 404
                    t0 = time.time()
                    assert c2.get("/remote-img", params={"u": u.replace("a.webp", "b.webp")}).status_code == 404
                    assert len(calls) == 1 and time.time() - t0 < 1, calls
                finally:
                    httpx.get = real_get
                print("ok  host remoto irraggiungibile saltato senza attese")

                # nota e voto personali: a livello di gioco, voto fuori scala rifiutato
                assert c2.post(f"/api/game/{ruby_jp[0]}/flag", json={"field": "note", "value": "  finito al 100%  "}).status_code == 200
                assert c2.post(f"/api/game/{ruby[0]}/flag", json={"field": "my_rating", "value": 4}).status_code == 200
                assert c2.post(f"/api/game/{ruby[0]}/flag", json={"field": "my_rating", "value": 9}).status_code == 400
                u = c2.get(f"/api/game/{ruby[0]}").json()["user"]
                assert u["note"] == "finito al 100%" and u["my_rating"] == 4, u
                print("ok  nota e voto personali")

                # giochi gratis: nuovi / visti / riscattati (senza rete: elenco gia' in cache)
                from gamescraper.cache import Cache
                import time as _t
                cc = Cache(cfg.cache)
                cc.kv_set("freegames", {"time": _t.time(), "errors": [], "items": [
                    {"title": "Hue", "url": "u1", "kind": "Free with Prime", "console": "pc", "drm": "amazon", "source": "allkeyshop"},
                    {"title": "Celeste", "url": "u2", "kind": "Free to keep", "console": "pc", "drm": "epic", "source": "gamerpower"}]})
                cc.close()
                fr = c2.get("/api/free").json()
                assert fr["first"] and fr["new"] == 2, fr  # prima apertura: il client non li mostra come nuovi
                c2.post("/api/free/mark", json={"seen": True})
                fr = c2.get("/api/free").json()
                assert not fr["first"] and fr["new"] == 0
                hue = next(i for i in fr["items"] if i["title"] == "Hue")
                c2.post("/api/free/mark", json={"claim": hue["id"], "value": True})
                fr = c2.get("/api/free").json()
                assert [i["claimed"] for i in fr["items"]] == [True, False], fr["items"]
                print("ok  giochi gratis: nuovi, visti e riscattati")

                # RetroAchievements: non configurato -> niente sezione; poi catalogo + progresso (risposte finte, in cache)
                assert c2.get(f"/api/game/{emerald[0]}/ra").json() == {"configured": False}
                import httpx
                from gamescraper import retroachievements as RA
                calls = []

                def ra_handler(req):
                    calls.append(req.url.path.rsplit("/", 1)[-1])
                    name = req.url.path.rsplit("/", 1)[-1]
                    if name == "API_GetUserCompletionProgress.php":
                        return httpx.Response(200, json={"Count": 0, "Total": 0, "Results": []})
                    if name == "API_GetGameList.php":
                        # titolo inglese: si riconosce solo dall'hash (la ROM in libreria e' italiana)
                        return httpx.Response(200, json=[{"ID": 515, "Title": "Pokemon Emerald Version", "ConsoleID": 5,
                                                          "NumAchievements": 2, "Hashes": ["c3e85be7372bbd5e43f6334a565968c1"]}])
                    return httpx.Response(200, json={"ID": 515, "Title": "Pokemon Emerald Version", "ConsoleID": 5,
                                                     "NumAchievements": 2, "Achievements": {
                        "1": {"ID": 1, "Title": "Prima medaglia", "Description": "Batti Roxanne", "Points": 5, "BadgeName": "1",
                              "DisplayOrder": 1, "DateEarned": "2026-01-01 10:00:00"},
                        "2": {"ID": 2, "Title": "Campione", "Description": "Lega Pokemon", "Points": 25, "BadgeName": "2",
                              "DisplayOrder": 2}}})
                real_client = RA.client
                RA.client = lambda u, k, **kw: real_client(u, k, transport=httpx.MockTransport(ra_handler))
                cc = Cache(cfg.cache)  # MD5 dal DAT No-Intro, come per gli .zip veri
                cc.put_file(str(rom), 100, rom.stat().st_mtime, {"dat_md5": "C3E85BE7372BBD5E43F6334A565968C1"})
                cc.close()
                try:
                    assert c2.post("/api/ra", json={"username": "paffo", "key": "k"}).status_code == 200
                    cfg.app["retroachievements"] = {"username": "paffo", "key": "k"}
                    r = c2.get(f"/api/game/{emerald[0]}/ra").json()
                    assert r["found"] and [a["unlocked"] for a in r["game"]["achievements"]] == [True, False], r
                    n = len(calls)
                    c2.get(f"/api/game/{emerald[0]}/ra")  # abbinamento e progresso dalla cache: nessuna richiesta
                    assert len(calls) == n, calls
                    assert c2.get(f"/api/game/{kh[0]}/ra").json()["found"] is False  # PSP: catalogo vuoto per il titolo
                finally:
                    RA.client = real_client
                print("ok  RetroAchievements: abbinamento per hash del DAT, obiettivi, cache")

                # telefono come controller: le vibrazioni del gioco (thread del driver) arrivano al telefono, i comandi continuano
                def until(ws, t):  # salta i messaggi di contorno (errori di ViGEm sul PC di prova, sala giocatori)
                    for _ in range(10):
                        m = ws.receive_json()
                        if m.get("t") == t:
                            return m
                    raise AssertionError(t)
                hub = app_after.state.phone_hub
                with c2.websocket_connect("/m/ws") as ws:
                    slot = until(ws, "ok")["slot"]
                    pl = until(ws, "players")
                    assert pl["slot"] == slot and [p["slot"] for p in pl["list"]] == [slot], pl
                    hub.slots[slot]["rumble"](120, 30)
                    m = until(ws, "rumble")
                    for _ in range(5):  # con ViGEm vero il driver manda vibrazioni nulle quando il controller si collega
                        if m["l"] or m["s"]:
                            break
                        m = until(ws, "rumble")
                    assert m == {"t": "rumble", "l": 120, "s": 30}, m
                    ws.send_json({"t": "ping", "ts": 7})
                    assert until(ws, "pong") == {"t": "pong", "ts": 7}
                    # mouse dal telefono: spento finche' il PC non lo permette
                    ws.send_json({"t": "mouse", "x": 0.5, "y": 0.5, "l": 1})
                    assert "non permesso" in until(ws, "err")["msg"]
                    # sala giocatori: il telefono passa al posto 3 senza ricollegarsi
                    ws.send_json({"t": "swap", "slot": 2})
                    pl = until(ws, "players")
                    assert pl["slot"] == 2 and list(hub.slots) == [2], (pl, list(hub.slots))
                    ws.send_json({"t": "ping", "ts": 8})
                    assert until(ws, "pong")["ts"] == 8
                for _ in range(40):
                    if not hub.slots:
                        break
                    time.sleep(0.05)
                assert not hub.slots, hub.slots
                print("ok  vibrazioni dal gioco al telefono, sala giocatori, mouse solo se permesso")

                # salute: ogni controllo risponde, gli errori vengono prima
                items = c2.get("/api/health").json()["items"]
                assert items and all(x["level"] in ("ok", "warn", "error") for x in items), items
                assert items[0]["level"] == "error" and "ROM" in items[0]["title"], items[0]  # cartella "roms" inesistente
                assert not [x for x in items if x["title"] == "Controllo non riuscito"], items
                assert any("emulatore" in x["title"] for x in items), items  # PSP senza emulatore
                print("ok  pagina Salute")

                # console senza emulatore: errore chiaro
                r = c2.post(f"/api/game/{kh[0]}/play")
                assert r.status_code == 409 and "PlayStation Portable" in r.json()["detail"], r.text
                print("ok  messaggio chiaro se manca l'emulatore")
    print("TUTTI I TEST OK")


def test_names_settings_emulators():
    from gamescraper import platforms as P
    from gamescraper.library import launcher as L
    from gamescraper.scanner import clean_title, scene_serial

    cases = {
        "Sekiro.Shadows.Die.Twice.PS4-CUSA12047": "Sekiro Shadows Die Twice",
        "Bloodborne_PS4_CUSA00207_v1.09_MULTi": "Bloodborne",
        "The.Last.of.Us.Remastered.PS4-DUPLEX": "The Last of Us Remastered",
        "Persona 4 Golden (PCSE00812) (NTSC)": "Persona 4 Golden",
        "Dr. Mario (Japan, USA)": "Dr. Mario",
        "R.B.I. Baseball (USA)": "R.B.I. Baseball",
        "Legend of Zelda, The - A Link to the Past (USA)": "The Legend of Zelda - A Link to the Past",
    }
    for raw, want in cases.items():
        assert clean_title(raw) == want, (raw, clean_title(raw))
    assert scene_serial("Sekiro.Shadows.Die.Twice.PS4-CUSA12047") == "CUSA12047"
    print("ok  pulizia nomi (scene PS4, codici tra parentesi, nomi normali intatti)")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        (tmp / "config.toml").write_text('[paths]\nroms = "roms"\n', encoding="utf-8")
        emus = tmp / "Emulatori"
        (emus / "Ryujinx").mkdir(parents=True)
        (emus / "Ryujinx" / "Ryujinx.exe").write_bytes(b"")
        (emus / "melonDS 1.0").mkdir()
        (emus / "melonDS 1.0" / "melonDS.exe").write_bytes(b"")
        found = L.detect_emulators(emus)
        assert set(found) == {"ryujinx", "melonds"}, found
        cfg = config.load(tmp / "config.toml")
        config.save_settings(cfg, emulators=found, launch={"nds": {"emulator": "melonds"}},
                             platform_folders={"Vari": "gb"})
        cfg2 = config.load(tmp / "config.toml")  # riletto da disco
        assert cfg2.platform_folders == {"Vari": "gb"} and cfg2.launch["nds"]["emulator"] == "melonds"
        assert L.plan(cfg2, P.get("switch"))["emulator"] == "ryujinx"  # preset automatico
        assert L.plan(cfg2, P.get("nds"))["emulator"] == "melonds"
        config.save_settings(cfg2, launch={"nds": None})  # torna ad "Automatico"
        assert "nds" not in cfg2.launch and "nds" not in config.load(tmp / "config.toml").launch
        print("ok  emulatori: ricerca automatica, salvataggio in settings.json, ritorno ad automatico")


def test_relink_apply():
    """Riassociazione: i dati passano dalla vecchia chiave alla nuova (anche con le tabelle di userdata) e resta una copia .bak."""
    from gamescraper import relink
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        c = sqlite3.connect(tmp / "cache.db")
        c.execute("CREATE TABLE resources(key TEXT, source TEXT, field TEXT, value TEXT, ts REAL, PRIMARY KEY(key, source, field))")
        c.execute("CREATE TABLE lookups(key TEXT, source TEXT, status TEXT, ts REAL, PRIMARY KEY(key, source))")
        c.execute("INSERT INTO resources VALUES('switch/old','nsp','title','Celeste',1)")
        c.execute("INSERT INTO lookups VALUES('switch/new','nsp','notfound',1)")  # la nuova chiave ha gia' una ricerca vuota
        c.execute("INSERT INTO lookups VALUES('switch/old','nsp','found',1)")
        c.commit()
        c.close()
        u = sqlite3.connect(tmp / "userdata.db")
        u.execute("CREATE TABLE user_games(key TEXT PRIMARY KEY, favorite INT)")
        u.execute("INSERT INTO user_games VALUES('switch/old', 1)")
        u.commit()
        u.close()
        cfg = config.Config(cache=tmp, output=tmp / "out")
        relink.apply(cfg, [("switch/old", "switch/new", "Celeste", "x")], log=lambda m: None)
        c = sqlite3.connect(tmp / "cache.db")
        assert c.execute("SELECT key FROM resources").fetchall() == [("switch/new",)]
        assert c.execute("SELECT key, status FROM lookups").fetchall() == [("switch/new", "found")]  # vince il dato vecchio
        assert sqlite3.connect(tmp / "userdata.db").execute("SELECT key, favorite FROM user_games").fetchall() == [("switch/new", 1)]
        assert len(list(tmp.glob("*.bak"))) == 2
    print("ok  riassociazione: dati spostati sulla nuova chiave, copie .bak create")


def test_backup_restore():
    """Copia dei dati utente e ripristino con il database aperto dall'app (nessun riavvio)."""
    from gamescraper import backup
    from gamescraper.library.userdata import UserData
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        (tmp / "config.toml").write_text("[paths]", encoding="utf-8")
        cfg = config.load(tmp / "config.toml")
        config.save_settings(cfg, emulators={"ryujinx": "C:/R/Ryujinx.exe"})
        Path(cfg.cache).mkdir()
        user = UserData(Path(cfg.cache) / "userdata.db")
        user.set_flag("gba/AAA", "favorite", 1)
        first = backup.create(cfg)
        assert backup.auto(cfg) is None  # copia recente: niente copia automatica
        user.set_flag("gba/AAA", "favorite", 0)
        user.set_flag("gba/BBB", "favorite", 1)
        r = backup.restore(cfg, first.name, user=user)
        assert r["restored"] == ["userdata.db", "settings.json"], r
        g = user.games()
        assert g["gba/AAA"]["favorite"] == 1 and "gba/BBB" not in g, g  # stessa connessione, dati della copia
        names = [b["name"] for b in backup.list_backups(cfg)]
        assert r["safety_copy"] in names and first.name in names
        try:
            backup.restore(cfg, "../config.toml")
            raise AssertionError("percorso fuori dalla cartella delle copie accettato")
        except FileNotFoundError:
            pass
        user.db.close()
    print("ok  copie dei dati utente: creazione, ripristino a caldo, copia di sicurezza prima del ripristino")


def test_extra_rom_roots():
    """Piu' cartelle ROM: la principale con le console dentro, una aggiuntiva che e' essa stessa una console,
    una non raggiungibile (saltata, non blocca le altre)."""
    from gamescraper.app import jobs as J
    from gamescraper.scanner import find_platform_dirs
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t).resolve()  # macOS: /var -> /private/var, e config.load risolve i percorsi
        (tmp / "Roms" / "snes").mkdir(parents=True)
        (tmp / "Roms" / "snes" / "Mario.sfc").write_bytes(b"x" * 64)
        (tmp / "Altro disco" / "Switch" / "Celeste").mkdir(parents=True)
        (tmp / "config.toml").write_text('[paths]\nroms = "Roms"\n', encoding="utf-8")
        cfg = config.load(tmp / "config.toml")
        config.save_settings(cfg, paths={"extra_roms": [str(tmp / "Altro disco" / "Switch"), str(tmp / "Scollegato")]})
        cfg = config.load(tmp / "config.toml")  # riletto: le cartelle aggiunte dall'app restano
        assert len(config.rom_roots(cfg)) == 3
        found = {(p.id, f.name) for p, f in find_platform_dirs(cfg)}
        assert found == {("snes", "snes"), ("switch", "Switch")}, found
        pv = J.preview(cfg, set())
        assert [x["online"] for x in pv["extra"]] == [True, False], pv["extra"]
        assert {r["platform"]: r["new"] for r in pv["platforms"]} == {"snes": 1, "switch": 1}
        config.save_settings(cfg, paths={"extra_roms": []})  # tolte tutte
        assert config.rom_roots(config.load(tmp / "config.toml")) == [tmp / "Roms"]
    print("ok  piu' cartelle ROM (anche una console da sola, anche scollegate)")


def test_updates():
    """Confronto delle versioni, changelog trovato, controllo su GitHub con una risposta finta (niente rete)."""
    import httpx
    from gamescraper import __version__, updates
    from gamescraper.cache import Cache
    assert updates.parse("v0.2.10") > updates.parse("0.2.9") > updates.parse("0.2") and updates.parse("dev-abc") == (0,)
    assert "## " in updates.changelog()
    calls = []

    def fake(url, **k):
        calls.append(url)
        return httpx.Response(200, json={"tag_name": "v99.0.0", "html_url": "https://x/r", "body": "note"},
                              request=httpx.Request("GET", url))
    real, httpx.get = httpx.get, fake
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            c = Cache(Path(t))
            r = updates.check(c)
            assert r["newer"] and r["latest"] == "v99.0.0" and r["current"] == __version__, r
            updates.check(c)  # seconda volta: dalla cache, nessuna nuova richiesta
            assert len(calls) == 1, calls
            c.close()
    finally:
        httpx.get = real
    print("ok  versione, changelog e controllo aggiornamenti")

    # installazione dall'app (portatile Windows): scarica lo zip giusto, verifica il checksum, prepara lo script
    import hashlib
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("GameScraper/GameScraper.exe", "nuovo")
    data = buf.getvalue()
    base = f"https://github.com/{updates.REPO}/releases/download/v99.0.0/"
    assets = [{"name": "GameScraper-99.0.0-windows-x64-setup.exe", "browser_download_url": base + "setup.exe", "size": 1},
              {"name": "GameScraper-99.0.0-windows-x64.zip", "browser_download_url": base + "w.zip", "size": len(data),
               "digest": "sha256:" + hashlib.sha256(data).hexdigest()}]

    class Stream:
        def __init__(self, url):
            self.url = url
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def raise_for_status(self):
            pass
        def iter_bytes(self, n):
            yield data

    launched, quits = [], []
    saved = (httpx.get, httpx.stream, updates.install_kind, updates.app_dir, updates._detach)
    httpx.get = lambda url, **k: httpx.Response(200, json={"tag_name": "v99.0.0", "assets": assets},
                                                request=httpx.Request("GET", url))
    httpx.stream = lambda method, url, **k: Stream(url)
    updates._detach = launched.append
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            c = Cache(Path(t))
            updates.install_kind = lambda: None
            updates.install(c, quit=lambda: quits.append(1))
            assert updates.STATE["status"] == "error" and "sorgenti" in updates.STATE["phase"], updates.STATE
            updates.install_kind, updates.app_dir = (lambda: "win"), (lambda: Path(t) / "app")
            (Path(t) / "app").mkdir()
            updates.install(c, quit=lambda: quits.append(1))
            assert updates.STATE["status"] == "done" and quits == [1], updates.STATE
            script = Path(launched[-1][-1])
            body = script.read_text(encoding="utf-8")
            assert str(Path(t) / "app") in body and "/MIR" not in body and "/PURGE" not in body, body
            new = script.parent / "new" / "GameScraper" / "GameScraper.exe"
            assert new.read_text() == "nuovo", "lo zip va estratto prima di sostituire l'app"
            assets[1]["digest"] = "sha256:0"  # file manomesso: niente installazione
            launched.clear()
            updates.install(c, quit=lambda: quits.append(1))
            assert updates.STATE["status"] == "error" and "checksum" in updates.STATE["phase"] and not launched
            c.close()
    finally:
        httpx.get, httpx.stream, updates.install_kind, updates.app_dir, updates._detach = saved
    print("ok  aggiornamento dall'app: download verificato, script di sostituzione, niente dati cancellati")


def test_wishlist_keyshops():
    """Lista desideri: prezzo ufficiale (Steam) + prezzo dei negozi di key (GG.deals); la soglia vale anche per le key.
    Risposte finte, nessuna richiesta online."""
    import httpx
    from gamescraper import wishlist as W
    from gamescraper.library.userdata import UserData
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.host)
        if req.url.host == "api.gg.deals":
            assert req.url.params["key"] == "k" and req.url.params["region"] == "it"
            ids = req.url.params["ids"].split(",")
            data = {"202970": {"title": "Call of Duty: Black Ops II", "url": "https://gg.deals/game/bo2/",
                               "prices": {"currentRetail": "59.99", "currentKeyshops": "18.50", "historicalRetail": "19.99",
                                          "historicalKeyshops": "9.10", "currency": "EUR"}},
                    "999": {"title": "Fuori catalogo", "url": "https://gg.deals/game/old/",
                            "prices": {"currentRetail": None, "currentKeyshops": "4.00", "currency": "EUR"}}}
            return httpx.Response(200, json={"success": True, "data": {i: data.get(i) for i in ids}})
        # Steam: BO2 a prezzo pieno, l'altro non e' in vendita
        return httpx.Response(200, json={"202970": {"success": True, "data": {"price_overview": {
            "final": 5999, "initial": 5999, "discount_percent": 0, "currency": "EUR"}}}, "999": {"success": False}})

    real = W.httpx.Client
    W.httpx.Client = lambda **k: real(transport=httpx.MockTransport(handler), **k)
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            u = UserData(Path(t) / "userdata.db")
            u.wish_add("steam:202970", "Call of Duty: Black Ops II", "202970", target=20.0)
            u.wish_add("steam:999", "Fuori catalogo", "999", target=5.0)
            cfg = config.Config(cache=Path(t))
            cfg.app = {"wishlist": {"gg_key": "k", "country": "IT"}}
            r = W.check(cfg, u, log=lambda m: None)
            w = {x["id"]: x for x in u.wishlist()}
            bo2, old = w["steam:202970"], w["steam:999"]
            assert bo2["price"] == 59.99 and bo2["key_price"] == 18.5 and bo2["key_low"] == 9.1, bo2
            assert bo2["alert"].startswith("Sotto il tuo prezzo (20.00) nei negozi di key"), bo2["alert"]
            assert old["price"] is None and old["key_price"] == 4.0 and old["alert"], old  # solo nei negozi di key
            assert sorted(r["alerts"]) == ["Call of Duty: Black Ops II", "Fuori catalogo"], r
            assert "api.gg.deals" in seen
            # storico: un punto al giorno con il prezzo piu' basso visto
            today = time.strftime("%Y-%m-%d")
            assert u.wish_history()["steam:202970"] == [(today, 59.99, 18.5)]
            u.wish_price_point("steam:202970", 49.99, 25.0, today)
            assert u.wish_history()["steam:202970"] == [(today, 49.99, 18.5)]
            assert W.own_low([("2026-01-01", 30.0, 22.0), (today, 5.0, None)], today) == 22.0
            yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
            u.wish_price_point("steam:999", None, 6.0, yday)  # ieri 6 €, oggi 4 € nei negozi di key: avviso anche senza soglia
            u.wish_update("steam:999", target=None)
            W._save(u, {**w["steam:999"], "target": None}, None, None, 0, "", "", None, "EUR", 50, [], {"key": 4.0})
            assert "più basso da quando lo segui" in u.wishlist()[1]["alert"], u.wishlist()[1]
            u.wish_delete("steam:999")
            assert "steam:999" not in u.wish_history()
            u.db.close()
    finally:
        W.httpx.Client = real
    print("ok  lista desideri: prezzi dei negozi di key e soglia")


def test_eden_screenshot_dirs():
    """Schermate di Eden: cartella predefinita in APPDATA e percorso personalizzato letto da qt-config.ini."""
    import os
    from gamescraper.library import screens
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        (tmp / "eden" / "screenshots").mkdir(parents=True)
        (tmp / "eden" / "config").mkdir()
        (tmp / "Mie").mkdir()
        (tmp / "eden" / "config" / "qt-config.ini").write_text(
            f"[UI]\nScreenshots\\screenshot_path={(tmp / 'Mie').as_posix()}\n", encoding="utf-8")
        old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(tmp)
        try:
            dirs = {os.path.normcase(str(d)) for d in screens.emulator_dirs("eden", None)}
        finally:
            if old is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old
        assert os.path.normcase(str(tmp / "eden" / "screenshots")) in dirs, dirs
        assert os.path.normcase(str(tmp / "Mie")) in dirs, dirs
    print("ok  cartelle delle schermate di Eden")


def test_game_saves():
    """Salvataggi: riconosciuti per Eden (Title ID nel percorso), Ryujinx (Title ID in ExtraData0) e RetroArch (nome ROM);
    copia, ripristino con copia di sicurezza, e niente scritture fuori dai percorsi originali."""
    import os
    import struct
    import zipfile
    from gamescraper.library import saves as SV
    tid = "0100ABCD12340000"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        ed = tmp / "eden" / "nand" / "user" / "save" / "0000000000000000" / "USER1" / tid
        ed.mkdir(parents=True)
        (ed / "slot1.bin").write_bytes(b"v1")
        ry = tmp / "Ryujinx" / "bis" / "user" / "save" / "0000000000000007"
        (ry / "0").mkdir(parents=True)
        (ry / "ExtraData0").write_bytes(struct.pack("<Q", int(tid, 16)) + b"\x01" * 8)
        (ry / "0" / "data.sav").write_bytes(b"r1")
        ra = tmp / "RetroArch"
        (ra / "saves").mkdir(parents=True)
        (ra / "states" / "mGBA").mkdir(parents=True)
        (ra / "saves" / "Pokemon Rosso.srm").write_bytes(b"s1")
        (ra / "states" / "mGBA" / "Pokemon Rosso.state1").write_bytes(b"st")
        (ra / "saves" / "Altro.srm").write_bytes(b"no")
        old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(tmp)
        try:
            assert SV.game_saves("eden", None, title_id=tid.lower()) == [ed]
            assert SV.game_saves("ryujinx", None, title_id=tid) == [ry]
            rfiles = SV.game_saves("retroarch", str(ra / "retroarch.exe"), rom_stem="Pokemon Rosso")
            assert sorted(f.name for f in rfiles) == ["Pokemon Rosso.srm", "Pokemon Rosso.state1"], rfiles
            assert not SV.supported("dolphin") and SV.game_saves("dolphin", None, title_id=tid) == []
        finally:
            if old is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old
        cfg = config.Config(cache=tmp / "cache")
        b = SV.backup(cfg, "switch/x", "Gioco", [ed])
        (ed / "slot1.bin").write_bytes(b"v2-rovinato")
        r = SV.restore(cfg, "switch/x", b.name)
        assert (ed / "slot1.bin").read_bytes() == b"v1" and r["written"] == 1 and r["safety_copy"], r
        with zipfile.ZipFile(SV.folder(cfg, "switch/x") / r["safety_copy"]) as z:
            assert z.read("00/slot1.bin") == b"v2-rovinato"  # la copia di sicurezza ha i dati di prima
        rb = SV.backup(cfg, "gba/y", "Pokemon", rfiles)
        (ra / "saves" / "Pokemon Rosso.srm").write_bytes(b"xx")
        SV.restore(cfg, "gba/y", rb.name)
        assert (ra / "saves" / "Pokemon Rosso.srm").read_bytes() == b"s1"
        # zip manomesso: un nome con ".." non deve uscire dalla cartella originale
        evil = SV.folder(cfg, "switch/x") / "20990101-000000-x.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("manifest.json", json.dumps({"key": "switch/x", "items": [{"arc": "00", "path": str(ed), "dir": True}]}))
            z.writestr("00/../../fuori.txt", "x")
        SV.restore(cfg, "switch/x", evil.name)
        assert not (ed.parent.parent / "fuori.txt").exists() and not (ed.parent / "fuori.txt").exists()
        try:
            SV.restore(cfg, "gba/altro", b.name)
            raise AssertionError("copia di un altro gioco accettata")
        except FileNotFoundError:
            pass
    print("ok  salvataggi: Eden, Ryujinx, RetroArch; copia, ripristino, zip manomesso")


def test_mark_started():
    """Partita avviata (anche dal telefono): subito in «Giocati di recente» e «In corso», senza toccare «Finito»."""
    from gamescraper.library.userdata import UserData
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        u = UserData(Path(t) / "userdata.db")
        r0 = u.rev
        u.mark_started("gba/new")
        u.set_flag("gba/done", "status", "done")
        u.mark_started("gba/done")
        g = u.games()
        assert g["gba/new"]["status"] == "playing" and g["gba/new"]["last_played"] > 0, g
        assert g["gba/done"]["status"] == "done" and g["gba/done"]["last_played"] > 0, g
        assert u.rev == r0 + 3  # ogni scrittura cambia la revisione: l'app sul PC ricarica
        u.db.close()
    print("ok  partita avviata: subito tra i recenti e in corso")


def test_stream_optimize():
    """Ottimizza lo streaming: impostazioni lente di Sunshine e Wi-Fi segnalati; app «Gioco in corso» riconosciuta come nostra."""
    from gamescraper.remote import sunshine as SUN
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        d = Path(t)
        (d / "config").mkdir()
        (d / "config" / "sunshine.conf").write_text("locale = it\nnvenc_preset = 4\nnvenc_twopass = full_res\n# nota\n",
                                                   encoding="utf-8")
        saved = SUN.install_dir, SUN.service_state, SUN.start_mode, SUN.pc_link
        SUN.install_dir, SUN.service_state, SUN.start_mode = (lambda: d), (lambda: "running"), (lambda: "manual")
        SUN.pc_link = lambda: {"name": "Wi-Fi", "wifi": True, "speed": 866}
        try:
            assert SUN.read_conf() == {"locale": "it", "nvenc_preset": "4", "nvenc_twopass": "full_res"}
            titles = [c["title"] for c in SUN.optimize_checks()]
        finally:
            SUN.install_dir, SUN.service_state, SUN.start_mode, SUN.pc_link = saved
    assert any("non parte con Windows" in x for x in titles), titles
    assert any("P4" in x for x in titles) and any("Two-pass" in x for x in titles) and any("Wi-Fi" in x for x in titles), titles
    assert SUN.ours({"name": SUN.CURRENT}) and not SUN.ours({"name": "Desktop"}) and not SUN.ours({"name": SUN.CURRENT, "cmd": "x"})
    print("ok  ottimizza lo streaming: impostazioni di Sunshine e rete")


def test_single_data_dir():
    """exe e sorgenti usano la stessa cartella dati: GAMESCRAPER_CONFIG > accanto all'exe > cartella dati dell'utente."""
    import os
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t).resolve()  # macOS: /var/folders e' un link a /private/var/folders, e config.load risolve i percorsi
        shared, other = tmp / "dati", tmp / "progetto"
        shared.mkdir()
        other.mkdir()
        (shared / "config.toml").write_text("[paths]", encoding="utf-8")
        (other / "config.toml").write_text("[paths]", encoding="utf-8")
        saved = config.app_data_dir, os.environ.get("GAMESCRAPER_CONFIG"), os.getcwd()
        config.app_data_dir = lambda: shared
        os.environ.pop("GAMESCRAPER_CONFIG", None)
        try:
            os.chdir(other)  # un config.toml nella cartella corrente NON deve spostare i dati altrove
            assert config._default_config_path() == shared / "config.toml"
            assert config.load(None).cache == shared / "cache"
            os.environ["GAMESCRAPER_CONFIG"] = str(other / "config.toml")
            assert config._default_config_path() == other / "config.toml"
        finally:
            config.app_data_dir = saved[0]
            os.chdir(saved[2])
            if saved[1] is None:
                os.environ.pop("GAMESCRAPER_CONFIG", None)
            else:
                os.environ["GAMESCRAPER_CONFIG"] = saved[1]
    print("ok  una sola cartella dati per exe e sorgenti")


def test_library_parts():
    """Avvio rapido: prima i giochi posseduti (part=owned), poi il catalogo online (part=online)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        rom = tmp / "game.gba"
        rom.write_bytes(b"x")
        make_library(tmp, rom)
        db = sqlite3.connect(tmp / "library.db")
        db.execute("INSERT INTO games(key, platform, title, sort_title, genres, files, media, found, grp, is_primary, "
                   "origin, store) VALUES('gba/ON', 'gba', 'Online', 'online', '[]', '[]', '{}', 1, 'gba/on', 1, '{}', 'online')")
        db.execute("UPDATE games SET description = 'Una descrizione lunga', origin = '{\"title\": \"libretro\"}' "
                   "WHERE key = 'gba/AAA'")
        db.commit()
        db.close()
        cfg = config.Config(cache=tmp, output=tmp / "out")
        cfg.config_path = tmp / "config.toml"
        c = TestClient(create_app(cfg))
        own, onl, every = (c.get("/api/library" + q).json() for q in ("?part=owned", "?part=online", ""))
        assert sorted(r[1] for r in onl["games"]["rows"]) == ["Online"], onl["games"]["rows"]
        assert "Online" not in {r[1] for r in own["games"]["rows"]} and len(own["games"]["rows"]) == 4
        assert len(every["games"]["rows"]) == 5
        for lib in (own, onl):  # gli indici di gruppo valgono dentro ciascuna parte
            assert all(r[6] < len(lib["groups"]["rows"]) for r in lib["games"]["rows"])
            assert all(lib["groups"]["rows"][r[6]][0] == r[0] for r in lib["games"]["rows"] if r[1] != "Pocket Monsters - Ruby")
        # le colonne pesanti non stanno in memoria ma la scheda del gioco le ha complete
        ruby = next(r for r in own["games"]["rows"] if r[1] == "Pokemon - Versione Rubino")
        d = c.get(f"/api/game/{ruby[0]}").json()
        assert d["description"] == "Una descrizione lunga" and d["sources"] == {"title": "libretro"}, d
        assert "descrizione" not in d["missing"], d["missing"]  # in memoria ne resta il primo carattere
    print("ok  libreria in due parti: posseduti subito, catalogo online dopo; dettagli letti solo per la scheda")


if __name__ == "__main__":
    main()
    test_library_parts()
    test_names_settings_emulators()
    test_single_data_dir()
    test_relink_apply()
    test_backup_restore()
    test_extra_rom_roots()
    test_updates()
    test_wishlist_keyshops()
    test_eden_screenshot_dirs()
    test_game_saves()
    test_mark_started()
    test_stream_optimize()
