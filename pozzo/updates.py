"""Versione installata, changelog, controllo e installazione degli aggiornamenti (ultima release su GitHub).

Il controllo fa al massimo una richiesta ogni 12 ore (risposta in cache.db). install() scarica il file della
release adatto a come e' installata l'app (installer Windows, zip/tar.gz portatile, .app, .deb) e lo applica.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

from . import __version__

REPO = "erpaffo/GameScraper"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
TTL = 12 * 3600


def parse(v: str) -> tuple[int, ...]:
    """'v0.2.1' -> (0, 2, 1); parti non numeriche (es. '-dev') ignorate."""
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("-")[0])[:3]) or (0,)


def changelog() -> str:
    """changelog.md accanto al pacchetto (sorgenti) o dentro l'eseguibile (PyInstaller)."""
    bases = [Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parent.parent]
    for b in bases:
        p = b / "changelog.md"
        if str(b) and p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


def check(cache, force: bool = False) -> dict:
    hit = cache.kv_get("update_check")
    if not hit or force or time.time() - hit.get("time", 0) > TTL:
        try:
            r = httpx.get(LATEST, timeout=httpx.Timeout(10, connect=5), follow_redirects=True,
                          headers={"Accept": "application/vnd.github+json", "User-Agent": f"PozzoLauncher/{__version__}"})
            if r.status_code == 404:  # nessuna release pubblicata
                hit = {"time": time.time(), "latest": None}
            else:
                r.raise_for_status()
                j = r.json()
                hit = {"time": time.time(), "latest": j.get("tag_name"), "url": j.get("html_url"),
                       "published": j.get("published_at"), "notes": (j.get("body") or "")[:4000],
                       "assets": [{"name": a.get("name") or "", "url": a.get("browser_download_url") or "",
                                   "size": a.get("size") or 0, "digest": a.get("digest")} for a in j.get("assets") or []]}
            cache.kv_set("update_check", hit)
        except (httpx.HTTPError, ValueError) as e:
            return {"current": __version__, "error": f"{type(e).__name__}: {e}"[:200], **(hit or {})}
    latest = hit.get("latest")
    return {"current": __version__, **hit, "newer": bool(latest) and parse(latest) > parse(__version__)}


# ---------------------------------------------------------------- installazione dall'app
# file della release per ogni modo di installazione (nomi creati da .github/workflows/release.yml)
SUFFIX = {"setup": "-windows-x64-setup.exe", "win": "-windows-x64.zip", "mac": "-macos-arm64.zip",
          "linux": "-linux-x64.tar.gz", "deb": "_amd64.deb"}
STATE = {"status": "idle", "phase": "", "done": 0, "total": 0, "kind": None}


def app_dir() -> Path:
    """Cartella dell'app da aggiornare: quella dell'eseguibile, su macOS il pacchetto PozzoLauncher.app."""
    exe = Path(sys.executable).resolve()
    return exe.parents[2] if sys.platform == "darwin" else exe.parent


def install_kind() -> str | None:
    """Come si aggiorna questa copia (chiave di SUFFIX); None se avviata dai sorgenti."""
    if not getattr(sys, "frozen", False):
        return None
    d = app_dir()
    if sys.platform == "win32":
        return "setup" if (d / "unins000.exe").exists() else "win"
    if sys.platform == "darwin":
        return "mac"
    return "linux" if os.access(d, os.W_OK) else "deb"  # /opt/PozzoLauncher del .deb non e' scrivibile


def _env() -> dict:
    """Ambiente pulito per i programmi lanciati: senza le librerie interne dell'eseguibile PyInstaller."""
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def _detach(cmd: list[str]):
    """Avvia un processo che sopravvive alla chiusura dell'app."""
    if sys.platform == "win32":
        subprocess.Popen(cmd, env=_env(), close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        subprocess.Popen(cmd, env=_env(), close_fds=True, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def swap_script(tmp: Path, src: Path, dst: Path, pid: int) -> list[str]:
    """Script che aspetta la chiusura dell'app, copia la nuova versione sopra la vecchia e la riapre.
    Copia senza cancellare: accanto all'exe portatile possono esserci config.toml e la cache dell'utente."""
    if sys.platform == "win32":
        s = tmp / "update.bat"
        # percorsi completi: find/timeout di Git per Windows nel PATH avrebbero la precedenza su quelli di sistema
        s.write_text("@echo off\r\nchcp 65001 >nul\r\nset S=%SystemRoot%\\System32\r\n:wait\r\n"
                     f'"%S%\\tasklist.exe" /FI "PID eq {pid}" | "%S%\\find.exe" "{pid}" >nul'
                     ' && ("%S%\\timeout.exe" /t 1 /nobreak >nul & goto wait)\r\n'
                     f'robocopy "{src}" "{dst}" /E /R:10 /W:1 >nul\r\n'
                     f'start "" "{dst / "PozzoLauncher.exe"}"\r\n', encoding="utf-8")
        return ["cmd", "/c", str(s)]
    run = f"open {shlex.quote(str(dst))}" if sys.platform == "darwin" else f"{shlex.quote(str(dst / 'PozzoLauncher'))} &"
    s = tmp / "update.sh"
    s.write_text(f"#!/bin/sh\nwhile kill -0 {pid} 2>/dev/null; do sleep 1; done\n"
                 f"cp -Rf {shlex.quote(str(src))}/. {shlex.quote(str(dst))}/\n{run}\n", encoding="utf-8")
    return ["/bin/sh", str(s)]


def install(cache, quit) -> None:
    """Scarica e installa l'ultima versione; quit() chiude l'app perche' la nuova prenda il suo posto.
    Avanzamento ed errori in STATE (da eseguire in un thread)."""
    kind = install_kind()
    STATE.update(status="running", phase="Controllo della versione", done=0, total=0, kind=kind)
    try:
        if not kind:
            raise RuntimeError("Avviata dai sorgenti: aggiorna con git pull")
        info = check(cache, force=True)
        if not info.get("newer"):
            raise RuntimeError(info.get("error") or "Hai gia' l'ultima versione")
        a = next((x for x in info.get("assets") or [] if x["name"].endswith(SUFFIX[kind])), None)
        if not a:
            raise RuntimeError(f"La release {info['latest']} non contiene il file per questo sistema (*{SUFFIX[kind]})")
        if not a["url"].startswith(f"https://github.com/{REPO}/releases/download/"):
            raise RuntimeError("Indirizzo di download inatteso: " + a["url"][:120])
        if kind in ("win", "mac", "linux") and not os.access(app_dir().parent if kind == "mac" else app_dir(), os.W_OK):
            raise RuntimeError(f"Cartella non scrivibile ({app_dir()}): scarica la nuova versione a mano")
        tmp = Path(tempfile.mkdtemp(prefix="pozzo-update-"))
        file = tmp / Path(a["name"]).name
        STATE.update(phase=f"Download di {file.name}", total=a["size"])
        h = hashlib.sha256()
        with httpx.stream("GET", a["url"], follow_redirects=True, timeout=httpx.Timeout(60, connect=15),
                          headers={"User-Agent": f"PozzoLauncher/{__version__}"}) as r, file.open("wb") as f:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                h.update(chunk)
                STATE["done"] += len(chunk)
        if a.get("digest") and a["digest"] != "sha256:" + h.hexdigest():
            raise RuntimeError("File scaricato danneggiato (checksum diverso): riprova")
        STATE["phase"] = "Installazione"
        if kind == "deb":  # serve la password di amministratore: ci pensa l'installatore del sistema
            _detach(["xdg-open", str(file)])
            STATE.update(status="done", phase=f"Aperto {file.name}: completa l'installazione e riavvia PozzoLauncher")
            return
        if kind == "setup":
            _detach([str(file), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"])
        else:
            new = tmp / "new"
            if kind == "win":
                with zipfile.ZipFile(file) as z:
                    z.extractall(new)
            elif kind == "mac":  # ditto conserva permessi e link simbolici del pacchetto .app
                subprocess.run(["ditto", "-x", "-k", str(file), str(new)], check=True, env=_env())
            else:
                with tarfile.open(file) as t:
                    t.extractall(new, filter="data")
            src = next(new.iterdir())  # PozzoLauncher/ oppure PozzoLauncher.app/
            _detach(swap_script(tmp, src, app_dir(), os.getpid()))
        STATE.update(status="done", phase="Riavvio con la nuova versione…")
        quit()
    except Exception as e:
        STATE.update(status="error", phase=f"{type(e).__name__}: {e}"[:300])
