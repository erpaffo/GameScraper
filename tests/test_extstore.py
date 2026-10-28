"""Store delle estensioni: indice aggiornato, installazione verificata (sha256, nomi, versione minima), rimozione,
copia di sviluppo intoccabile, store sempre primo e sempre attivo.

Esegui: python tests/test_extstore.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from pozzo import config as C  # noqa: E402
from pozzo.app import extensions as X  # noqa: E402
from pozzo.app import extstore as S  # noqa: E402
import extensions as T  # noqa: E402  (tools/extensions.py)


def cfg_in(t: Path, index: Path) -> C.Config:
    cfg = C.Config(cache=t / "cache")
    cfg.config_path = t / "config.toml"
    cfg.app = {"extensions_index": str(index)}
    return cfg


def expect(fn, text: str):
    try:
        fn()
    except ValueError as e:
        assert text in str(e), e
        return
    raise AssertionError(f"doveva rifiutare: {text}")


def main():
    # l'indice nel repository corrisponde ai file: chi modifica un'estensione deve rigenerarlo (tools/extensions.py index)
    assert T.render(T.build_index()) == T.INDEX.read_text(encoding="utf-8"), "extensions/index.json non aggiornato"
    print("ok  extensions/index.json aggiornato")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        t = Path(t)
        repo = t / "repo"
        shutil.copytree(T.EXT, repo)
        cfg = cfg_in(t, repo / "index.json")
        plugins = X.user_dir(cfg, "plugins")

        ids = [p["id"] for p in X.list_plugins(cfg)]
        assert ids == [S.STORE_ID], ids  # nell'app c'e' solo lo store
        cat = {x["id"]: x for x in S.catalog(cfg)["items"]}
        assert set(cat) == {d.name for d in T.EXT.iterdir() if (d / "plugin.toml").exists()}      # tutte quelle del repository
        assert {"linkstores", "minerva", "statistiche"} <= set(cat) and cat["statistiche"]["state"] == "available"

        r = S.install(cfg, "statistiche")
        assert r["ok"] and (plugins / "statistiche" / "backend.py").is_file()
        ps = X.list_plugins(cfg)
        assert [p["id"] for p in ps] == [S.STORE_ID, "statistiche"] and ps[0]["locked"] and ps[1]["enabled"]
        assert {x["id"]: x for x in S.catalog(cfg)["items"]}["statistiche"]["state"] == "installed"
        print("ok  installazione da indice locale, store per primo")

        # aggiornamento: versione piu' alta nell'indice -> «update», reinstallando si sostituisce la cartella
        idx = json.loads((repo / "index.json").read_text(encoding="utf-8"))
        st = next(e for e in idx["extensions"] if e["id"] == "statistiche")
        st["version"] = "9.0"
        (repo / "index.json").write_text(json.dumps(idx), encoding="utf-8")
        assert {x["id"]: x for x in S.catalog(cfg)["items"]}["statistiche"]["state"] == "update"
        (plugins / "statistiche" / "vecchio.txt").write_text("x")
        S.install(cfg, "statistiche")
        assert not (plugins / "statistiche" / "vecchio.txt").exists()
        assert not [d for d in plugins.iterdir() if d.name.startswith(".")]  # niente .tmp/.old lasciati
        print("ok  aggiornamento")

        # contenuto diverso dall'indice: rifiutato e niente installato
        (repo / "minerva" / "backend.py").write_text("import os  # manomesso\n")
        expect(lambda: S.install(cfg, "minerva"), "sha256")
        assert not (plugins / "minerva").exists()
        # nomi di file con percorsi, versione minima, id sconosciuto, store stesso
        mv = next(e for e in idx["extensions"] if e["id"] == "minerva")
        mv["files"]["../fuori.py"] = mv["files"]["backend.py"]
        lk = next(e for e in idx["extensions"] if e["id"] == "linkstores")
        lk["min_app"] = "99.0.0"
        idx["extensions"].append({"id": S.STORE_ID, "files": {"plugin.toml": "0"}})
        (repo / "index.json").write_text(json.dumps(idx), encoding="utf-8")
        expect(lambda: S.install(cfg, "minerva"), "nomi di file")
        expect(lambda: S.install(cfg, "linkstores"), "richiede PozzoLauncher 99.0.0")
        assert {x["id"]: x for x in S.catalog(cfg)["items"]}["linkstores"]["state"] == "incompatible"
        expect(lambda: S.install(cfg, S.STORE_ID), "non trovata")
        expect(lambda: S.remove(cfg, S.STORE_ID), "non si puo' rimuovere")
        assert not (t / "fuori.py").exists() and not (plugins.parent / "fuori.py").exists()
        print("ok  rifiutati: sha256 sbagliato, percorsi nei nomi, versione minima, store")

        S.remove(cfg, "statistiche")
        assert not (plugins / "statistiche").exists()
        expect(lambda: S.remove(cfg, "statistiche"), "non installata")

        # copia di sviluppo collegata: lo store non la aggiorna ne' la cancella (sarebbero i sorgenti)
        link = plugins / "linkstores"
        if sys.platform == "win32":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(repo / "linkstores")], check=True, capture_output=True)
        else:
            link.symlink_to(repo / "linkstores", target_is_directory=True)
        assert {x["id"]: x for x in S.catalog(cfg)["items"]}["linkstores"]["dev"] is True
        expect(lambda: S.remove(cfg, "linkstores"), "sviluppo")
        lk["min_app"] = "0"
        (repo / "index.json").write_text(json.dumps(idx), encoding="utf-8")
        expect(lambda: S.install(cfg, "linkstores"), "sviluppo")
        assert (repo / "linkstores" / "backend.py").is_file()
        link.rmdir() if sys.platform == "win32" else link.unlink()
        print("ok  copia di sviluppo collegata intoccabile")

        # uno «estensioni» messo a mano nella cartella utente non sostituisce lo store incluso
        (plugins / S.STORE_ID).mkdir()
        (plugins / S.STORE_ID / "plugin.toml").write_text('[plugin]\nname = "finto"\n')
        assert X.list_plugins(cfg)[0]["name"] == "Store estensioni"
        # disattivato in impostazioni vecchie: resta attivo
        cfg.app["disabled_plugins"] = [S.STORE_ID]
        assert X.list_plugins(cfg)[0]["enabled"]
        print("ok  store sempre quello incluso e sempre attivo")

        # cartella di un'estensione non attraversabile (WinError 448, junction non attendibile): l'app parte lo stesso,
        # l'estensione compare disattivata con il motivo, le altre restano
        cfg.app["disabled_plugins"] = []
        (plugins / "buona").mkdir()
        (plugins / "buona" / "plugin.toml").write_text('[plugin]\nname = "Buona"\n')
        (plugins / "rotta").mkdir()
        real = X._manifest

        def broken(d):
            if d.name == "rotta":
                raise OSError(448, "Impossibile attraversare il percorso perche' contiene un punto di montaggio non attendibile")
            return real(d)
        X._manifest = broken
        try:
            by = {p["id"]: p for p in X.list_plugins(cfg)}
            assert by["buona"]["enabled"] and not by["buona"]["error"]
            assert not by["rotta"]["enabled"] and "illeggibile" in by["rotta"]["error"] and by["rotta"]["backend"] is None
            assert X.load_backends(None, cfg, X.Events(), log=lambda m: None) is not None     # l'avvio non si ferma
        finally:
            X._manifest = real
        print("ok  cartella plugin illeggibile: l'app parte, l'estensione e' disattivata con il motivo")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_fonti(Path(t))

    # il backend dello store si carica e registra le sue route
    class GS:
        cfg, routes, log = None, {}, staticmethod(lambda m: None)

        def route(self, path, methods=("GET",)):
            return lambda fn: self.routes.setdefault(path, fn)
    import importlib.util
    spec = importlib.util.spec_from_file_location("gs_plugin_store_test", X.BUILTIN_PLUGINS / S.STORE_ID / "backend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    gs = GS()
    mod.register(gs)
    assert set(gs.routes) == {"catalogo", "installa", "rimuovi", "fonti/aggiungi", "fonti/rimuovi"}
    print("ok  backend dello store")
    print("test_extstore: OK")


def test_fonti(t: Path):
    """Repository separati: export, store che rimanda ai repository, fonti aggiunte con un link, priorita' e errori."""
    # link -> index.json
    assert S.index_link("https://github.com/u/r") == "https://raw.githubusercontent.com/u/r/HEAD/index.json"
    assert S.index_link("https://github.com/u/r.git/") == "https://raw.githubusercontent.com/u/r/HEAD/index.json"
    assert S.index_link("https://github.com/u/r/tree/v1.2") == "https://raw.githubusercontent.com/u/r/v1.2/index.json"
    assert S.index_link(" https://esempio.it/store/index.json ") == "https://esempio.it/store/index.json"
    for bad in ("http://github.com/u/r", "https://github.com/u", "C:/store/index.json", "https://esempio.it/pagina", "ftp://x/i.json"):
        expect(lambda b=bad: S.index_link(b), "link non valido")
    assert S.sources(C.Config())[0] == {"link": S.OFFICIAL, "official": True}   # lo store ufficiale e' sempre il primo

    # export: un repository per estensione (index.json con base "./") e lo store con i riferimenti ai tag
    out = t / "out"
    T.cmd_export(str(out), "utente")
    store = json.loads((out / T.STORE / "index.json").read_text(encoding="utf-8"))
    ids = sorted(d.name for d in T.extension_dirs(T.EXT))
    assert [r["repo"] for r in store["repos"]] == [f"utente/{T.PREFIX}{i}" for i in ids]
    assert all(r["ref"].startswith("v") for r in store["repos"])
    one = out / f"{T.PREFIX}statistiche"
    idx = json.loads((one / "index.json").read_text(encoding="utf-8"))["extensions"]
    assert len(idx) == 1 and idx[0]["id"] == "statistiche" and idx[0]["base"] == "./"
    assert {"README.md", "LICENSE", "index.json"}.isdisjoint(idx[0]["files"])      # file del repository, non dell'estensione
    assert (one / "README.md").exists() and (one / ".gitattributes").exists()
    # index <cartella> rigenera lo stesso indice dal repository separato (la cartella si chiama pozzo-ext-<id>)
    before = (one / "index.json").read_text(encoding="utf-8")
    T.cmd_index(str(one))
    assert (one / "index.json").read_text(encoding="utf-8") == before

    # store locale che rimanda ai repository con "url" (al posto di repo+ref, che richiederebbe GitHub)
    local = {"version": 1, "repos": [{"url": str(out / f"{T.PREFIX}{i}" / "index.json")} for i in ("stasera", "diario")]
             + [{"url": str(t / "manca" / "index.json")}, {"repo": "non valido!"}]}
    (t / "store.json").write_text(json.dumps(local), encoding="utf-8")
    cfg = cfg_in(t, t / "store.json")
    plugins = X.user_dir(cfg, "plugins")
    cat = S.catalog(cfg)
    assert sorted(x["id"] for x in cat["items"]) == ["diario", "stasera"], cat["items"]
    src = cat["sources"][0]
    assert src["official"] and src["count"] == 2 and len(src["errors"]) == 2      # un repository irraggiungibile non toglie gli altri
    S.install(cfg, "stasera")
    assert (plugins / "stasera" / "logica.js").is_file() and not (plugins / "stasera" / "README.md").exists()

    # seconda fonte (un altro store, letto da disco perche' l'utente non puo' aggiungere percorsi locali): stessa estensione
    # in entrambe -> vince lo store ufficiale; una nuova -> compare con la sua fonte
    other = {"version": 1, "repos": [{"url": str(out / f"{T.PREFIX}{i}" / "index.json")} for i in ("diario", "pulizia")]}
    (t / "altro.json").write_text(json.dumps(other), encoding="utf-8")
    cfg.app["extensions_sources"] = [str(t / "altro.json")]
    by = {x["id"]: x for x in S.catalog(cfg)["items"]}
    assert by["diario"]["official"] and not by["pulizia"]["official"] and by["pulizia"]["source"] == str(t / "altro.json")
    S.install(cfg, "pulizia", str(t / "altro.json"))
    assert (plugins / "pulizia" / "plugin.toml").is_file()
    expect(lambda: S.install(cfg, "pulizia", "https://github.com/sconosciuto/store"), "fonte sconosciuta")
    # sha256 manomesso nel repository di un'estensione: rifiutata anche se arriva da uno store
    (out / f"{T.PREFIX}diario" / "backend.py").write_text("import os\n", encoding="utf-8")
    expect(lambda: S.install(cfg, "diario"), "sha256")
    assert not (plugins / "diario").exists()

    # aggiungere e togliere fonti: solo link https validi e con estensioni; niente doppioni; l'ufficiale resta
    cfg.app["extensions_sources"] = []
    expect(lambda: S.add_source(cfg, "C:/store.json"), "link non valido")
    real_fetch = S.fetch_source
    S.fetch_source = lambda link: ([{"id": "x"}], []) if "buona" in link else ([], [])
    try:
        assert S.add_source(cfg, "https://github.com/utente/buona/") == ["https://github.com/utente/buona"]
        expect(lambda: S.add_source(cfg, "https://github.com/utente/buona"), "gia' presente")
        expect(lambda: S.add_source(cfg, "https://github.com/utente/vuota"), "non ci sono estensioni")
        saved = json.loads((t / "settings.json").read_text(encoding="utf-8"))
        assert saved["app"]["extensions_sources"] == ["https://github.com/utente/buona"]
        assert S.remove_source(cfg, "https://github.com/utente/buona") == []
        expect(lambda: S.remove_source(cfg, S.OFFICIAL), "non si rimuove")
    finally:
        S.fetch_source = real_fetch
    print("ok  repository separati: export, store con rimandi, fonti aggiunte da link, priorita' dello store ufficiale")


if __name__ == "__main__":
    main()
