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
             "Game-win32.zip", "Game.pdb.zip", "Game-windows-x64.zip.sha256", "Game-android.apk", "Game-setup.exe"]
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
    PT._fetch_catalog = lambda: [entry]  # «↻ Aggiorna» rilegge il catalogo: niente rete nei test
    PT._download = lambda url, dest, progress=None, token="": Path(dest).write_bytes(builds[release["v"]])

    app = create_app(cfg)
    with TestClient(app) as cl:
        d = cl.get("/api/ports").json()
        it = d["items"][0]
        assert it["roms"] and it["roms"][0]["title"] == "Snow Game" and not it["installed"], it
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


if __name__ == "__main__":
    test_assets()
    test_rom_negli_store()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_install_update_rom(Path(t))
    print("TUTTI I TEST OK")
