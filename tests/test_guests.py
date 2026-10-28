"""Ospite a distanza (feature «gioca con un amico»): invito, ingresso, e cosa puo' raggiungere.

Copre: il codice d'invito e' separato da quello dei telefoni, vale una volta sola, si blocca dopo i tentativi
sbagliati; l'ospite entra come un altro controller e il cancello gli lascia solo il pad (niente libreria,
niente impostazioni, niente mouse); il link usa l'indirizzo pubblico quando c'e'.

Esegui: python tests/test_guests.py  (oppure pytest tests/test_guests.py)
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, WebSocket  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from pozzo import config  # noqa: E402
from pozzo.remote import phone as PH  # noqa: E402


def make_cfg() -> config.Config:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "roms").mkdir(exist_ok=True)
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n', encoding="utf-8")
    return config.load(tmp / "config.toml")


def new_hub() -> PH.PhoneHub:
    return PH.PhoneHub(make_cfg(), config.save_settings)


def test_invito_e_ingresso():
    hub = new_hub()
    code = hub.new_invite()
    assert len(code) == 6 and code.isdigit(), code
    token = hub.join(code, "Amico")
    dev = hub.device_for(token)
    assert dev and dev["kind"] == "guest" and dev["name"] == "Amico", dev
    assert hub.invite()["code"] is None, "l'invito vale una volta sola"
    try:
        hub.join(code, "Un altro")
        raise AssertionError("lo stesso invito ha fatto entrare due ospiti")
    except ValueError:
        pass
    st = hub.status()
    assert [g["name"] for g in st["guests"]] == ["Amico"], st["guests"]
    assert all(d["name"] != "Amico" for d in st["devices"]), "l'ospite non e' un telefono abbinato"
    print("ok  invito: codice di 6 cifre, uso singolo, ospite separato dai telefoni")


def test_invito_separato_dai_telefoni():
    hub = new_hub()
    phone_code = hub.new_code()
    invite = hub.new_invite()
    try:
        hub.join(phone_code, "Ospite")
        raise AssertionError("il codice dei telefoni fa entrare un ospite")
    except ValueError:
        pass
    try:
        hub.pair(invite, "Telefono", kind="phone")
        raise AssertionError("il codice dell'invito abbina un telefono")
    except ValueError:
        pass
    print("ok  i due codici non si scambiano: telefono e ospite restano separati")


def test_invito_si_blocca_dopo_i_tentativi():
    hub = new_hub()
    code = hub.new_invite()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(PH.MAX_FAILS):
        try:
            hub.join(wrong, "Curioso")
        except ValueError:
            pass
    try:
        hub.join(code, "Amico")
        raise AssertionError("dopo i tentativi sbagliati l'invito funziona ancora")
    except ValueError as e:
        assert "scaduto" in str(e), e
    print(f"ok  dopo {PH.MAX_FAILS} tentativi sbagliati l'invito non vale piu'")


def test_link_con_indirizzo_pubblico():
    hub = new_hub()
    hub._save(public_url="amico.trycloudflare.com/")
    code = hub.new_invite()
    assert hub.base_url() == "https://amico.trycloudflare.com", hub.base_url()
    link = hub.invite()["link"]
    assert link == f"https://amico.trycloudflare.com/g/#code={code}", link
    hub._save(public_url="")
    assert "/g/" in hub.invite()["link"], hub.invite()  # senza tunnel resta la rete di casa
    print("ok  link d'invito sull'indirizzo pubblico, altrimenti sulla rete di casa")


def test_cancello_ospite():
    hub = new_hub()
    inner = FastAPI()

    @inner.get("/m/api/me")
    def me():
        return {"ok": True}

    @inner.post("/m/api/logout")
    def logout():
        return {"ok": True}

    @inner.get("/api/library")
    def library():
        return {"ok": True}

    @inner.get("/api/mobile/state")
    def state():
        return {"ok": True}

    @inner.get("/g/api/hello")
    def hello():
        return {"ok": True}

    @inner.post("/g/api/pin")
    def pin():
        return {"ok": True}

    @inner.websocket("/m/ws")
    async def ws(w: WebSocket):
        await w.accept()
        await w.send_json({"dev": w.scope.get("state", {}).get("phone_device")})
        await w.close()

    gate = TestClient(PH.PhoneGate(inner, hub))
    assert gate.get("/api/library").status_code == 401, "senza abbinamento si passa"
    assert gate.get("/g/api/hello").status_code == 200, "la pagina dell'ospite e' pubblica"

    token = hub.join(hub.new_invite(), "Amico")
    gate.cookies.set(PH.COOKIE, token)
    assert gate.get("/m/api/me").status_code == 200
    for path in ("/api/library", "/api/mobile/state"):
        assert gate.get(path).status_code == 403, f"l'ospite raggiunge {path}"
    assert gate.post("/g/api/pin", json={"pin": "1234"}).status_code == 200
    with gate.websocket_connect("/m/ws") as w:
        dev = w.receive_json()["dev"]
    assert dev == hub.device_for(token)["id"], "il controller dell'ospite non arriva al server"

    phone = hub.pair(hub.new_code(), "Telefono", kind="phone")
    gate.cookies.set(PH.COOKIE, phone)
    assert gate.get("/api/library").status_code == 200, "il telefono abbinato ha perso la libreria"
    print("ok  cancello: l'ospite ha solo il pad (e il PIN), il telefono abbinato ha tutto")


def test_ospite_nel_hello_del_telefono():
    from pozzo.app.server import create_app

    cfg = make_cfg()
    api = TestClient(create_app(cfg))
    hub = api.app.state.phone_hub
    try:
        assert api.get("/m/api/hello").json() == {"paired": False, "guest": False}, "senza cookie non e' abbinato"
        token = hub.join(hub.new_invite(), "Amico")
        api.cookies.set(PH.COOKIE, token)
        assert api.get("/m/api/hello").json() == {"paired": True, "guest": True}
        assert api.get("/g/api/hello").json()["paired"] is True
        page = api.get("/g/")
        assert page.status_code == 200 and "Moonlight" in page.text, "la pagina dell'ospite non si apre"
    finally:
        for handler in getattr(api.app.router, "on_shutdown", []):
            try:
                handler()
            except Exception:
                pass
        api.close()
    print("ok  /m/api/hello dice all'app del telefono quando e' un ospite")


def test_scheda_del_pc():
    """La scheda «Gioca con un amico» legge e salva l'indirizzo pubblico; gli ospiti non si confondono coi telefoni."""
    from pozzo.app.server import create_app

    api = TestClient(create_app(make_cfg()))
    try:
        st = api.get("/api/phone").json()
        assert {"invite", "guests", "public_url", "video_addr"} <= set(st), sorted(st)
        assert st["invite"]["code"] is None, "nessun invito prima che lo si chieda"
        st = api.post("/api/phone", json={"public_url": "https://amico.trycloudflare.com/",
                                          "video_addr": "100.101.102.103"}).json()
        assert st["public_url"] == "https://amico.trycloudflare.com", st["public_url"]
        assert st["video_addr"] == "100.101.102.103", st["video_addr"]
        assert st["invite"]["link"].startswith("https://amico.trycloudflare.com/g/"), st["invite"]["link"]
        assert api.get("/g/api/hello").json()["video"] == "100.101.102.103"
    finally:
        for handler in getattr(api.app.router, "on_shutdown", []):
            try:
                handler()
            except Exception:
                pass
        api.close()
    print("ok  scheda del PC: indirizzo pubblico, indirizzo per Moonlight e invito")


if __name__ == "__main__":
    test_invito_e_ingresso()
    test_invito_separato_dai_telefoni()
    test_invito_si_blocca_dopo_i_tentativi()
    test_link_con_indirizzo_pubblico()
    test_cancello_ospite()
    test_ospite_nel_hello_del_telefono()
    test_scheda_del_pc()
    print("tutti i test dell'ospite a distanza sono passati")
