"""Test di store PC, estensioni (temi/plugin) e avvio/chiusura dei giochi PC. Nessun file reale toccato.

Esegui: python tests/test_stores.py
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

from pozzo import config  # noqa: E402
from pozzo.app.server import create_app  # noqa: E402
from pozzo.cache import Cache  # noqa: E402
from pozzo.library.build import build  # noqa: E402
from pozzo.stores.base import parse_vdf  # noqa: E402


def test_vdf():
    d = parse_vdf('"libraryfolders"\n{\n "0"\n {\n  "path"  "C:\\\\Steam"\n  "apps" { "105600" "123" }\n }\n}')
    assert d["libraryfolders"]["0"]["path"] == "C:\\Steam" and d["libraryfolders"]["0"]["apps"]["105600"] == "123"
    print("ok  lettura file VDF di Steam")


def make_env(tmp: Path):
    (tmp / "roms").mkdir()
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n', encoding="utf-8")
    cfg = config.load(tmp / "config.toml")
    c = Cache(cfg.cache)
    snap = lambda store, gid, title, inst, launch: {  # noqa: E731
        "store": store, "id": gid, "title": title, "installed": inst, "install_dir": "", "launch": launch,
        "install_uri": f"{store}://install/{gid}", "page": "", "playtime": 3600, "last_played": 0, "images": {},
        "genres": [], "description": "", "developer": "", "publisher": "", "release_date": "2017", "media": {}}
    c.kv_set("store:steam", [snap("steam", "1", "Hollow Knight", True, {"type": "uri", "target": "steam://rungameid/1"})])
    c.kv_set("store:epic", [snap("epic", "abc", "Hollow Knight", False, {"type": "uri", "target": "x"}),
                            snap("epic", "def", "Celeste", False, {"type": "uri", "target": "y"})])
    # DLC: resta in libreria con il suo tipo (colonna content), non più scartata
    c.kv_set("store:gog", [snap("gog", "9", "Celeste - Farewell DLC", False, {"type": "uri", "target": "z"})])
    # gioco "manuale": python che dorme, per misurare il tempo di gioco e provare la chiusura
    c.kv_set("store:manual", [snap("manual", "1", "Finto gioco", True,
                                   {"type": "exe", "target": sys.executable, "args": ["-c", "import time; time.sleep(30)"], "cwd": ""})])
    c.close()
    return cfg


def test_library_and_api(tmp: Path):
    cfg = make_env(tmp)
    stats = build(cfg, log=lambda *a: None)
    assert stats["pc"] == 5, stats
    db = sqlite3.connect(Path(cfg.cache) / "library.db")
    rows = db.execute("SELECT store, installed, is_primary, grp FROM games WHERE platform='windows' AND title='Hollow Knight'").fetchall()
    db.close()
    assert len({r[3] for r in rows}) == 1, "stesso gioco su Steam ed Epic = un gruppo"
    assert [r for r in rows if r[2]][0][0] == "steam", "principale = la versione installata"
    print("ok  giochi degli store nella libreria, stesso gioco su piu' store unito")

    # plugin di prova nella cartella dell'utente
    pdir = tmp / "plugins" / "prova"
    pdir.mkdir(parents=True)
    (pdir / "plugin.toml").write_text('[plugin]\nname = "Prova"\nversion = "0.1"\napi = 1\n', encoding="utf-8")
    (pdir / "frontend.js").write_text("export default function (gs) {}", encoding="utf-8")
    (pdir / "backend.py").write_text(
        "EVENTS = []\n"
        "def register(gs):\n"
        "    @gs.route('ciao')\n"
        "    def ciao():\n"
        "        return {'ciao': gs.id, 'eventi': EVENTS}\n"
        "    gs.on('game_stopped', lambda d: EVENTS.append(d.get('title')))\n", encoding="utf-8")
    # Statistiche non e' piu' inclusa: installata come farebbe lo store
    import shutil
    shutil.copytree(Path(__file__).resolve().parent.parent / "extensions" / "statistiche", tmp / "plugins" / "statistiche")
    app = create_app(cfg)
    with TestClient(app) as c:
        ext = c.get("/api/extensions").json()
        assert {"default", "oled", "chiaro", "retro", "notte-blu"} <= {t["id"] for t in ext["themes"]}
        ids = {p["id"]: p for p in ext["plugins"]}
        assert "prova" in ids and "statistiche" in ids and ids["prova"]["enabled"]
        assert ext["plugins"][0]["id"] == "estensioni" and ext["plugins"][0]["locked"]  # lo store per primo
        assert c.get("/plugins/prova/frontend.js").status_code == 200
        assert c.get("/themes/oled/theme.css").status_code == 200
        assert c.get("/themes/oled/../../server.py").status_code == 404  # niente uscita dalla cartella del tema
        assert c.get("/plugin-api/prova/ciao").json()["ciao"] == "prova"
        assert "per_piattaforma" in c.get("/plugin-api/statistiche/riepilogo").json()
        assert c.post("/api/app/theme", json={"theme": "oled"}).status_code == 200
        print("ok  temi e plugin: elenco, file statici, endpoint dei plugin, cambio tema")

        lib = c.get("/api/library").json()
        cols = lib["games"]["cols"]
        rows = [dict(zip(cols, r)) for r in lib["games"]["rows"]]
        fake = next(r for r in rows if r["title"] == "Finto gioco")
        celeste = next(r for r in rows if r["title"] == "Celeste")
        dlc = next(r for r in rows if r["title"] == "Celeste - Farewell DLC")
        assert dlc["content"] == "dlc" and celeste["content"] == "game", (dlc["content"], celeste["content"])
        assert c.post(f"/api/game/{celeste['id']}/play").status_code == 409  # non installato
        d = c.get(f"/api/game/{celeste['id']}").json()
        assert d["store"] == "epic" and not d["installed"] and d["can_install"]

        # DLC nascoste per console: impostazione per console, subito nella risposta della libreria
        assert c.get("/api/library").json()["hide_dlc"] == []
        assert c.post("/api/app/hide-dlc", json={"platform": "switch", "hide": True}).json()["hide_dlc"] == ["switch"]
        assert c.get("/api/library").json()["hide_dlc"] == ["switch"]
        assert c.post("/api/app/hide-dlc", json={"platform": "switch", "hide": False}).json()["hide_dlc"] == []
        assert c.post("/api/app/hide-dlc", json={"hide": True}).status_code == 400
        print("ok  «Nascondi DLC»: per console, salvata in settings.json e restituita con la libreria")

        # avvio del gioco manuale, poi chiusura come con Select+Start
        assert c.post(f"/api/game/{fake['id']}/play").status_code == 200
        for _ in range(50):
            if c.get("/api/running").json().get("status") == "running":
                break
            time.sleep(0.1)
        time.sleep(1.2)
        assert c.post("/api/running/stop").json()["stopped"]
        for _ in range(100):
            st = c.get("/api/running").json()
            if st.get("status") in ("finished", "error"):
                break
            time.sleep(0.1)
        assert st["status"] == "finished", st
        d = c.get(f"/api/game/{fake['id']}").json()
        assert d["user"]["play_count"] == 1 and d["user"]["playtime"] >= 1, d["user"]
        assert c.get("/plugin-api/prova/ciao").json()["eventi"] == ["Finto gioco"], "evento game_stopped ai plugin"
        print(f"ok  gioco PC: avvio, chiusura dal controller, tempo registrato ({d['user']['playtime']:.1f} s), evento ai plugin")


if __name__ == "__main__":
    test_vdf()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_library_and_api(Path(t))
    print("TUTTI I TEST OK")
