"""Estensioni: indici per lo store, collegamento alla copia di sviluppo, repository separati. Istruzioni in docs/ESTENSIONI.md.

  python tools/extensions.py index                 # rigenera extensions/index.json (sha256 di ogni file)
  python tools/extensions.py index <cartella>      # index.json del repository di UNA estensione (base "./")
  python tools/extensions.py install [<cartella>]  # copia le estensioni nella cartella plugin del tuo config (da rifare dopo
                                                   # ogni modifica; "link" e' un alias). Niente junction: l'app installata
                                                   # non le attraversa se puntano dentro OneDrive (WinError 448)
  python tools/extensions.py export <cartella> [utente]
        # un repository per estensione (pozzo-ext-<id>) e quello dello store (pozzo-extensions),
        # pronti per git init / push; lo store punta a utente/pozzo-ext-<id> al tag v<versione>
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extensions"
INDEX = EXT / "index.json"
SKIP = {"__pycache__", ".pytest_cache"}
NOT_PUBLISHED = {"index.json", "README.md", "LICENSE"}  # file del repository, non dell'estensione installata
PREFIX, STORE = "pozzo-ext-", "pozzo-extensions"
GITATTRIBUTES = "# lo store verifica lo sha256 dei file serviti da GitHub raw: in locale devono essere identici (LF)\n" \
                "* text=auto eol=lf\n*.png binary\n*.jpg binary\n*.webp binary\n*.woff2 binary\n"


def files_of(d: Path) -> list[Path]:
    """File pubblicati di un'estensione: solo quelli in cima alla cartella (lo store non accetta sottocartelle)."""
    return sorted(f for f in d.iterdir() if f.is_file() and not f.name.startswith(".") and f.suffix != ".pyc"
                  and f.name not in NOT_PUBLISHED)


TEXT = {".py", ".js", ".css", ".toml", ".json", ".md"}


def sha(f: Path) -> str:
    """sha256 del file come lo salva git (testo con LF, vedi .gitattributes): e' quello servito da GitHub raw."""
    b = f.read_bytes()
    return hashlib.sha256(b.replace(b"\r\n", b"\n") if f.suffix in TEXT else b).hexdigest()


def manifest(d: Path) -> dict:
    return tomllib.loads((d / "plugin.toml").read_text(encoding="utf-8")).get("plugin", {})


def entry(d: Path, base: str) -> dict:
    m = manifest(d)
    return {"id": d.name, "name": m.get("name", d.name), "version": str(m.get("version", "")),
            "author": m.get("author", ""), "description": m.get("description", ""),
            "min_app": str(m.get("min_app", "0")), "base": base,
            "files": {f.name: sha(f) for f in files_of(d)}}


def extension_dirs(parent: Path) -> list[Path]:
    return sorted(p for p in parent.iterdir() if p.is_dir() and p.name not in SKIP and (p / "plugin.toml").exists())


def build_index() -> dict:
    return {"version": 1, "extensions": [entry(d, f"{d.name}/") for d in extension_dirs(EXT)]}


def render(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write(p: Path, text: str):
    p.write_text(text, encoding="utf-8", newline="\n")


def cmd_index(folder: str | None = None):
    if folder:  # repository di una sola estensione: il nome dell'estensione e' nel plugin.toml, la cartella puo' chiamarsi come vuole
        d = Path(folder).resolve()
        if not (d / "plugin.toml").exists():
            sys.exit(f"{d}: manca plugin.toml")
        ext_id = manifest(d).get("id") or d.name.removeprefix(PREFIX)
        e = {**entry(d, "./"), "id": ext_id}
        write(d / "index.json", render({"version": 1, "extensions": [e]}))
        print(f"{d / 'index.json'}: {ext_id} {e['version']} ({len(e['files'])} file)")
        return
    data = build_index()
    write(INDEX, render(data))
    print(f"{INDEX.relative_to(ROOT)}: {len(data['extensions'])} estensioni")


def cmd_install(folder: str | None = None):
    """Copia le estensioni di `folder` (default extensions/: una cartella di estensioni, oppure una sola) nella cartella
    plugin del config, al posto di quello che c'era. Una vecchia junction/symlink viene tolta (solo il collegamento)."""
    sys.path.insert(0, str(ROOT))
    from pozzo import config as C
    from pozzo.app.extensions import user_dir
    plugins = user_dir(C.load(None), "plugins")
    src = Path(folder).resolve() if folder else EXT
    dirs = [src] if (src / "plugin.toml").exists() else extension_dirs(src)
    for d in dirs:
        ext_id = manifest(d).get("id") or d.name.removeprefix(PREFIX)
        dest = plugins / ext_id
        if dest.is_symlink() or (hasattr(dest, "is_junction") and dest.is_junction()):
            os.rmdir(dest) if sys.platform == "win32" else dest.unlink()   # solo il collegamento, mai la cartella a cui punta
        elif dest.exists():
            shutil.rmtree(dest)
        dest.mkdir()
        for f in files_of(d):
            shutil.copy2(f, dest / f.name)
        print(f"{ext_id}: copiata in {dest}")


def ext_readme(e: dict, repo: str) -> str:
    return f"""# {e['name']}

{e['description']}

Estensione per [Pozzo Launcher](https://github.com/erpaffo/Pozzo-Launcher) (richiede la versione {e['min_app']} o successiva).

## Installazione

Dall'app: **Store estensioni** › **{e['name']}** › **⬇ Installa**, poi riavvia Pozzo Launcher.

Se non è nello store ufficiale: **Store estensioni** › *Fonti delle estensioni* › incolla
`https://github.com/{repo}` › **Aggiungi**.

## Sviluppo

1. Modifica i file e prova l'estensione collegandola alla tua cartella plugin:
   `python <PozzoLauncher>/tools/extensions.py install .` (copia i file: rifallo dopo ogni modifica)
2. Alza `version` in `plugin.toml` e rigenera l'indice: `python <PozzoLauncher>/tools/extensions.py index .`
3. Commit, tag `v<versione>` e push. Nello store, la voce di questa estensione va portata al tag nuovo.
"""


STORE_README = """# Store delle estensioni di Pozzo Launcher

Elenco delle estensioni che [Pozzo Launcher](https://github.com/erpaffo/Pozzo-Launcher) mostra nello **Store estensioni**.
Ogni estensione vive nel proprio repository; qui c'è solo `index.json`, con il repository e la versione approvata:

```json
{"repos": [{"repo": "utente/pozzo-ext-esempio", "ref": "v1.0"}]}
```

L'app legge l'`index.json` di ogni repository a quel tag e installa solo file con lo sha256 indicato lì.

## Aggiungere o aggiornare un'estensione

1. Il repository dell'estensione ha in cima `plugin.toml`, i suoi file e `index.json`
   (generato con `python tools/extensions.py index .` dal repository di Pozzo Launcher).
2. Pubblica un tag `v<versione>`.
3. Apri una pull request qui che aggiunge la voce o ne cambia `ref`.

Chi usa l'app può anche aggiungere un'estensione o un altro store da un link, senza passare da qui.
"""


def cmd_export(out: str, owner: str = "erpaffo"):
    dest = Path(out).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    lic = (ROOT / "LICENSE").read_text(encoding="utf-8") if (ROOT / "LICENSE").exists() else None
    repos = []
    for d in extension_dirs(EXT):
        repo = f"{PREFIX}{d.name}"
        r = dest / repo
        r.mkdir(exist_ok=True)
        for f in files_of(d):
            shutil.copy2(f, r / f.name)
        e = {**entry(d, "./"), "id": d.name}
        write(r / "index.json", render({"version": 1, "extensions": [e]}))
        write(r / "README.md", ext_readme(e, f"{owner}/{repo}"))
        write(r / ".gitattributes", GITATTRIBUTES)
        if lic:
            write(r / "LICENSE", lic)
        repos.append({"repo": f"{owner}/{repo}", "ref": f"v{e['version']}"})
        print(f"{repo}: {e['version']} ({len(e['files'])} file)")
    s = dest / STORE
    s.mkdir(exist_ok=True)
    write(s / "index.json", render({"version": 1, "repos": repos}))
    write(s / "README.md", STORE_README)
    write(s / ".gitattributes", GITATTRIBUTES)
    if lic:
        write(s / "LICENSE", lic)
    print(f"{STORE}: {len(repos)} estensioni -> {dest}")


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    fn = {"index": cmd_index, "install": cmd_install, "link": cmd_install, "export": cmd_export}.get(cmd)
    if not fn or (cmd == "export" and not args):
        sys.exit(__doc__)
    fn(*args)
