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


if __name__ == "__main__":
    main()
    test_names_settings_emulators()
