"""Sincronizzazione tra due PC (cartella condivisa) e profili utente. Nessuna rete: due cartelle dati temporanee."""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gamescraper import config, sync  # noqa: E402


def pc(root: Path, name: str, shared: Path):
    d = root / name
    (d / "cache").mkdir(parents=True)
    (d / "config.toml").write_text("[paths]", encoding="utf-8")
    cfg = config.Config(cache=d / "cache", output=d / "out")
    cfg.config_path = d / "config.toml"
    opened = {}

    def user_of(pid):
        if pid not in opened:
            opened[pid] = sync.open_profile(cfg, pid)
        return opened[pid]
    sync.save_conf(cfg, {**sync.conf(cfg), "folder": str(shared), "pc_name": name})
    return cfg, user_of, sync.Sync(cfg, user_of)


def test_folder_sync_and_profiles():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        root = Path(t)
        shared = root / "OneDrive"
        shared.mkdir()
        cfg_a, ua, sa = pc(root, "A", shared)
        cfg_b, ub, sb = pc(root, "B", shared)
        a, b = ua("default"), ub("default")

        # stati diversi sui due PC, prima della prima sincronizzazione
        a.set_flag("snes/X", "favorite", 1)
        a.set_flag("snes/Y", "note", "da finire")
        a.record_play("snes/X", 100)
        b.set_flag("snes/Y", "status", "done")
        b.record_play("snes/X", 50)
        b.wish_add("steam:1", "Celeste", "504230")
        slug = b.create_collection("Mie")
        b.edit_membership(slug, "snes/Y", "add")

        sa.run(), sb.run(), sa.run()
        for u in (a, b):
            g = u.games()
            assert g["snes/X"]["favorite"] == 1 and g["snes/Y"]["note"] == "da finire" and g["snes/Y"]["status"] == "done", g
            assert round(g["snes/X"]["playtime"]) == 150 and g["snes/X"]["play_count"] == 2, g["snes/X"]
            assert [w["id"] for w in u.wishlist()] == ["steam:1"] and slug in u.collections()
            assert (slug, "snes/Y", "add") in u.edits()
        print("ok  cartella condivisa: preferiti, note, stati, tempo sommato, lista desideri e collezioni su entrambi")

        # di nuovo: nulla cambia (nessun tempo contato due volte)
        r = sa.run()
        assert all(not any(v.values()) for m in r["folder"]["merged"].values() for v in m.values()), r
        assert round(a.games()["snes/X"]["playtime"]) == 150

        # vince la modifica piu' recente, le cancellazioni arrivano sull'altro PC
        time.sleep(0.01)
        a.set_flag("snes/X", "favorite", 0)
        b.wish_delete("steam:1")
        b.delete_collection(slug)
        sa.run(), sb.run(), sa.run()
        for u in (a, b):
            assert u.games()["snes/X"]["favorite"] == 0 and u.wishlist() == [] and slug not in u.collections()
        print("ok  vince la modifica piu' recente; preferito tolto, desiderio e collezione cancellati su entrambi")

        # profili: dati separati, stesso nome = stesso profilo sull'altro PC, impostazioni del PC comuni
        luca = sync.add_profile(cfg_a, "Luca")
        assert luca["id"] == "luca" and [p["id"] for p in sync.profiles(cfg_a)] == ["default", "luca"]
        ua("luca").set_flag("snes/Z", "favorite", 1)
        assert "snes/Z" not in a.games(), "i dati di un profilo non finiscono negli altri"
        a.set_profile(["snes/Z"], {"emulator": "snes9x"})
        assert ua("luca").profile("snes/Z") == {"emulator": "snes9x"}, "i profili di avvio sono comuni a tutti i profili"
        sa.run(), sb.run()
        assert [p["name"] for p in sync.profiles(cfg_b)] == ["Principale", "Luca"]
        assert ub("luca").games()["snes/Z"]["favorite"] == 1 and "snes/Z" not in b.games()
        print("ok  profili: dati separati, sincronizzati per nome, profili di avvio comuni")

        # impostazioni: solo se scelto, e mai i percorsi del PC
        for c in (cfg_a, cfg_b):
            sync.save_conf(c, {**sync.conf(c), "what": "all"})
        config.save_settings(cfg_a, paths={"roms": "D:/ROM"})
        config.set_setting(cfg_a, "catalogs", {"gba": ["https://esempio.test/gba/"]})
        sa.run(), sb.run()
        s = config.read_settings(cfg_b)
        assert s.get("catalogs") == {"gba": ["https://esempio.test/gba/"]} and "roms" not in (s.get("paths") or {}), s
        print("ok  impostazioni sincronizzate solo se scelto, senza i percorsi del PC")

        # eliminare un profilo non cancella i dati: il database va in profiles/eliminati
        ua("luca").db.close()
        moved = sync.delete_profile(cfg_a, "luca")
        assert moved and moved.exists() and [p["id"] for p in sync.profiles(cfg_a)] == ["default"]
        for u in (a, b, ub("luca")):
            u.db.close()


def test_server_profiles_and_lan_gate():
    """Profili dall'app (dati separati nella libreria) e cancello del server di rete per i PC abbinati."""
    from fastapi.testclient import TestClient

    from gamescraper.app.server import create_app
    from gamescraper.remote.phone import PhoneGate
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_frontend import make_library
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        rom = tmp / "game.gba"
        rom.write_bytes(b"x")
        make_library(tmp, rom)
        cfg = config.Config(cache=tmp, output=tmp / "out")
        cfg.config_path = tmp / "config.toml"
        app = create_app(cfg)
        c = TestClient(app)
        gid = c.get("/api/library?part=owned").json()["games"]["rows"][0][0]
        c.post(f"/api/game/{gid}/flag", json={"field": "favorite", "value": True})
        fav = lambda: sum(r[1] for r in c.get("/api/library?part=owned").json()["groups"]["rows"])  # noqa: E731
        assert fav() == 1
        rev = c.get("/api/library?part=owned").json()["user_rev"]
        st = c.post("/api/profiles", json={"name": "Luca"}).json()
        assert [p["name"] for p in st["profiles"]] == ["Principale", "Luca"]
        assert c.post("/api/profiles/active", json={"id": "luca"}).json()["active"] == "luca"
        lib = c.get("/api/library?part=owned").json()
        assert fav() == 0 and lib["user_rev"] != rev, "il profilo nuovo parte senza preferiti e le pagine ricaricano"
        c.post("/api/profiles/active", json={"id": "default"})
        assert fav() == 1
        assert c.delete("/api/profiles/luca").json()["moved_to"]
        print("ok  profili dall'app: dati separati, cambio di profilo, eliminazione senza perdere il database")

        hub = app.state.phone_hub
        gate = TestClient(PhoneGate(app, hub), base_url="https://testserver")
        hub.new_code()
        bad = gate.post("/api/sync/pair", json={"code": "000000", "name": "PC finto"})
        assert bad.status_code == 403
        code = hub.new_code()
        r = gate.post("/api/sync/pair", json={"code": code, "name": "Portatile"}).json()
        pc_cookie = {"gs_phone": r["token"]}
        assert r["pc"]["pc_id"]
        ex = gate.post("/api/sync/exchange", json={"bundles": {}}, cookies=pc_cookie)
        assert ex.status_code == 200 and "default" in ex.json()["bundles"], ex.text
        assert gate.get("/api/library", cookies=pc_cookie).status_code == 403, "un PC abbinato vede solo lo scambio"
        phone = hub.pair(hub.new_code(), "Telefono")
        assert gate.post("/api/sync/exchange", json={"bundles": {}}, cookies={"gs_phone": phone}).status_code == 403
        assert c.post("/api/sync/exchange", json={"bundles": {}}).status_code == 403, "senza abbinamento niente dati"
        st = c.get("/api/sync").json()
        assert [p["name"] for p in st["pcs"]] == ["Portatile"]
        assert all(d.get("kind") != "pc" for d in c.get("/api/phone").json()["devices"])
        print("ok  rete locale: abbinamento con codice, i PC raggiungono solo lo scambio, i telefoni no")


def test_lan_end_to_end():
    """Due app vere: A accende il server HTTPS (solo su 127.0.0.1), B si abbina con il codice e sincronizza."""
    import socket

    from fastapi.testclient import TestClient

    from gamescraper.app.server import create_app
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        apps = {}
        for name in ("A", "B"):
            d = Path(t) / name
            d.mkdir()
            cfg = config.Config(cache=d / "cache", output=d / "out")
            cfg.config_path = d / "config.toml"
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            cfg.app["phone"] = {"host": "127.0.0.1", "port": port}
            app = create_app(cfg)
            apps[name] = (cfg, app, TestClient(app), port)
        (cfg_a, app_a, ca, port_a), (cfg_b, app_b, cb, _) = apps["A"], apps["B"]
        try:
            code = ca.post("/api/sync/code").json()["code"]
            ca.post("/api/sync/peer", json={"url": "x", "code": "1"})  # indirizzo sbagliato: errore, nessun crash
            r = cb.post("/api/sync/peer", json={"url": f"127.0.0.1:{port_a}", "code": code})
            assert r.status_code == 200 and r.json()["peers"][0]["url"] == f"https://127.0.0.1:{port_a}", r.text
            peer = sync.conf(cfg_b)["peers"][0]
            assert len(peer["fp"]) == 64, "impronta del certificato salvata all'abbinamento"
            assert cb.post("/api/sync/peer", json={"url": f"127.0.0.1:{port_a}", "code": code}).status_code == 400, \
                "un codice vale una volta"
            # dati diversi sui due PC, uno scambio da B li unisce su entrambi
            sync.open_profile(cfg_a, "default").set_flag("gba/A1", "favorite", 1)
            sync.open_profile(cfg_b, "default").set_flag("gba/B1", "favorite", 1)
            last = cb.post("/api/sync/now").json()["last"]
            assert last["peers"][peer["id"]].get("ok"), last
            for cfg in (cfg_a, cfg_b):
                g = sync.open_profile(cfg, "default").games()
                assert g["gba/A1"]["favorite"] == 1 and g["gba/B1"]["favorite"] == 1, g
            # certificato diverso da quello abbinato: B si rifiuta di mandare il token
            c = sync.conf(cfg_b)
            c["peers"][0]["fp"] = "0" * 64
            sync.save_conf(cfg_b, c)
            last = cb.post("/api/sync/now").json()["last"]
            assert "certificato" in last["peers"][peer["id"]]["error"], last
        finally:
            app_a.state.phone_hub.stop()
    print("ok  rete locale, due app vere: abbinamento, scambio nei due sensi, certificato verificato")


if __name__ == "__main__":
    test_folder_sync_and_profiles()
    test_server_profiles_and_lan_gate()
    test_lan_end_to_end()
    print("TUTTI I TEST OK")
