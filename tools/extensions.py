"""Estensioni (cartella extensions/): indice per lo store e collegamento alla copia di sviluppo. Istruzioni in docs/ESTENSIONI.md.

  python tools/extensions.py index   # rigenera extensions/index.json (sha256 di ogni file): va fatto prima del commit
  python tools/extensions.py link    # collega extensions/<id> nella cartella plugin del tuo config (junction/symlink)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extensions"
INDEX = EXT / "index.json"
SKIP = {"__pycache__", ".pytest_cache"}


def files_of(d: Path) -> list[Path]:
    """File pubblicati di un'estensione: solo quelli in cima alla cartella (lo store non accetta sottocartelle)."""
    return sorted(f for f in d.iterdir() if f.is_file() and not f.name.startswith(".") and f.suffix != ".pyc")


TEXT = {".py", ".js", ".css", ".toml", ".json", ".md"}


def sha(f: Path) -> str:
    """sha256 del file come lo salva git (testo con LF, vedi .gitattributes): e' quello servito da GitHub raw."""
    b = f.read_bytes()
    return hashlib.sha256(b.replace(b"\r\n", b"\n") if f.suffix in TEXT else b).hexdigest()


def build_index() -> dict:
    out = []
    for d in sorted(p for p in EXT.iterdir() if p.is_dir() and p.name not in SKIP):
        mf = d / "plugin.toml"
        if not mf.exists():
            continue
        m = tomllib.loads(mf.read_text(encoding="utf-8")).get("plugin", {})
        out.append({"id": d.name, "name": m.get("name", d.name), "version": str(m.get("version", "")),
                    "author": m.get("author", ""), "description": m.get("description", ""),
                    "min_app": str(m.get("min_app", "0")), "base": f"{d.name}/",
                    "files": {f.name: sha(f) for f in files_of(d)}})
    return {"version": 1, "extensions": out}


def render(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def cmd_index():
    data = build_index()
    INDEX.write_text(render(data), encoding="utf-8", newline="\n")
    print(f"{INDEX.relative_to(ROOT)}: {len(data['extensions'])} estensioni")


def _same(a: Path, b: Path) -> bool:
    """Copia gia' installata identica ai sorgenti: si puo' sostituire con il collegamento senza perdere nulla."""
    return all((b / f.name).is_file() and sha(f) == sha(b / f.name) for f in files_of(a)) \
        and {f.name for f in files_of(b)} <= {f.name for f in files_of(a)}


def cmd_link():
    sys.path.insert(0, str(ROOT))
    from gamescraper import config as C
    from gamescraper.app.extensions import user_dir
    plugins = user_dir(C.load(None), "plugins")
    for d in sorted(p for p in EXT.iterdir() if (p / "plugin.toml").exists()):
        dest = plugins / d.name
        if dest.is_symlink() or dest.is_junction():
            print(f"{d.name}: gia' collegata")
            continue
        if dest.exists():
            if not _same(d, dest):
                print(f"{d.name}: {dest} esiste ed e' diversa dai sorgenti, lasciata com'e' (rimuovila per collegarla)")
                continue
            shutil.rmtree(dest)
        if sys.platform == "win32":  # junction: non servono i permessi da amministratore come per i symlink
            subprocess.run(["cmd", "/c", "mklink", "/J", str(dest), str(d)], check=True, capture_output=True)
        else:
            os.symlink(d, dest, target_is_directory=True)
        print(f"{d.name}: collegata -> {dest}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"index": cmd_index, "link": cmd_link}.get(cmd, lambda: sys.exit(__doc__))()
