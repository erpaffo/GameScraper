"""Telefono: PWA installabile, telecomando del PC, impostazioni del pad sul PC, prova controller,
avvisi di Windows e spegnimento a fine lavori. Nessuna rete, nessuna notifica vera."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from pozzo import config  # noqa: E402
from pozzo.app.server import create_app  # noqa: E402

_LIVE: list = []


def client(cfg):
    api = TestClient(create_app(cfg))
    _LIVE.append(api)
    return api


def teardown_function(_=None):
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
    (tmp / "config.toml").write_text('[paths]\nroms = "roms"\ncache = "cache"\n[scrape]\nsources = []\n', encoding="utf-8")
    return config.load(tmp / "config.toml")


class Patch:
    """Sostituisce un attributo e lo rimette com'era: vale sia con pytest sia lanciando lo script a mano."""

    def __init__(self):
        self._undo: list = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)
        self._undo.clear()


def paired_gate(cfg):
    """Cancello del telefono come in produzione, con un telefono gia' abbinato (cookie)."""
    from pozzo.remote.phone import PhoneGate

    app = create_app(cfg)
    hub = app.state.phone_hub
    gate = TestClient(PhoneGate(app, hub), base_url="https://testserver")
    token = gate.post("/m/api/pair", json={"code": hub.new_code(), "name": "Pixel"}).cookies.get("gs_phone")
    return app, gate, {"gs_phone": token}


def close(app, gate):
    for handler in getattr(app.router, "on_shutdown", []):
        try:
            handler()
        except Exception:
            pass
    gate.close()


def test_pwa_installabile(tmp_path):
    """Service worker raggiungibile senza abbinamento (serve al browser prima del pairing) e manifest completo."""
    cfg = make_cfg(tmp_path)
    app, gate, _ = paired_gate(cfg)
    try:
        r = gate.get("/m/sw.js")
        assert r.status_code == 200, r.text
        assert r.headers.get("service-worker-allowed") == "/m/", dict(r.headers)
        assert "gs-m-" in r.text and "/m/static/m.js" in r.text
        man = gate.get("/m/manifest.webmanifest").json()
        assert man["scope"] == "/m/" and man["start_url"] == "/m/" and man["id"] == "/m/"
        assert {"192x192", "512x512"} <= {i["sizes"] for i in man["icons"]}
        # la pagina registra il service worker e spiega cosa manca se il certificato non e' fidato
        js = gate.get("/m/static/m.js").text
        assert 'navigator.serviceWorker.register("/m/sw.js"' in js
        assert "beforeinstallprompt" in js and "install-btn" in js
        # il certificato della CA si scarica senza abbinamento (serve proprio a fidarsi del PC)
        gate.cookies.clear()
        ca = gate.get("/m/ca.crt")
        assert ca.status_code == 200, ca.text
        assert "x-x509-ca-cert" in ca.headers["content-type"], ca.headers
        assert ca.content.startswith(b"-----BEGIN CERTIFICATE-----")
    finally:
        close(app, gate)
    print("ok  PWA: sw.js pubblico con ambito /m, manifest con icone 192/512, CA scaricabile, pulsante Installa")


def test_certificato_firmato_dalla_ca(tmp_path):
    """Il certificato del server e' firmato da una CA locale: senza, il telefono mostra «non privata» e il browser
    rifiuta il service worker (app non installabile). La CA deve restare la stessa quando cambia l'IP."""
    from cryptography import x509
    from cryptography.x509.oid import ExtendedKeyUsageOID

    from pozzo.remote import certs

    folder = tmp_path / "certs"
    crt, key, ca = certs.ensure(folder)
    assert crt.exists() and key.exists() and ca.exists() and ca.name == "ca.crt"
    leaf = x509.load_pem_x509_certificate(crt.read_bytes())
    root = x509.load_pem_x509_certificate(ca.read_bytes())
    assert leaf.issuer == root.subject and leaf.subject != root.subject, "il certificato e' ancora autofirmato"
    assert root.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    ski = root.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
    aki = leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value.key_identifier
    assert aki == ski, "il telefono non collegherebbe il certificato alla CA"
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    assert "127.0.0.1" in ips and "localhost" in {str(d) for d in san.get_values_for_type(x509.DNSName)}
    # secondo giro: niente viene rifatto (il telefono non va riconfigurato)
    first = leaf.serial_number
    crt2, _, ca2 = certs.ensure(folder)
    assert crt2 == crt and ca2 == ca
    assert x509.load_pem_x509_certificate(crt2.read_bytes()).serial_number == first

    # indirizzo nuovo: si rifa' solo il certificato del server, la CA resta quella
    mp = Patch()
    try:
        mp.setattr(certs, "lan_ips", lambda: ["10.9.9.9"])
        crt3, _, ca3 = certs.ensure(folder)
        renewed = x509.load_pem_x509_certificate(crt3.read_bytes())
        assert renewed.serial_number != first, "certificato non rifatto dopo il cambio di indirizzo"
        assert "10.9.9.9" in {str(i) for i in renewed.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)}
        assert renewed.issuer == x509.load_pem_x509_certificate(ca3.read_bytes()).subject
        assert ca3.read_bytes() == ca.read_bytes(), "la CA e' cambiata: il telefono andrebbe riconfigurato"
    finally:
        mp.undo()

    # certificato autofirmato di una versione precedente (o file sbagliato): viene sostituito
    crt.write_bytes(ca.read_bytes())
    crt4, _, _ = certs.ensure(folder)
    fixed = x509.load_pem_x509_certificate(crt4.read_bytes())
    assert fixed.serial_number != x509.load_pem_x509_certificate(ca.read_bytes()).serial_number
    assert fixed.issuer == x509.load_pem_x509_certificate(ca.read_bytes()).subject

    # il PC puo' salvare il certificato (per passarlo al telefono se il download sul telefono viene bloccato)
    cfg = make_cfg(tmp_path)
    api = client(cfg)
    dl = api.get("/api/phone/ca")
    assert dl.status_code == 200, dl.text
    assert "x-x509-ca-cert" in dl.headers["content-type"], dl.headers
    print("ok  certificato del server firmato dalla CA locale, CA stabile, rifatto al cambio di IP")


def test_libreria_pronta_in_cache(tmp_path):
    """La libreria non viene ricostruita a ogni apertura (0,5 s e 8 MB su una libreria grande): stessa risposta
    finche' nulla cambia, e nuova appena cambia un dato utente. E' quello che rende rapida l'app del telefono."""
    import json
    import sqlite3

    from pozzo.library.build import SCHEMA

    cache = tmp_path / "cache"
    cache.mkdir()
    db = sqlite3.connect(cache / "library.db")
    db.executescript(SCHEMA)
    db.executemany(
        "INSERT INTO games(key, platform, title, sort_title, year, genres, files, media, found, grp, is_primary, origin) "
        "VALUES(?,?,?,?,?,'[]','[]','{}',1,?,?, '{}')",
        [("gba/AAA", "gba", "Pokemon Rubino", "pokemon rubino", 2003, "gba/s:AXV", 1),
         ("gba/BBB", "gba", "Pokemon Zaffiro", "pokemon zaffiro", 2003, "gba/s:BXV", 1)])
    db.commit()
    db.close()

    cfg = config.Config(cache=cache, output=tmp_path / "out")
    cfg.config_path = tmp_path / "config.toml"
    api = client(cfg)
    a = api.get("/api/library?part=owned")
    assert a.status_code == 200 and len(a.json()["games"]["rows"]) == 2, a.text
    b = api.get("/api/library?part=owned")
    assert b.content == a.content, "la seconda richiesta deve usare la risposta gia' pronta"

    gid = a.json()["games"]["rows"][0][0]
    r = api.post(f"/api/game/{gid}/flag", json={"field": "favorite", "value": True})
    assert r.status_code == 200, r.text
    c = api.get("/api/library?part=owned")
    assert c.content != a.content, "cambiando un dato utente la libreria va ricostruita"
    gcols = c.json()["groups"]["cols"]
    fav = [row[gcols.index("favorite")] for row in c.json()["groups"]["rows"]]
    assert any(fav), "il preferito appena messo deve comparire subito"
    print("ok  libreria in cache: risposta pronta al secondo giro, rifatta dopo una modifica")


def test_pad_salvato_sul_pc(tmp_path):
    cfg = make_cfg(tmp_path)
    app, gate, ck = paired_gate(cfg)
    try:
        assert gate.get("/m/api/pad", cookies=ck).json() == {}
        r = gate.post("/m/api/pad", cookies=ck, json={
            "layout": "ps", "sens": 99, "gyro": "boh", "invy": 1, "scale": 0.1,
            "pos": {"ps": {"ls": {"x": 200, "y": -5, "k": 9}}, "inventato": {"ls": {"x": 1, "y": 1}}, "gc": {"boh": {}}}})
        assert r.status_code == 200, r.text
        pad = r.json()
        assert pad["layout"] == "ps" and pad["sens"] == 3.0 and pad["scale"] == 0.8, pad
        assert "gyro" not in pad and pad["invy"] is True, pad          # valori non previsti: scartati
        assert pad["pos"] == {"ps": {"ls": {"x": 95.0, "y": 0.0, "k": 1.8}}}, pad["pos"]
        assert gate.get("/m/api/pad", cookies=ck).json() == pad        # resta sul PC
        saved = config.read_settings(cfg)["app"]["phone"]["pad"]
        assert saved["layout"] == "ps" and saved["pos"]["ps"]["ls"]["x"] == 95.0
        # corpo enorme: rifiutato invece di riempire le impostazioni
        big = gate.post("/m/api/pad", cookies=ck, json={"pos": {f"x{i}": {} for i in range(2000)}})
        assert big.status_code == 400, big.text
    finally:
        close(app, gate)
    print("ok  aspetti del pad salvati sul PC, con valori riportati nei limiti")


def test_ca_installata_nasconde_il_qr(tmp_path):
    """La casella «certificato gia' installato» resta salvata sul PC e il QR non viene piu' generato."""
    cfg = make_cfg(tmp_path)
    api = client(cfg)
    assert api.get("/api/phone").json()["ca_installed"] is False
    assert api.post("/api/phone", json={"ca_installed": True}).json()["ca_installed"] is True
    assert config.read_settings(cfg)["app"]["phone"]["ca_installed"] is True
    assert api.get("/api/phone").json()["ca_installed"] is True
    assert api.post("/api/phone", json={"ca_installed": False}).json()["ca_installed"] is False
    print("ok  certificato gia' installato: scelta salvata sul PC")


def test_telecomando(tmp_path, monkeypatch=None):
    """Volume, play/pausa, Esc, Invio, Alt+Tab: solo i tasti dell'elenco arrivano al PC."""
    from pozzo.remote import keyboard

    mp = monkeypatch or Patch()
    try:
        api = client(make_cfg(tmp_path))
        assert api.post("/api/mobile/action", json={"action": "key", "key": "rm -rf /"}).status_code == 400
        assert api.post("/api/mobile/action", json={"action": "key"}).status_code == 400
        sent = []
        mp.setattr(keyboard, "press", lambda name: sent.append(name))
        for k in ("vol_up", "play", "esc", "alt_tab"):
            r = api.post("/api/mobile/action", json={"action": "key", "key": k})
            assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert sent == ["vol_up", "play", "esc", "alt_tab"], sent
    finally:
        mp.undo()
    # l'elenco e' chiuso: nessun testo arbitrario digitato sul PC
    for bad in ("rm -rf", "ctrl+shift+esc", ""):
        try:
            keyboard.press(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"tasto non previsto accettato: {bad!r}")
    print("ok  telecomando: solo i tasti previsti, gli altri rifiutati (400)")


def test_prova_controller(tmp_path):
    """Lo stato dei pad si legge solo dal PC (sul telefono il cancello lo blocca)."""
    cfg = make_cfg(tmp_path)
    api = client(cfg)
    d = api.get("/api/phone/pads").json()
    assert d["pads"] == [] and "vigem" in d
    assert api.post("/api/phone/pads/rumble", json={"slot": 0}).status_code == 400  # nessun telefono collegato
    app, gate, ck = paired_gate(cfg)
    try:
        assert gate.get("/api/phone/pads", cookies=ck).status_code == 403
        assert gate.post("/api/phone/pads/rumble", json={"slot": 0}, cookies=ck).status_code == 403
    finally:
        close(app, gate)
    print("ok  prova controller: leggibile dal PC, non dal telefono")


def test_power_a_fine_lavori(tmp_path):
    from pozzo import power

    api = client(make_cfg(tmp_path))
    assert api.get("/api/app/power").json()["after_jobs"] == "none"
    assert api.post("/api/app/power", json={"after_jobs": "formattare"}).status_code == 400
    d = api.post("/api/app/power", json={"after_jobs": "shutdown"}).json()
    assert d["after_jobs"] == "shutdown" and d["seconds"] == 0, d
    if sys.platform == "win32":
        power.schedule("sleep", 600)  # programmato e subito annullato: il PC resta acceso
        assert api.get("/api/app/power").json()["seconds"] > 500
        assert api.post("/api/app/power/abort", json={}).json()["cancelled"] is True
        assert api.get("/api/app/power").json()["seconds"] == 0
        assert power.cancel() is False  # niente piu' in attesa
    print("ok  sospensione/spegnimento a fine lavori: scelta salvata e annullabile")


def test_notifiche(tmp_path, monkeypatch=None):
    from pozzo import notify

    mp = monkeypatch or Patch()
    try:
        cfg = make_cfg(tmp_path)
        api = client(cfg)
        d = api.get("/api/app/notify").json()
        assert d["enabled"] is True and "available" in d, d
        sent = []
        mp.setattr(notify, "send", lambda *a, **k: sent.append(a) or True)
        assert api.post("/api/app/notify", json={"enabled": True}).json()["enabled"] is True
        assert sent, "accendendo le notifiche deve arrivare un avviso di conferma"
        assert api.post("/api/app/notify", json={"enabled": False}).json()["enabled"] is False
    finally:
        mp.undo()
    assert notify.enabled(cfg) is False
    assert notify.send(cfg, "x", "y") is False
    print("ok  notifiche: interruttore salvato e rispettato da notify.send")


def test_avvisi_giochi_gratis_e_port(tmp_path, monkeypatch=None):
    """Gli avvisi in sottofondo: una volta per offerta e per versione, mai ripetuti a ogni controllo."""
    from pozzo import freegames, notify, ports

    mp = monkeypatch or Patch()
    try:
        cfg = make_cfg(tmp_path)
        api = client(cfg)  # l'app crea gli avvisi (app.state.alerts)
        sent = []
        acts = []
        mp.setattr(notify, "send", lambda *a, **k: sent.append(a[1:3]) or acts.append(a[3:]) or True)
        d = {"items": [{"source": "steam", "console": "", "title": "Gioco Gratis", "url": "u"}]}
        mp.setattr(freegames, "get", lambda cache, force=False, log=print: d)
        config.set_setting(cfg, "free_state", {"seen": [], "claimed": []})  # la pagina dei giochi gratis e' gia' stata vista
        api.app.state.alerts["free"]()
        assert sent and "Gioco Gratis" in sent[0][1], sent
        sent.clear()
        api.app.state.alerts["free"]()  # stessa offerta: nessun secondo avviso
        assert sent == [], sent
        d["items"].append({"source": "epic", "console": "", "title": "Altro Regalo", "url": "v"})
        api.app.state.alerts["free"]()
        assert sent and "Altro Regalo" in sent[0][1], sent

        sent.clear()
        mp.setattr(ports, "all_installed", lambda cfg_: {"snow": {"name": "Snow", "version": "1.0",
                                                                 "repository": "o/r", "folder": "Snow"}})
        mp.setattr(ports, "update_info", lambda cache, man, tok="", force=False: {"latest": "2.0", "update": True})
        api.app.state.alerts["ports"]()
        assert sent and "Snow" in sent[0][1] and "2.0" in sent[0][1], sent
        assert acts[-1] == ({"view": "ports", "port": "snow"},), acts  # il clic apre la pagina del port
        sent.clear()
        api.app.state.alerts["ports"]()  # stessa versione: nessun secondo avviso
        assert sent == [], sent
    finally:
        mp.undo()
    print("ok  avvisi: giochi gratis e port aggiornati, una volta sola per offerta/versione")


if __name__ == "__main__":
    for fn in (test_pwa_installabile, test_certificato_firmato_dalla_ca, test_libreria_pronta_in_cache,
               test_pad_salvato_sul_pc, test_ca_installata_nasconde_il_qr, test_telecomando, test_prova_controller,
               test_power_a_fine_lavori,
               test_notifiche, test_avvisi_giochi_gratis_e_port):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print("TUTTI I TEST OK")
