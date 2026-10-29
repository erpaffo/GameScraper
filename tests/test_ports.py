"""Test dei port PC: scelta dell'asset Windows, installazione, aggiornamento con i file dell'utente conservati,
collegamento con le ROM della libreria e copia della ROM con controllo dell'hash. Nessuna rete.

Esegui: python tests/test_ports.py
"""
import hashlib
import io
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from pozzo import config  # noqa: E402
from pozzo import ports as PT  # noqa: E402
from pozzo.app.server import create_app  # noqa: E402
from pozzo.cache import Cache  # noqa: E402
from pozzo.library.build import build  # noqa: E402

ROM = b"N64ROM" * 2000


def test_assets():
    names = ["Game-linux-x64.tar.gz", "Game-macos.dmg", "Game-windows-arm64.zip", "Game-windows-x64.zip",
             "Game-win32.zip", "Game.pdb.zip", "Game-windows-x64.zip.sha256", "Game-android.apk", "Game-setup.exe",
             "Game-source.zip", "Game-dedicated-win64.zip"]
    assets = [{"name": n, "url": n, "size": 0} for n in names]
    assert [a["name"] for a in assets if PT.is_windows_asset(a["name"])] == [
        "Game-windows-arm64.zip", "Game-windows-x64.zip", "Game-win32.zip", "Game-setup.exe"]
    assert PT.pick_asset(assets)["name"] == "Game-windows-x64.zip", "x64 e zip prima di tutto"
    assert PT.pick_asset(assets, "win32")["name"] == "Game-win32.zip", "filtro del catalogo (releaseAssetFilter)"
    assert not PT.is_windows_asset("Studios-BIOS-PortMaster.zip") and PT.is_windows_asset("BIOS-tools.zip")
    rels = [{"tag": "v2-beta", "prerelease": True, "url": "", "assets": assets},
            {"tag": "v1", "prerelease": False, "url": "", "assets": assets}]
    assert PT.pick_release(rels)[0]["tag"] == "v1", "prima la release stabile"
    assert PT.platform_of("gcn") == "gc" and PT.platform_of("n64") == "n64" and PT.platform_of("pc") is None
    print("ok  asset Windows scelto come Quiver (x64, zip, niente simboli/altri sistemi)")


def _zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return b.getvalue()


def test_rom_negli_store():
    """ROM non posseduta ma presente in uno store online della stessa console: la si propone da scaricare."""
    entry = {"name": "Ship of Harkinian", "consoles": ["n64"], "games": [{"title": "The Legend of Zelda: Ocarina of Time"}]}
    oot = {"id": 7, "title": "The Legend of Zelda: Ocarina of Time", "platform": PT.platform_of("n64"), "store": "online"}
    other = {**oot, "id": 8, "platform": "psx"}
    games = [oot, other, {"id": 9, "title": "Mario", "platform": oot["platform"], "store": "online"}]
    assert PT.roms_for(entry, PT.rom_index(games)) == []                       # non e' una ROM posseduta
    assert PT.roms_for(entry, PT.rom_index(games, online=True)) == [oot]       # solo stessa console e titolo
    print("ok  ROM dei port cercata anche negli store online")


def test_install_update_rom(tmp: Path):
    (tmp / "roms" / "n64").mkdir(parents=True)
    (tmp / "roms" / "n64" / "Snow Game (USA).z64").write_bytes(ROM)
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n', encoding="utf-8")
    cfg = config.load(tmp / "config.toml")
    build(cfg, log=lambda *a: None)
    sha = hashlib.sha1(ROM).hexdigest()
    entry = {"slug": "snow-recomp", "name": "Snow Game", "consoles": ["n64"], "supportedOS": ["windows"],
             "games": [{"title": "Snow Game"}], "lastReleaseVersion": "1.0", "libraryArt": {},
             "developer": {"name": "Dev"}, "description": "Port nativo",
             "launcher": {"folderName": "SnowRecomp", "filesToAdd": ["portable.txt"],
                          "preferredExecutables": {"windows": ["snow.exe"]},
                          "setupFiles": [{"id": "rom", "label": "rom", "destination": "rom.z64", "extensions": ["z64"],
                                          "accepted": [{"algo": "sha1", "label": "US", "value": sha}]}]}}
    c = Cache(cfg.cache)
    c.kv_set("ports:catalog", {"time": time.time(), "items": [entry]})
    c.kv_set("ports:app:snow-recomp", {"entry": entry, "project": {"provider": "github", "repository": "dev/snow"}})
    c.close()
    release = {"v": "1.0"}
    builds = {"1.0": _zip({"SnowRecomp/snow.exe": "exe1", "SnowRecomp/crashpad_handler.exe": "x", "SnowRecomp/data.bin": "d1"}),
              "2.0": _zip({"SnowRecomp/snow.exe": "exe2", "SnowRecomp/data.bin": "d2"})}
    PT.releases = lambda provider, repo, token="": [{"tag": release["v"], "prerelease": False, "url": "u",
                                                     "assets": [{"name": "Snow-windows-x64.zip", "url": "z", "size": 0}]}]
    PT.catalog_releases = lambda cache, slug: []  # niente rete: nessuna release «verificata» da confrontare
    PT._fetch_catalog = lambda: [entry]  # «↻ Aggiorna» rilegge il catalogo: niente rete nei test
    PT._download = lambda url, dest, progress=None, token="": Path(dest).write_bytes(builds[release["v"]])

    app = create_app(cfg)
    with TestClient(app) as cl:
        d = cl.get("/api/ports").json()
        it = d["items"][0]
        assert it["roms"] and it["roms"][0]["title"] == "Snow Game" and not it["installed"], it
        assert it["type"] == "port", it  # il catalogo puo' avere anche giochi ed emulatori
        rom_game = it["roms"][0]["id"]
        assert cl.get(f"/api/game/{rom_game}").json()["ports"] == [{"slug": "snow-recomp", "name": "Snow Game"}]
        print("ok  catalogo: il port riconosce la ROM della libreria e la scheda della ROM mostra il port")

        def wait():
            for _ in range(200):
                j = cl.get("/api/ports/job").json()
                if j["status"] != "running":
                    return j
                time.sleep(0.05)
        assert cl.post("/api/ports/install", json={"slug": "snow-recomp"}).status_code == 200
        j = wait()
        assert j["status"] == "done", j
        folder = PT.ports_dir(cfg) / "SnowRecomp"
        assert (folder / "snow.exe").read_text() == "exe1" and (folder / "portable.txt").exists(), "cartella appiattita + portable.txt"
        assert (folder / "rom.z64").read_bytes() == ROM, "ROM compatibile copiata automaticamente dopo il download"
        m = cl.get("/api/ports/snow-recomp").json()
        assert m["exe"] == "snow.exe" and m["candidates"] == ["snow.exe"], m  # crashpad_handler scartato
        for _ in range(400):
            if cl.get("/api/stores/refresh").json()["status"] != "running":
                break
            time.sleep(0.05)
        gid = cl.get("/api/ports").json()["items"][0]["installed"]["game_id"]
        g = cl.get(f"/api/game/{gid}").json()
        assert g["store"] == "ports" and g["installed"] and g["platform"] == "windows", g
        print("ok  installazione: release Windows estratta in Ports\\SnowRecomp, eseguibile scelto, gioco PC in libreria")

        assert cl.post(f"/api/game/{gid}/play", json={}).json() == {"ask": "window"}, "senza scelta salvata si chiede"
        assert cl.post("/api/ports/snow-recomp/play", json={}).json() == {"ask": "window"}
        assert PT.window_mode(cfg, "snow-recomp", {"window": "borderless"}) == "borderless"
        assert PT.window_mode(cfg, "snow-recomp", {}) is None, "senza «ricorda» non si salva"
        assert PT.window_mode(cfg, "snow-recomp", {"window": "windowed", "remember": True}) == "windowed"
        assert PT.window_mode(cfg, "snow-recomp", {}) == "windowed"
        assert cl.post("/api/ports/snow-recomp/window", json={"window": "boh"}).status_code == 400
        assert cl.post("/api/ports/snow-recomp/window", json={"window": ""}).status_code == 200
        assert cl.get("/api/ports/snow-recomp").json().get("window") is None
        print("ok  finestra all'avvio: si chiede, «ricorda» la salva nel manifesto, il menu la cambia")

        r = cl.post("/api/ports/snow-recomp/rom", json={"id": "rom"})
        assert r.status_code == 200 and (folder / "rom.z64").read_bytes() == ROM, r.text
        print("ok  prima configurazione: ROM della libreria copiata dopo il controllo dell'hash")

        (folder / "save" / "slot1.sav").parent.mkdir()
        (folder / "save" / "slot1.sav").write_text("partita")
        release["v"] = "2.0"
        d = cl.get("/api/ports?force=1").json()
        assert d["items"][0]["installed"]["update"] and d["items"][0]["installed"]["latest"] == "2.0", d["items"][0]
        cl.post("/api/ports/snow-recomp/update", json={})
        assert wait()["status"] == "done"
        assert (folder / "snow.exe").read_text() == "exe2" and not (folder / "crashpad_handler.exe").exists()
        assert (folder / "save" / "slot1.sav").read_text() == "partita" and (folder / "rom.z64").exists(), "file dell'utente conservati"
        assert (PT.ports_dir(cfg) / ".backup" / "SnowRecomp" / "snow.exe").read_text() == "exe1", "copia di sicurezza"
        assert not cl.get("/api/ports").json()["items"][0]["installed"]["update"]
        print("ok  aggiornamento: copia di sicurezza, salvataggi e ROM conservati, file vecchi tolti")

        man = PT.read_manifest(folder)
        assert {p.relative_to(folder).as_posix() for p in PT.user_files(folder, man)} == {"save/slot1.sav", "rom.z64"}
        sv = cl.get(f"/api/game/{gid}/saves").json()
        assert sv["supported"] and len(sv["paths"]) == 2, sv
        assert cl.post(f"/api/game/{gid}/saves").status_code == 200, "backup dei salvataggi del port"
        release["v"] = "3.0"
        builds["3.0"] = builds["2.0"]
        cl.post("/api/ports/snow-recomp/skip", json={"version": "3.0"})
        assert not cl.get("/api/ports?force=1").json()["items"][0]["installed"]["update"], "versione saltata"
        print("ok  «salta questa versione»; salvataggi del port = file non della release")

    try:
        PT._inside(folder, "../fuori.txt")
        raise AssertionError("percorso fuori dalla cartella accettato")
    except ValueError:
        pass
    z = tmp / "rom.zip"
    z.write_bytes(_zip({"readme.txt": "x", "Snow Game (USA).z64": ROM}))
    sf = entry["launcher"]["setupFiles"][0]
    assert PT.find_rom(sf, [z]) == (z, "Snow Game (USA).z64"), "ROM dentro uno zip"
    assert PT.find_rom({**sf, "accepted": [{"algo": "sha1", "value": "00"}]}, [z]) is None
    print("ok  ROM trovata anche negli zip; percorsi fuori dal port rifiutati")


def test_checksum_e_repository(tmp: Path):
    """Lo SHA-256 della release del catalogo verifica il download (un file diverso viene rifiutato) e
    «gitlab:utente/progetto» (o un indirizzo completo) diventa provider + repository."""
    (tmp / "roms").mkdir()
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n', encoding="utf-8")
    cfg = config.load(tmp / "config.toml")
    data = _zip({"App/app.exe": "exe"})
    good = hashlib.sha256(data).hexdigest()
    saved = (PT._download, PT.catalog_releases)
    PT._download = lambda url, dest, progress=None, token="": Path(dest).write_bytes(data)
    try:
        # stessa release di GitHub, ma il catalogo Quiver ne dichiara il checksum
        PT.catalog_releases = lambda cache, slug: [
            {"tag": "v1.0", "prerelease": False, "url": "", "released": 1,
             "assets": [{"name": "App-windows-x64.zip", "url": "z", "size": 0, "sum": good}]}]
        assert PT.verified_asset(None, "app", "V1.0")["sum"] == good, "tag «V1.0» e «v1.0» sono la stessa release"
        assert PT.verified_asset(None, "app", "v9") is None
        asset = PT.verified_asset(None, "app", "v1.0")
        man = PT.deploy(cfg, "App", {"slug": "app", "name": "App", "files_to_add": [], "preferred": []},
                        {"tag": "v1.0", "url": "u"}, asset, say=lambda m: None)
        assert man["sha256"] == good and PT.read_manifest(PT.ports_dir(cfg) / "App")["sha256"] == good
        try:
            PT.deploy(cfg, "Rotto", {"slug": "rotto", "name": "Rotto", "files_to_add": [], "preferred": []},
                      {"tag": "v1.0", "url": "u"}, {**asset, "sum": "0" * 64}, say=lambda m: None)
            raise AssertionError("doveva rifiutare un file con il checksum sbagliato")
        except RuntimeError as e:
            assert "checksum" in str(e), e
        assert not (PT.ports_dir(cfg) / "Rotto").exists()
    finally:
        PT._download, PT.catalog_releases = saved
    assert PT.split_repo("utente/progetto") == ("github", "utente/progetto")
    assert PT.split_repo("gitlab:gruppo/sottogruppo/progetto") == ("gitlab", "gruppo/sottogruppo/progetto")
    assert PT.split_repo("https://github.com/a/b.git") == ("github", "a/b")
    assert PT.split_repo("https://gitlab.com/a/b") == ("gitlab", "a/b")
    try:
        PT.split_repo("non un repository")
        raise AssertionError("repository non valido accettato")
    except ValueError:
        pass
    print("ok  SHA-256 della release verificato (file diverso rifiutato) e repository GitLab/GitHub riconosciuti")


def test_aggiorna_da_solo(tmp: Path):
    """«Aggiorna da solo» installa solo le voci con una versione nuova (quelle saltate restano ferme)."""
    (tmp / "roms").mkdir()
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n', encoding="utf-8")
    cfg = config.load(tmp / "config.toml")
    data = _zip({"App/app.exe": "exe"})
    saved = (PT._download, PT.update_info, PT.install)
    PT._download = lambda url, dest, progress=None, token="": Path(dest).write_bytes(data)
    try:
        for slug in ("uno", "due"):
            PT.deploy(cfg, "App-" + slug, {"slug": slug, "name": slug, "files_to_add": [], "preferred": []},
                      {"tag": "1.0", "url": "u"}, {"name": "App-windows-x64.zip", "url": "z", "size": 0},
                      say=lambda m: None)
        PT.update_info = lambda cache, m, token="", force=False, offline=False: {
            "latest": "2.0", "update": m["slug"] != "due"}  # «due» ha la versione saltata
        calls = []
        PT.install = lambda cfg, cache, slug, progress=None, say=print: calls.append(slug) or {"slug": slug}
        assert PT.update_all(cfg, None, say=lambda m: None) == ["uno"], calls
    finally:
        PT._download, PT.update_info, PT.install = saved
    print("ok  aggiornamento automatico: solo le voci da aggiornare, quella saltata resta ferma")


def test_estrazione_7z(tmp: Path):
    """Un .7z che tar non riesce ad aprire: senza 7-Zip l'errore lo dice chiaramente (niente eccezioni oscure)."""
    arc = tmp / "finto.7z"
    arc.write_bytes(b"non e' un archivio 7z")
    saved = PT._7zip
    try:
        PT._7zip = lambda: None
        try:
            PT._extract(arc, tmp / "uno", "finto.7z")
            raise AssertionError("doveva rifiutare un archivio non estraibile")
        except RuntimeError as e:
            assert "7-Zip" in str(e), e
        with zipfile.ZipFile(tmp / "vero.zip", "w") as z:  # gli zip non hanno bisogno di nulla
            z.writestr("game.exe", "x")
        PT._extract(tmp / "vero.zip", tmp / "due", "vero.zip")
        assert (tmp / "due" / "game.exe").is_file()
    finally:
        PT._7zip = saved
    print("ok  estrazione: zip diretto, .7z con 7-Zip (senza, errore chiaro)")


def test_percorso_breve(tmp: Path):
    """Un port che compila dentro la propria cartella (SMS Launcher) non sta in un percorso lungo: si installa
    in una cartella breve, e quello gia' installato si sposta conservando salvataggi e impostazioni."""
    (tmp / "roms").mkdir()
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n', encoding="utf-8")
    cfg = config.load(tmp / "config.toml")
    long_dir = tmp / ("cartella-dei-port-molto-lunga-" + "x" * 40)
    breve = tmp / "breve"
    saved = (PT.ports_dir, PT.short_dir)
    PT.ports_dir = lambda cfg_: (long_dir.mkdir(parents=True, exist_ok=True) or long_dir)
    PT.short_dir = lambda cfg_: (breve.mkdir(parents=True, exist_ok=True) or breve)
    try:
        man = {"slug": "sms", "repository": "chasem-dev/sms-launcher", "folder": str(long_dir / "SMSLauncher")}
        assert PT.path_problem(cfg, {**man, "folder": str(breve / "SMSLauncher")}) is None, "percorso corto: nessun problema"
        p = PT.path_problem(cfg, man)
        assert p and p["folder"] == man["folder"] and p["limit"] == 70 and p["short"] == str(breve / "SMSLauncher"), p
        assert PT.path_problem(cfg, {**man, "repository": "altro/progetto"}) is None, "solo i port con un limite noto"
        assert PT._target(cfg, "SMSLauncher", "sms", "chasem-dev/sms-launcher") == breve / "SMSLauncher", "installazione diretta nella cartella breve"
        assert PT._target(cfg, "Altro", "altro", "altro/progetto") == long_dir / "Altro", "gli altri port restano nella cartella dei port"
        assert "port_links" not in cfg.app or "sms" not in (cfg.app.get("port_links") or {})
        # quello gia' installato si sposta dall'API (in background, come le installazioni), con i file dell'utente
        (long_dir / "SMSLauncher").mkdir(parents=True)
        (long_dir / "SMSLauncher" / "sms.exe").write_text("exe")
        (long_dir / "SMSLauncher" / ".pozzo-port.json").write_text(
            '{"slug": "sms", "name": "SMS", "repository": "chasem-dev/sms-launcher", "files": ["sms.exe"]}',
            encoding="utf-8")
        (long_dir / "SMSLauncher" / "SMS-Launcher-Portable").mkdir()
        (long_dir / "SMSLauncher" / "SMS-Launcher-Portable" / "preferences.json").write_text("{}")
        app = create_app(cfg)
        with TestClient(app) as cl:
            assert cl.get("/api/ports/sms").json()["path"]["limit"] == 70, "la scheda del port mostra il problema"
            assert cl.post("/api/ports/sms/short-path").status_code == 200
            for _ in range(200):
                j = cl.get("/api/ports/job").json()
                if j["status"] != "running":
                    break
                time.sleep(0.05)
            assert j["status"] == "done", j
            assert not cl.get("/api/ports/sms").json()["path"], "dopo lo spostamento il percorso va bene"
        assert (breve / "SMSLauncher" / "sms.exe").is_file(), "eseguibile copiato"
        assert (breve / "SMSLauncher" / "SMS-Launcher-Portable" / "preferences.json").is_file(), "impostazioni del launcher copiate"
        assert not (long_dir / "SMSLauncher").exists(), "la cartella lunga non resta in giro"
        assert any((long_dir / ".backup").glob("SMSLauncher-percorso-lungo/sms.exe")), "copia di sicurezza dell'originale"
        assert PT.all_installed(cfg)["sms"]["folder"] == str(breve / "SMSLauncher"), "la libreria usa la cartella nuova"
        assert PT._target(cfg, "SMSLauncher", "sms", "chasem-dev/sms-launcher") == breve / "SMSLauncher", "gli aggiornamenti restano nella cartella breve"
        assert PT.path_problem(cfg, PT.all_installed(cfg)["sms"]) is None
        PT.uninstall(cfg, "sms")
        assert not (breve / "SMSLauncher").exists(), "disinstallando un port spostato si libera anche il disco"
        assert "sms" not in (cfg.app.get("port_links") or {})
    finally:
        PT.ports_dir, PT.short_dir = saved
    print("ok  percorso lungo: rilevato, spostato in una cartella breve con i dati, libreria aggiornata")


if __name__ == "__main__":

    test_assets()
    test_rom_negli_store()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_estrazione_7z(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_checksum_e_repository(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_aggiorna_da_solo(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_install_update_rom(Path(t))
    if sys.platform == "win32":  # il limite dei percorsi lunghi vale solo su Windows (path_problem)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            test_percorso_breve(Path(t))
    print("TUTTI I TEST OK")
