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

from gamescraper import config as C  # noqa: E402
from gamescraper.app import extensions as X  # noqa: E402
from gamescraper.app import extstore as S  # noqa: E402
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
        assert set(cat) == {"linkstores", "minerva", "statistiche"} and cat["statistiche"]["state"] == "available"

        r = S.install(cfg, "statistiche")
        assert r["ok"] and (plugins / "statistiche" / "backend.py").is_file()
        ps = X.list_plugins(cfg)
        assert [p["id"] for p in ps] == [S.STORE_ID, "statistiche"] and ps[0]["locked"] and ps[1]["enabled"]
        assert S.catalog(cfg)["items"][2]["state"] == "installed"
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
        expect(lambda: S.install(cfg, "linkstores"), "richiede GameScraper 99.0.0")
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

    # il backend dello store si carica e registra le sue tre route
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
    assert set(gs.routes) == {"catalogo", "installa", "rimuovi"}
    print("ok  backend dello store")
    print("test_extstore: OK")


if __name__ == "__main__":
    main()
