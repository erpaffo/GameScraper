"""API pubblica /api/v1: token, endpoint stabili, pagine, errori uniformi, idempotenza ed eventi SSE.

Non usa file reali: libreria finta in una cartella temporanea e un finto emulatore (python che dorme).

Esegui: python tests/test_apiv1.py
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
from gamescraper.app import apitoken  # noqa: E402
from gamescraper.app.apiv1 import sse  # noqa: E402
from gamescraper.app.extensions import Events  # noqa: E402
from gamescraper.app.server import LocalGuard, create_app  # noqa: E402
from gamescraper.library.build import SCHEMA  # noqa: E402

BASE = "http://127.0.0.1:8765"


def make_library(cache: Path, rom: Path):
    db = sqlite3.connect(cache / "library.db")
    db.executescript(SCHEMA)
    games = [  # (key, platform, title, grp, primary, year, store)
        ("gba/AAA", "gba", "Pokemon - Versione Rubino", "gba/s:AXV", 1, 2003, None),
        ("gba/BBB", "gba", "Pocket Monsters - Ruby", "gba/s:AXV", 0, 2003, None),
        ("gba/CCC", "gba", "Pokemon - Versione Smeraldo", "gba/s:BPE", 1, 2005, None),
        ("psp/DDD", "psp", "Kingdom Hearts - Birth by Sleep", "psp/i:khbbs", 1, 2010, None),
        ("steam/42", "windows", "Half-Life 2", "windows/s:hl2", 1, 2004, "steam"),
    ]
    for key, plat, title, grp, prim, year, store in games:
        db.execute("INSERT INTO games(key, platform, title, sort_title, year, genres, files, media, found, grp, "
                   "is_primary, origin, store, installed) VALUES(?,?,?,?,?, '[]', ?, '{\"cover\": \"x\"}', 1, ?, ?, "
                   "'{}', ?, ?)", (key, plat, title, title.lower(), year, json.dumps([str(rom)]), grp, prim, store,
                                  1 if store else None))
    db.execute("INSERT INTO collections VALUES('pokemon', 'Pokémon')")
    db.executemany("INSERT INTO collection_games VALUES('pokemon', ?)", [("gba/AAA",), ("gba/CCC",)])
    db.execute("INSERT INTO meta VALUES('built_at', '1')")
    db.commit()
    db.close()


def tmp_cfg(tmp: Path, rom: Path):
    cfg = config.Config(cache=tmp, output=tmp / "out")
    cfg.config_path = tmp / "config.toml"  # settings.json nella cartella temporanea
    cfg.emulators = {"melonds": sys.executable}
    cfg.launch = {"gba": {"emulator": "melonds", "args": ["-c", "import time; time.sleep(1.0)", "{rom}"]}}
    return cfg


def test_token(tmp: Path, cfg):
    """Il token esiste, resta lo stesso, si puo' cambiare e il confronto e' stretto."""
    t = apitoken.get(cfg)
    assert len(t) >= 32 and apitoken.get(cfg) == t, "il token deve restare lo stesso tra due letture"
    assert apitoken.check(cfg, t) and not apitoken.check(cfg, t[:-1]) and not apitoken.check(cfg, "")
    new = apitoken.rotate(cfg)
    assert new != t and apitoken.check(cfg, new) and not apitoken.check(cfg, t)
    if os.name == "posix":
        assert (apitoken.path(cfg).stat().st_mode & 0o777) == 0o600, oct(apitoken.path(cfg).stat().st_mode)
    print("ok  token: creato una volta, confronto stretto, rotazione")


def test_v1(tmp: Path, cfg):
    token = apitoken.get(cfg)
    auth = {"Authorization": f"Bearer {token}"}
    app = create_app(cfg)
    with TestClient(app, base_url=BASE) as c:
        # senza guardia il token non e' richiesto (e' LocalGuard a chiederlo): qui si prova la logica degli endpoint
        st = c.get("/api/v1/status").json()
        assert st["app"] == "gamescraper" and st["api"] == 1 and st["games"] == 5, st
        schema = c.get("/openapi.json").json()
        assert any(p.startswith("/api/v1/") for p in schema["paths"]), "lo schema deve documentare /api/v1"
        print("ok  schema OpenAPI con gli endpoint /api/v1")

        games = c.get("/api/v1/games").json()
        assert games["total"] == 4, games  # solo le versioni principali dei 5 giochi
        titles = [g["title"] for g in games["items"]]
        assert "Pocket Monsters - Ruby" not in titles, titles
        first = games["items"][0]
        assert first["cover"] == f"{BASE}/img/{first['id']}/cover", first
        assert set(first) >= {"id", "key", "title", "platform", "year", "favorite", "playtime"}, first
        print("ok  /api/v1/games: solo versioni principali, copertine assolute")

        assert c.get("/api/v1/games", params={"q": "rubino"}).json()["total"] == 1
        assert c.get("/api/v1/games", params={"platform": "psp"}).json()["total"] == 1
        assert c.get("/api/v1/games", params={"store": "steam"}).json()["total"] == 1
        page = c.get("/api/v1/games", params={"limit": 2, "offset": 1, "sort": "-year"}).json()
        assert page["limit"] == 2 and len(page["items"]) == 2 and page["items"][0]["year"] >= page["items"][1]["year"], page
        assert c.get("/api/v1/games", params={"versions": True}).json()["total"] == 5
        r = c.get("/api/v1/games", params={"sort": "boh"})
        assert r.status_code == 400 and r.json()["code"] == "bad_request", r.text
        print("ok  filtri, pagine, ordinamento e ordinamento non valido")

        # errore uniforme anche per gli endpoint interni (l'interfaccia legge ancora "detail")
        r = c.get("/api/v1/games/9999")
        assert r.status_code == 404 and r.json()["code"] == "not_found" and r.json()["detail"], r.text
        r = c.get("/api/game/9999")
        assert r.status_code == 404 and r.json()["code"] == "not_found", r.text
        print("ok  errori uniformi {detail, code} su /api/v1 e /api")

        # preferiti: valgono per tutte le versioni e si filtrano
        gid = next(g["id"] for g in games["items"] if g["title"] == "Pokemon - Versione Rubino")
        assert c.post(f"/api/v1/games/{gid}/favorite", json={"value": True}).json()["favorite"] is True
        assert c.get("/api/v1/games", params={"favorite": True}).json()["total"] == 1
        assert c.post(f"/api/v1/games/{gid}/favorite", json={"value": False}).json()["favorite"] is False
        print("ok  preferito dall'API")

        d = c.get(f"/api/v1/games/{gid}").json()
        assert "files" not in d and d["media"]["cover"].startswith(BASE), d
        print("ok  dettaglio senza percorsi locali, immagini assolute")

        assert c.get("/api/v1/platforms").json()["statuses"] == ["backlog", "playing", "done", "dropped"]
        coll = c.get("/api/v1/collections/pokemon").json()
        assert coll["name"] == "Pokémon" and len(coll["game_ids"]) == 2, coll
        assert c.get("/api/v1/collections/nonesiste").status_code == 404
        assert isinstance(c.get("/api/v1/wishlist").json()["items"], list)
        assert isinstance(c.get("/api/v1/jobs").json()["items"], list)
        print("ok  console, collezioni, lista desideri, lavori")

        # idempotenza: due avvii con la stessa chiave -> stessa risposta, un solo avvio
        r1 = c.post(f"/api/v1/games/{gid}/play", headers={"Idempotency-Key": "prova-1"})
        r2 = c.post(f"/api/v1/games/{gid}/play", headers={"Idempotency-Key": "prova-1"})
        assert r1.status_code == 200 and r1.json() == r2.json(), (r1.text, r2.text)
        assert c.get("/api/v1/running").json()["status"] in ("starting", "running", "finished")
        for _ in range(60):
            if c.get("/api/v1/running").json()["status"] in ("finished", "error"):
                break
            time.sleep(0.1)
        assert c.get("/api/v1/running").json()["status"] == "finished"
        assert c.post("/api/v1/running/stop").status_code == 200
        print("ok  idempotenza sull'avvio e attesa della fine")

        r = c.post("/api/v1/jobs/stop-game", headers={"Idempotency-Key": "stop-1"})
        assert r.status_code == 200 and "state" in r.json(), r.text
        assert c.post("/api/v1/jobs/boh").status_code == 404
        print("ok  comando di lavoro e comando sconosciuto -> 404")

        # validazione: la forma e' la stessa degli altri errori
        r = c.get("/api/v1/games", params={"limit": "molti"})
        assert r.status_code == 422 and r.json()["code"] == "invalid_request", r.text
        print("ok  errore di validazione con {detail, code}")


def test_guard(cfg):
    """LocalGuard: /api/v1 vuole il token (status no), mentre l'interfaccia interna resta com'era."""
    token = apitoken.get(cfg)
    app = LocalGuard(create_app(cfg), 8765)
    with TestClient(app, base_url=BASE) as c:
        assert c.get("/api/v1/status").status_code == 200
        r = c.get("/api/v1/games")
        assert r.status_code == 401 and r.json()["code"] == "unauthorized", r.text
        assert c.get("/api/v1/games", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert c.get("/api/v1/games", headers={"X-Api-Token": token}).status_code == 200
        assert c.get("/api/v1/games", params={"token": token}).status_code == 200  # EventSource non manda intestazioni
        assert c.get("/api/v1/games", headers={"Authorization": "Bearer sbagliato"}).status_code == 401
        assert c.get("/api/library").status_code == 200  # l'interfaccia non e' toccata
        assert c.post("/api/v1/games/1/play").status_code == 401  # vale anche per le POST
        assert c.get("/api/api-token").json()["token"] == token
    print("ok  LocalGuard: token su /api/v1, status libero, /api intatto")


def test_sse():
    """Gli eventi arrivano come fotogrammi SSE e chi ascolta non blocca l'app."""
    ev = Events()
    gen = sse(ev, heartbeat=5)
    assert next(gen) == ": connesso\n\n"
    ev.emit("library_built", {"games": 4})
    frame = next(gen)
    assert "event: library_built" in frame and '"games": 4' in frame, frame
    ev.emit("game_stopped", {"title": "Rubino"})
    assert "event: game_stopped" in next(gen)
    gen.close()
    assert not ev.subs, "chiudendo il flusso l'ascoltatore deve sparire"
    print("ok  SSE: eventi in streaming e ascoltatori rimossi")


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        rom = tmp / "game.gba"
        rom.write_bytes(b"x" * 100)
        make_library(tmp, rom)
        cfg = tmp_cfg(tmp, rom)
        test_token(tmp, cfg)
        test_guard(cfg)
        test_v1(tmp, cfg)
        test_sse()
    print("TUTTI I TEST OK")


if __name__ == "__main__":
    main()
