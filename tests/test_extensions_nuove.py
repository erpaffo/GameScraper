"""Estensioni di extensions/: stasera, anniversari, pulizia, diario, limite-tempo, obiettivi, riepilogo-annuale,
steamgriddb, calendario-uscite. Database temporanei veri (libreria e profilo utente), il backend richiamato come fa
l'app, la logica JavaScript provata con node (se c'e').

Esegui: python tests/test_extensions_nuove.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from gamescraper import config as C  # noqa: E402
from gamescraper.library import build  # noqa: E402
from gamescraper.library.userdata import UserData  # noqa: E402

EXT = ROOT / "extensions"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"ext_{name.replace('-', '_')}", EXT / name / "backend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeGS:
    """Sostituto di PluginAPI: raccoglie endpoint, eventi e fonti invece di toccare FastAPI."""

    def __init__(self, cfg, pid):
        self.cfg, self.id, self.routes, self.events, self.sources, self.logs = cfg, pid, {}, {}, [], []
        self.settings = {}
        self.log = self.logs.append

    def route(self, path, methods=("GET",)):
        def deco(fn):
            self.routes[path.lstrip("/")] = fn
            return fn
        return deco

    def on(self, ev, fn):
        self.events.setdefault(ev, []).append(fn)

    def add_source(self, cls):
        self.sources.append(cls)

    def save_settings(self, values):  # come PluginAPI: in memoria e su settings.json
        self.settings.update(values)
        C.save_settings(self.cfg, plugins={self.id: dict(self.settings)})


def make_env(t: Path):
    """cfg + library.db + profilo: 4 giochi, 2 sessioni, uno finito."""
    cfg = C.Config(cache=t / "cache")
    cfg.config_path = t / "config.toml"
    cfg.app = {}
    cfg.cache.mkdir(parents=True, exist_ok=True)
    lib = sqlite3.connect(cfg.cache / "library.db")
    lib.executescript(build.SCHEMA)
    rows = [
        (1, "zelda", "n64", "Zelda Ocarina of Time", "1998-11-21", "Action, Adventure", "zelda", 1, None),
        (2, "mario", "n64", "Super Mario 64", "1996-06-23", "Platformer", "mario", 1, None),
        (3, "hk", "windows", "Hollow Knight", "2017-02-24", "Action, Platformer", "hk", 1, "steam"),
        (4, "online", "psx", "Gioco Online", "1996-06-23", "", "online", 1, "online"),
    ]
    for gid, key, plat, title, rel, gen, grp, prim, store in rows:
        lib.execute("INSERT INTO games(id, key, platform, title, sort_title, release_date, genres, grp, is_primary, store, found) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,1)", (gid, key, plat, title, title.lower(), rel, gen, grp, prim, store))
    lib.commit()
    lib.close()
    from gamescraper.sync import profile_path
    u = UserData(profile_path(cfg, "default"))
    return cfg, u


def js(path: str, code: str) -> str | None:
    """Esegue `code` (modulo ES) con node, importando `path`. None se node non c'e'."""
    node = shutil.which("node")
    if not node:
        return None
    src = f"import * as m from {json.dumps((ROOT / path).as_uri())};\n{code}"
    # file .mjs e stdin chiuso: `node -e` con stdin ereditato resta appeso su alcuni runner (macOS della CI)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        f = Path(d) / "prova.mjs"
        f.write_text(src, encoding="utf-8")
        r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


# ---------------------------------------------------------------------------------------------------- JavaScript

def test_stasera_logica():
    out = js("extensions/stasera/logica.js", """
const g = (id, o = {}) => ({ id, title: "G" + id, genres: "Azione, RPG", platform: "n64", store: null, installed: 1, hltb: 5, ...o,
  grp: { primary: id, hidden: 0, status: "", last: 0, ...(o.grp || {}) } });
const L = [g(1), g(2, { hltb: 20 }), g(3, { hltb: null }), g(4, { grp: { status: "done" } }), g(5, { grp: { status: "backlog" }, platform: "snes" }),
  g(6, { store: "online" }), g(7, { store: "steam", installed: 0 }), g(8, { grp: { hidden: 1 } }), g(9, { genres: "Puzzle" })];
const ids = (l) => l.map((x) => x.id).sort().join(",");
const r = {};
r.base = ids(m.candidates(L));                                   // niente finiti, online, non installati, nascosti
r.ore = ids(m.candidates(L, { hours: 6 }));                      // hltb <= 6, senza durata nota esclusi
r.ignote = ids(m.candidates(L, { hours: 6, unknown: true }));
r.genere = ids(m.candidates(L, { genre: "Puzzle" }));
r.console = ids(m.candidates(L, { platform: "snes" }));
r.generi = m.genresOf(L).join("|");
// pesi: un gioco mai avviato pesa piu' di uno giocato oggi
const now = 1e9;
r.pesi = [m.weight({ grp: { last: now } }, now), m.weight({ grp: { last: 0 } }, now)].map((x) => Math.round(x * 10) / 10).join(",");
// pick: deterministico con rnd; salta gli id gia' visti; null se non resta niente
const pool = [g(1), g(2)];
r.p0 = m.pick(pool, new Set(), () => 0).id;
r.p1 = m.pick(pool, new Set([1]), () => 0.5).id;
r.pn = String(m.pick(pool, new Set([1, 2])));
// distribuzione: con gli stessi pesi il mai giocato esce di piu'
const a = g(1, { grp: { last: now } }), b = g(2);
let nb = 0; for (let i = 0; i < 2000; i++) if (m.pick([a, b], new Set(), Math.random, now).id === 2) nb++;
r.biased = nb > 1500;
console.log(JSON.stringify(r));
""")
    if out is None:
        return print("--  stasera: node non trovato, logica JavaScript non provata")
    r = json.loads(out)
    assert r["base"] == "1,2,3,5,9", r["base"]
    assert r["ore"] == "1,5,9", r["ore"]
    assert r["ignote"] == "1,3,5,9", r["ignote"]
    assert r["genere"] == "9" and r["console"] == "5", r
    assert r["generi"].split("|")[:2] == ["Azione", "RPG"], r["generi"]
    assert r["pesi"] == "1,25.3", r["pesi"]
    assert (r["p0"], r["p1"], r["pn"], r["biased"]) == (1, 2, "null", True), r
    print("ok  stasera: filtri (tempo, genere, console, stato), pesi, estrazione")


def test_pulizia_logica():
    out = js("extensions/pulizia/logica.js", """
const g = (id, o = {}) => ({ id, title: "G" + id, platform: "n64", store: null, found: 1, cover: 1, year: 1998, genres: "x", splay: 0, ...o,
  grp: { primary: id, hidden: 0, status: "", last: 0, playtime: 0, ...(o.grp || {}) } });
const now = 1e9, Y = 365 * 86400;
const L = [g(1, { title: "Super Mario" }), g(2, { title: "super  MARIO!" }), g(3, { title: "Super Mario", platform: "snes" }),
  g(4, { grp: { last: now - 3 * Y, playtime: 100 } }), g(5, { grp: { last: now - 0.5 * Y } }), g(6, { found: 0, cover: 0 }),
  g(7, { store: "online" }), g(8, { grp: { hidden: 1 } }), g(9, { grp: { status: "done", last: now - 5 * Y } })];
const ids = (l) => l.map((x) => x.id).sort().join(",");
console.log(JSON.stringify({
  dup: m.duplicates(L).map(ids),
  mai: ids(m.neverPlayed(L)),
  fermi: ids(m.stale(L, 2, now)),
  inc: m.incomplete(L).map((x) => x.g.id + ":" + x.m.join("+")),
}));
""")
    if out is None:
        return print("--  pulizia: node non trovato, logica JavaScript non provata")
    r = json.loads(out)
    assert r["dup"] == ["1,2"], r["dup"]                   # stessa console, titolo uguale a meno di maiuscole e segni; non quello su snes
    assert r["mai"] == "1,2,3,6", r["mai"]                  # niente online, nascosti, giocati, finiti
    assert r["fermi"] == "4", r["fermi"]                    # giocato da 3 anni; 5 da pochi mesi; 9 e' finito
    assert r["inc"] == ["6:non trovato+copertina"], r["inc"]
    print("ok  pulizia: duplicati, mai avviati, fermi da anni, dati incompleti")


# ---------------------------------------------------------------------------------------------------- Python

def test_anniversari(t):
    cfg, u = make_env(t)
    gs = FakeGS(cfg, "anniversari")
    mod = load("anniversari")
    mod.register(gs)
    r = gs.routes["oggi"](giorno="06-23")
    assert [g["titolo"] for g in r["giochi"]] == ["Super Mario 64"], r          # l'online del 23/06 e' escluso
    assert r["giochi"][0]["anni"] == r["anno"] - 1996
    assert gs.routes["oggi"](giorno="02-29")["giochi"] == []
    try:
        gs.routes["oggi"](giorno="oggi")
    except HTTPException as e:
        assert e.status_code == 400
    else:
        raise AssertionError("giorno non valido accettato")
    # senza libreria costruita non esplode
    shutil.rmtree(cfg.cache / "library.db", ignore_errors=True) if (cfg.cache / "library.db").is_dir() else (cfg.cache / "library.db").unlink()
    assert gs.routes["oggi"](giorno="06-23")["giochi"] == []
    u.close()
    print("ok  anniversari: giochi usciti oggi, giorno non valido, libreria assente")


def test_diario(t):
    cfg, u = make_env(t)
    u.record_play("zelda", 1800)
    u.record_play("zelda", 600)
    u.record_play("mario", 99)
    gs = FakeGS(cfg, "diario")
    load("diario").register(gs)
    assert gs.routes["gioco"](id=1)["voci"] == []
    a = gs.routes["aggiungi"]({"id": 1, "testo": "  Tempio dell'acqua  "})
    gs.routes["aggiungi"]({"id": 1, "testo": "password: 4242"})
    d = gs.routes["gioco"](id=1)
    assert [v["testo"] for v in d["voci"]] == ["password: 4242", "Tempio dell'acqua"], d  # dalla piu' recente
    assert d["partite"] == 2 and d["totale"] == 2400 and len(d["sessioni"]) == 2          # solo le sessioni di quel gioco
    assert gs.routes["gioco"](id=2)["voci"] == []                                         # il diario e' per gioco
    for bad in ({"id": 1, "testo": "   "}, {"id": 999, "testo": "x"}):
        try:
            gs.routes["aggiungi"](bad)
        except HTTPException as e:
            assert e.status_code in (400, 404)
        else:
            raise AssertionError(f"accettato: {bad}")
    gs.routes["elimina"]({"id": 1, "t": a["t"]})
    assert [v["testo"] for v in gs.routes["gioco"](id=1)["voci"]] == ["password: 4242"]
    try:
        gs.routes["elimina"]({"id": 1, "t": a["t"]})
    except HTTPException as e:
        assert e.status_code == 404
    else:
        raise AssertionError("eliminata due volte")
    u.close()
    print("ok  diario: voci per gioco, sessioni, validazione, eliminazione")


def test_limite_tempo(t):
    cfg, u = make_env(t)
    mod = load("limite-tempo")
    # un mercoledi' a mezzogiorno: la settimana comincia lunedi' 00:00
    now = datetime(2026, 3, 11, 12, 0).timestamp()
    oggi0, sett0 = mod.inizi(now)
    assert datetime.fromtimestamp(oggi0) == datetime(2026, 3, 11) and datetime.fromtimestamp(sett0) == datetime(2026, 3, 9)
    ins = lambda start, sec: u.db.execute("INSERT INTO play_sessions VALUES('zelda', ?, ?)", (start, sec))  # noqa: E731
    ins(datetime(2026, 3, 11, 9, 0).timestamp(), 3600)          # oggi 1 h
    ins(datetime(2026, 3, 10, 20, 0).timestamp(), 7200)         # ieri 2 h
    ins(datetime(2026, 3, 10, 23, 0).timestamp(), 7200)         # a cavallo della mezzanotte: 1 h ieri + 1 h oggi
    ins(datetime(2026, 3, 8, 22, 0).timestamp(), 3600)          # domenica scorsa: fuori settimana
    u.db.commit()
    db = sqlite3.connect(u.path)
    oggi, sett = mod.usato(db, now)
    assert (oggi, sett) == (2 * 3600, 5 * 3600), (oggi, sett)   # settimana: 1 h oggi + 2 h ieri + 2 h a cavallo
    # partita in corso da mezz'ora (non e' ancora in play_sessions)
    oggi, sett = mod.usato(db, now, now - 1800)
    assert (oggi, sett) == (2.5 * 3600, 5.5 * 3600)
    # una partita iniziata ieri alle 23 e ancora in corso conta per oggi solo da mezzanotte: 12 h, oltre alle 2 h gia' registrate
    oggi, _ = mod.usato(db, now, datetime(2026, 3, 10, 23, 0).timestamp())
    assert oggi == 14 * 3600, oggi
    db.close()
    assert mod.superati({"giorno_ore": 2, "settimana_ore": 0}, 7200, 0) != []
    assert mod.superati({"giorno_ore": 2}, 7199, 0) == [] and mod.superati({}, 1e9, 1e9) == []
    assert len(mod.superati({"giorno_ore": 1, "settimana_ore": 5}, 3600, 6 * 3600)) == 2

    # backend: salva/stato, validazione, e il controllo parte e si ferma con gli eventi
    gs = FakeGS(cfg, "limite-tempo")
    mod.TICK = 0.05
    mod.RIPETI = 3600
    mod.register(gs)
    for bad in ({"giorno_ore": 30}, {"giorno_ore": "x"}, {"settimana_ore": -1}):
        try:
            gs.routes["salva"](bad)
        except HTTPException as e:
            assert e.status_code == 400
        else:
            raise AssertionError(f"accettato: {bad}")
    s = gs.routes["salva"]({"giorno_ore": 0.0001, "settimana_ore": 0, "attivo": True})   # limite di ~0,4 s: subito superato
    assert s["attivo"] and s["giorno_ore"] == 0.0001
    sent = []
    import gamescraper.notify as N
    N.send = lambda cfg_, title, body="": sent.append((title, body)) or True
    gs.events["game_started"][0]({"title": "Zelda"})
    time.sleep(0.6)
    assert sent and "Zelda" in sent[0][1] and "Limite" in sent[0][0], sent
    assert len(sent) == 1, "RIPETI: un solo avviso finche' non passa il tempo di ripetizione"
    assert gs.routes["stato"]()["in_corso"] is True
    gs.events["game_stopped"][0]({})
    assert gs.routes["stato"]()["in_corso"] is False
    # avvisi spenti: nessuna notifica
    gs.routes["salva"]({"giorno_ore": 0.0001, "attivo": False})
    sent.clear()
    gs.events["game_started"][0]({"title": "Zelda"})
    time.sleep(0.3)
    assert not sent and gs.routes["stato"]()["in_corso"] is False
    u.close()
    print("ok  limite-tempo: periodi, mezzanotte, partita in corso, avvisi, validazione")


def test_obiettivi(t):
    mod = load("obiettivi")
    d = date
    assert mod.serie({d(2026, 3, 9), d(2026, 3, 10), d(2026, 3, 11)}, d(2026, 3, 11)) == (3, 3)
    assert mod.serie({d(2026, 3, 9), d(2026, 3, 10)}, d(2026, 3, 11)) == (2, 2)          # oggi non ancora, ma ieri si': la serie vive
    assert mod.serie({d(2026, 3, 1), d(2026, 3, 2), d(2026, 3, 3), d(2026, 3, 10)}, d(2026, 3, 11)) == (1, 3)
    assert mod.serie({d(2026, 3, 1)}, d(2026, 3, 11)) == (0, 1) and mod.serie(set(), d(2026, 3, 11)) == (0, 0)
    mid = datetime(2026, 3, 10, 23, 0).timestamp()
    assert mod.giorni_di_gioco([(mid, 7200)]) == {d(2026, 3, 10), d(2026, 3, 11)}      # a cavallo della mezzanotte

    cfg, u = make_env(t)
    now = time.time()
    u.record_play("zelda", 3600)
    u.record_play("hk", 7200)
    for k in ("zelda", "hk", "mario"):                     # tre giochi finiti: due versioni dello stesso non esistono qui, ma mario ha 0 partite
        u.set_flag(k, "status", "done")
    gs = FakeGS(cfg, "obiettivi")
    mod.register(gs)
    r = gs.routes["salva"]({"obiettivi": [{"tipo": "finiti", "n": 3, "periodo": "anno"}, {"tipo": "ore", "n": 2, "periodo": "settimana"},
                                          {"tipo": "giorni", "n": 5, "periodo": "mese"}]})
    by = {o["tipo"]: o for o in r["obiettivi"]}
    assert by["finiti"]["valore"] == 3 and by["finiti"]["fatto"], by
    assert by["ore"]["valore"] == 3.0 and by["ore"]["fatto"], by
    assert by["giorni"]["valore"] == 1 and not by["giorni"]["fatto"], by
    assert r["serie"]["attuale"] == 1
    bd = {b["id"]: b for b in r["distintivi"]}
    assert bd["f1"]["ottenuto"] and not bd["f10"]["ottenuto"] and not bd["o100"]["ottenuto"] and not bd["s7"]["ottenuto"]
    for bad in ([{"tipo": "boh", "n": 1, "periodo": "anno"}], [{"tipo": "ore", "n": 0, "periodo": "anno"}],
                [{"tipo": "ore", "n": "x", "periodo": "anno"}], [{"tipo": "ore", "n": 1, "periodo": "secolo"}],
                [{"tipo": "ore", "n": 1, "periodo": "anno"}] * 21):
        try:
            gs.routes["salva"]({"obiettivi": bad})
        except HTTPException as e:
            assert e.status_code == 400
        else:
            raise AssertionError(f"accettato: {bad[:1]}")
    assert len(gs.routes["stato"]()["obiettivi"]) == 3          # i rifiuti non hanno toccato quelli salvati
    assert gs.routes["salva"]({"obiettivi": []})["obiettivi"] == []
    u.close()
    print("ok  obiettivi: serie, mezzanotte, avanzamento (finiti/ore/giorni), distintivi, validazione")


def test_riepilogo(t):
    mod = load("riepilogo-annuale")
    ts = lambda m, dd, h: datetime(2025, m, dd, h).timestamp()  # noqa: E731
    sess = [("a", ts(3, 3, 10), 3600), ("a2", ts(3, 4, 10), 7200), ("b", ts(7, 5, 21), 1800)]
    info = {"a": ("ga", "Gioco A", "N64"), "a2": ("ga", "Gioco A", "PC"), "b": ("gb", "Gioco B", "PC")}
    r = mod.riepilogo(sess, info, 2, 2025)
    assert r["ore"] == 3.5 and r["partite"] == 3 and r["giorni"] == 3 and r["giochi"] == 2 and r["finiti"] == 2
    assert r["top"][0]["titolo"] == "Gioco A" and r["top"][0]["ore"] == 3.0 and r["top"][0]["partite"] == 2   # due versioni = un gioco
    assert r["console"] == "PC" and r["mese"] == "marzo" and r["sessione_piu_lunga"] == {"ore": 2.0, "titolo": "Gioco A"}
    assert r["giorno_settimana"] in ("lunedì", "martedì") and sum(r["per_mese"]) == 3.5
    vuoto = mod.riepilogo([], {}, 0, 2025)
    assert vuoto["ore"] == 0 and vuoto["top"] == [] and vuoto["mese"] is None and vuoto["console"] is None

    cfg, u = make_env(t)
    u.db.execute("INSERT INTO play_sessions VALUES('zelda', ?, 3600)", (ts(5, 5, 10),))
    u.db.execute("INSERT INTO play_sessions VALUES('mario', ?, 600)", (datetime(2024, 12, 31, 23).timestamp(),))
    u.db.commit()
    gs = FakeGS(cfg, "riepilogo-annuale")
    mod.register(gs)
    d = gs.routes["anno"](anno=0)
    assert d["anni"] == [2025, 2024] and d["anno"] == 2025 and d["ore"] == 1.0 and d["top"][0]["titolo"] == "Zelda Ocarina of Time"
    assert gs.routes["anno"](anno=2024)["ore"] == 0.2
    try:
        gs.routes["anno"](anno=1800)
    except HTTPException as e:
        assert e.status_code == 400
    else:
        raise AssertionError("anno 1800 accettato")
    u.close()
    print("ok  riepilogo-annuale: aggregazione, versioni unite, anni, anno vuoto")


def test_steamgriddb(t):
    import httpx
    cfg, u = make_env(t)
    u.close()
    mod = load("steamgriddb")
    gs = FakeGS(cfg, "steamgriddb")
    mod.register(gs)
    assert gs.routes["stato"]() == {"chiave": False}
    assert gs.routes["chiave"]({"chiave": " abc "}) == {"chiave": True}
    assert gs.settings["chiave"] == "abc"
    assert mod.best_url([{"url": "http://x/a.png", "score": 99}, {"url": "https://x/b.png", "score": 1}, {"url": "https://x/c.png", "score": 5}]) == "https://x/c.png"
    assert mod.best_url([{"url": ""}]) is None and mod.best_url([]) is None

    calls = []

    def handler(req: httpx.Request):
        calls.append((req.url.path, req.headers.get("authorization")))
        p = req.url.path
        if "search/autocomplete" in p:
            return httpx.Response(200, json={"success": True, "data": [{"id": 7, "name": "Hollow Knight"}, {"id": 8, "name": "Altro"}]})
        if "/grids/game/7" in p:
            return httpx.Response(200, json={"success": True, "data": [{"url": "https://cdn/g1.png", "score": 3}]})
        if "/heroes/game/7" in p:
            return httpx.Response(200, json={"success": True, "data": []})
        if "/logos/game/7" in p:
            return httpx.Response(200, json={"success": True, "data": [{"url": "https://cdn/l1.png", "score": 1}]})
        return httpx.Response(404)

    class G:
        search_name = "Hollow Knight"

        def __init__(self, have=()):
            self.have = set(have)

        def missing(self, wanted):
            return {w for w in wanted if w not in self.have}

    async def run():
        from gamescraper.sources.base import Context
        cfg.media = ["cover", "fanart", "wheel"]
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        src = gs.sources[0](Context(cfg=cfg, cache=None, client=client, log=gs.log))
        assert src.name == "steamgriddb" and src.key == "abc"
        assert await src.setup(set()) is True
        res = await src.lookup(G())
        assert res.media == {"cover": "https://cdn/g1.png", "wheel": "https://cdn/l1.png"}, res.media    # niente hero: non c'e'
        assert all(a == "Bearer abc" for _, a in calls)
        n = len(calls)
        # gia' completo: nessuna richiesta
        assert await src.lookup(G(have={"media:cover", "media:fanart", "media:wheel"})) is None and len(calls) == n
        # solo cio' che manca e che la configurazione chiede
        cfg.media = ["cover"]
        calls.clear()
        res = await src.lookup(G())
        assert list(res.media) == ["cover"] and not any("logos" in p or "heroes" in p for p, _ in calls)
        # titolo che non corrisponde: niente
        class H(G):
            search_name = "Zzzz Qqqq Totally Different"
        assert await src.lookup(H()) is None
        # senza chiave la fonte si disattiva
        gs.routes["chiave"]({"chiave": ""})
        src2 = gs.sources[0](Context(cfg=cfg, cache=None, client=client, log=gs.log))
        assert src2.key == "" and await src2.setup(set()) is False and any("chiave" in m for m in gs.logs)
        await client.aclose()
    asyncio.run(run())
    print("ok  steamgriddb: ricerca, scelta del piu' votato, solo i media mancanti, chiave")


def test_calendario(t):
    mod = load("calendario-uscite")
    oggi = date(2026, 3, 11)
    assert mod.fold("Résident  Évil: 4!") == "resident evil 4"
    cfg, u = make_env(t)
    db = sqlite3.connect(cfg.cache / "library.db")
    # Action 2 giochi, Platformer 2, Adventure 1 (il gioco «online» non conta)
    assert mod.generi_preferiti(db) == ["action", "platformer", "adventure"]
    assert mod.generi_preferiti(db, 2) == ["action", "platformer"]
    db.close()

    pagine = {"search": {"results": [{"name": "Hades II", "released": "2026-06-01", "platforms": [{"platform": {"name": "PC"}}], "genres": []}]}}
    visti = []

    def get(url, params):
        visti.append(params)
        if "search" in params:
            q = params["search"]
            if q == "Hades II":
                return pagine["search"]
            if q == "Gia Uscito":
                return {"results": [{"name": "Gia Uscito", "released": "2020-01-01"}]}
            if q == "Omonimo":
                return {"results": [{"name": "Altro Titolo", "released": "2026-05-01"}]}
            return {"results": []}
        return {"results": [
            {"name": "B", "released": "2026-09-01", "genres": [{"name": "Action"}], "background_image": "https://i/b.jpg"},
            {"name": "A", "released": "2026-04-02", "platforms": [{"platform": {"name": "PC"}}, {"platform": None}]},
            {"name": "Senza data", "released": None},
            {"name": "Data rotta", "released": "2026"}]}

    d = mod.desideri(get, "k", ["Hades II", "Gia Uscito", "Omonimo", "Sconosciuto"], oggi)
    assert [v["titolo"] for v in d] == ["Hades II"] and d[0]["giorni"] == (date(2026, 6, 1) - oggi).days
    visti.clear()
    p = mod.prossime(get, "k", oggi, 180, ["action", "rpg"])
    assert [v["titolo"] for v in p] == ["A", "B"] and p[0]["piattaforme"] == ["PC"] and p[1]["generi"] == ["Action"]
    assert visti[0]["genres"] == "action,rpg" and visti[0]["dates"] == "2026-03-11,2026-09-07"
    visti.clear()
    mod.prossime(get, "k", oggi, 30, [])
    assert len(visti) == 1 and "genres" not in visti[0]            # senza generi nessun filtro
    # MAX_DESIDERI: una richiesta a titolo, mai di piu'
    visti.clear()
    mod.desideri(get, "k", [f"T{i}" for i in range(100)], oggi)
    assert len(visti) == mod.MAX_DESIDERI

    gs = FakeGS(cfg, "calendario-uscite")
    mod.register(gs)
    try:
        gs.routes["uscite"](giorni=180)
    except HTTPException as e:
        assert e.status_code == 400 and "RAWG" in e.detail
    else:
        raise AssertionError("senza chiave doveva rifiutare")
    cfg.rawg = {"api_key": "k"}
    try:
        gs.routes["uscite"](giorni=1)
    except HTTPException as e:
        assert e.status_code == 400
    else:
        raise AssertionError("intervallo non valido accettato")
    u.close()
    print("ok  calendario-uscite: desideri (solo nome esatto e data futura), prossime per genere, tetto di richieste, chiave")


def main():
    test_stasera_logica()
    test_pulizia_logica()
    for fn in (test_anniversari, test_diario, test_limite_tempo, test_obiettivi, test_riepilogo, test_steamgriddb, test_calendario):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print("test_extensions_nuove: OK")


if __name__ == "__main__":
    main()
