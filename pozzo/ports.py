"""Port PC: ricompilazioni e decomp di giochi console (Zelda64Recomp, Ship of Harkinian, sm64 port...).

Catalogo dall'API pubblica di Quiver Launcher (https://github.com/tgeorgiadis/quiver-launcher, MIT), in cache 24 ore;
installazione dalle release GitHub/GitLab/Codeberg nella cartella Ports\\<folderName>.

Ogni port installato ha un manifesto (.pozzo-port.json) nella sua cartella: e' l'unica fonte di verita'
(versione, eseguibile scelto, versione saltata, file della release, mod), quindi resta valido anche se si
reinstalla l'app. I file della cartella che NON vengono dalla release sono dell'utente (salvataggi, configurazione,
mod): si conservano negli aggiornamenti e finiscono nel backup dei salvataggi.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

import httpx

from . import __version__
from . import config as C
from . import platforms as P
from .matching import normalize

API = ("https://api.quiverlauncher.com/api/v1", "https://famous-wildebeest-660.convex.site/api/v1")
TTL = 24 * 3600
RELEASE_TTL = 12 * 3600  # controllo aggiornamenti: al massimo ogni 12 ore per repository (come updates.py)
MANIFEST = ".pozzo-port.json"
UA = {"User-Agent": f"PozzoLauncher/{__version__} (+https://github.com/erpaffo/Pozzo-Launcher)"}
# id console di Quiver -> id di platforms.py (gli altri coincidono; "pc", "mobile" non hanno ROM)
CONSOLES = {"gcn": "gc", "ps1": "psx", "og xbox": "xbox", "x360": "xbox360", "gen": "megadrive", "sms": "mastersystem",
            "gg": "gamegear", "32x": "sega32x", "vb": "virtualboy", "ami": "amiga"}
MOD_META = {"manifest.json", "icon.png", "readme.md", "changelog.md", "license", "license.md", "license.txt"}


def platform_of(console: str) -> str | None:
    pid = CONSOLES.get(console, console)
    return pid if pid in P.PLATFORMS and pid != "windows" else None


def ports_dir(cfg) -> Path:
    d = Path(cfg.app.get("ports_dir") or C.settings_path(cfg).parent / "Ports")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _token(cfg) -> str:
    return (cfg.app.get("github_token") or "").strip()


def _get(url: str, params: dict | None = None, token: str = ""):
    h = dict(UA)
    if token and "api.github.com" in url:
        h["Authorization"] = f"Bearer {token}"
    r = httpx.get(url, params=params, headers=h, timeout=httpx.Timeout(20, connect=8), follow_redirects=True)
    if r.status_code == 403 and "api.github.com" in url and r.headers.get("x-ratelimit-remaining") == "0":
        raise RuntimeError("limite di GitHub raggiunto (60 richieste/ora): aggiungi un token GitHub nelle impostazioni dei port")
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- catalogo (8.1)

def _fetch_catalog() -> list[dict]:
    err = None
    for base in API:
        try:
            items, cursor = [], ""
            for _ in range(100):  # 100 pagine x 100: ben oltre il catalogo attuale (~600)
                j = _get(f"{base}/apps", {"os": "windows", "projectType": "port", "limit": 100, "cursor": cursor})
                items += j.get("items") or []
                cursor = j.get("nextCursor") or ""
                if j.get("isDone") or not cursor:
                    return items
            return items
        except (httpx.HTTPError, ValueError) as e:
            err = e
    raise err


_MEM: dict = {}  # copia in memoria dell'ultimo catalogo: l'apertura non rilegge e non decodifica ogni volta il database


def cached(cache) -> dict | None:
    """Ultimo catalogo salvato, anche scaduto, senza rete."""
    if not _MEM.get("cat"):
        _MEM["cat"] = cache.kv_get("ports:catalog")
    return _MEM["cat"]


def catalog(cache, force: bool = False) -> dict:
    """{"items", "time", "error"?, "stale"?}: se l'API non risponde resta l'ultima copia."""
    hit = cached(cache)
    if hit and not force and time.time() - hit.get("time", 0) < TTL:
        return hit
    try:
        hit = {"time": time.time(), "items": [i for i in _fetch_catalog() if "windows" in (i.get("supportedOS") or [])]}
        cache.kv_set("ports:catalog", hit)
        _MEM["cat"] = hit
        return hit
    except (httpx.HTTPError, ValueError) as e:
        msg = f"{type(e).__name__}: {e}"[:200]
        return {**hit, "stale": True, "error": msg} if hit else {"time": 0, "items": [], "error": msg}


def detail(cache, slug: str) -> dict:
    """Scheda completa (con il repository del progetto), in cache 24 ore."""
    key = "ports:app:" + slug
    hit = cache.kv_get(key, max_age=TTL)
    if hit:
        return hit
    err = None
    for base in API:
        try:
            hit = _get(f"{base}/apps/{quote(slug, safe='')}")
            cache.kv_set(key, hit)
            return hit
        except (httpx.HTTPError, ValueError) as e:
            err = e
    stale = cache.kv_get(key)
    if stale:
        return stale
    raise err


# ---------------------------------------------------------------- release e asset (8.2)

def releases(provider: str, repo: str, token: str = "") -> list[dict]:
    """Release recenti (le piu' nuove prima): [{"tag", "prerelease", "url", "assets": [{"name", "url", "size"}]}]."""
    provider = (provider or "github").lower()
    if provider == "gitlab":
        j = _get(f"https://gitlab.com/api/v4/projects/{quote(repo, safe='')}/releases")
        return [{"tag": r.get("tag_name") or "", "prerelease": bool(r.get("upcoming_release")),
                 "url": (r.get("_links") or {}).get("self") or f"https://gitlab.com/{repo}/-/releases",
                 "assets": [{"name": a.get("name") or "", "url": a.get("direct_asset_url") or a.get("url"), "size": 0}
                            for a in (r.get("assets") or {}).get("links") or []]} for r in j]
    base = "https://codeberg.org/api/v1" if provider == "codeberg" else "https://api.github.com"
    j = _get(f"{base}/repos/{repo}/releases", {"per_page": 10}, token)
    return [{"tag": r.get("tag_name") or "", "prerelease": bool(r.get("prerelease")), "url": r.get("html_url") or "",
             "assets": [{"name": a.get("name") or "", "url": a.get("browser_download_url"), "size": a.get("size") or 0}
                        for a in r.get("assets") or []]}
            for r in j if not r.get("draft")]


# regole come PlatformAssetMatcher di Quiver: scarta iOS/console dedicate, simboli di debug, file accessori
# e ogni segno di un altro sistema; tiene i segni espliciti di Windows e gli archivi senza etichetta
_NON_WIN = ("linux", "macos", "osx", "darwin", "apple", ".deb", ".rpm", "appimage", "flatpak", ".dmg", ".pkg",
            "android", "arm64-v8a", ".apk", "switch")
_WIN = ("windows", "win64", "win32", "win-x64", "win-x86", "-win.", "_win.", ".exe", ".msi", "msvc", "mingw")
_DEVICE = re.compile(r"(?:^|[^a-z0-9])(?:ios|ipados|iphone|ipad|iphoneos|xbox|portmaster|stockos\d*|rg\d{2,3}[a-z0-9]*)"
                     r"(?:$|[^a-z0-9])|\.ipa(?:$|[._-])")
_DEBUG = re.compile(r"(?:^|[._-])(?:pdb|symbols|debugsymbols|debug[._-]symbols)(?:$|[._-])")
_AUX = (".sha256", ".sha1", ".md5", ".sig", ".asc", ".txt", ".json", ".md", ".yml", ".blockmap")
_ARCHIVES = (".zip", ".7z", ".rar")


def is_windows_asset(name: str) -> bool:
    n = (name or "").lower()
    if not n or _DEVICE.search(n) or _DEBUG.search(n) or n.endswith(_AUX) or any(m in n for m in _NON_WIN) \
            or re.search(r"(?:^|[^a-z0-9])mac(?:$|[^a-z0-9])", n):
        return False
    return any(m in n for m in _WIN) or bool(re.search(r"[_-]win[_-]|[_-]win\d|^win[_-]", n)) or n.endswith(_ARCHIVES)


def pick_asset(assets: list[dict], name_filter: str = "") -> dict | None:
    """Asset Windows migliore: prima x64 (poi x86, poi ARM), prima zip (poi 7z/rar, poi installer)."""
    c = [a for a in assets if is_windows_asset(a["name"]) and name_filter.lower() in a["name"].lower()]

    def rank(a):
        n = a["name"].lower()
        arch = 2 if re.search(r"arm64|aarch64", n) else 1 if re.search(r"x86(?!_64)|i[3-6]86|win32", n) else 0
        ext = next((i for i, e in enumerate((".zip", ".7z", ".rar", ".exe", ".msi")) if n.endswith(e)), 5)
        return arch, ext
    return min(c, key=rank) if c else None


def pick_release(rels: list[dict], name_filter: str = "") -> tuple[dict, dict] | None:
    """Ultima release stabile con un asset Windows (altrimenti la piu' nuova che ne ha uno)."""
    for stable in (True, False):
        for r in rels:
            if stable and r["prerelease"]:
                continue
            a = pick_asset(r["assets"], name_filter)
            if a:
                return r, a
    return None


# ---------------------------------------------------------------- download ed estrazione

def _download(url: str, dest: Path, progress=None, token: str = ""):
    h = dict(UA)
    if token and "github.com" in url:
        h["Authorization"] = f"Bearer {token}"
    with httpx.stream("GET", url, headers=h, timeout=httpx.Timeout(60, connect=10), follow_redirects=True) as r:
        r.raise_for_status()
        total, done = int(r.headers.get("content-length") or 0), 0
        with open(dest, "wb") as f:
            for b in r.iter_bytes(1 << 20):
                f.write(b)
                done += len(b)
                if progress:
                    progress(done, total)


def _extract(archive: Path, out: Path, name: str):
    out.mkdir(parents=True)
    n = name.lower()
    if n.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(out)  # zipfile scarta percorsi assoluti e ".."
    elif re.search(r"\.tar(\.\w+)?$|\.tgz$", n):
        with tarfile.open(archive) as t:
            t.extractall(out, filter="data")
    elif n.endswith((".7z", ".rar")):
        # tar.exe di Windows (libarchive) legge 7z e rar: nessuna dipendenza in piu'
        r = subprocess.run(["tar", "-xf", str(archive), "-C", str(out)], capture_output=True,
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
        if r.returncode:
            raise RuntimeError(f"estrazione di {name} non riuscita: {r.stderr.decode(errors='replace')[:200]}")
    else:  # .exe portatile o installer: si copia cosi' com'e'
        shutil.copy2(archive, out / name)
    # archivio con una sola cartella dentro: il contenuto sale di un livello
    kids = list(out.iterdir())
    if len(kids) == 1 and kids[0].is_dir():
        inner = kids[0]
        for k in list(inner.iterdir()):
            k.rename(out / k.name)
        inner.rmdir()


def release_files(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def _work(cfg, name: str) -> Path:
    d = ports_dir(cfg) / ".tmp" / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def _stage(cfg, asset: dict, folder_name: str, files_to_add: list[str], progress=None) -> tuple[Path, list[str]]:
    """Scarica ed estrae l'asset in una cartella temporanea; ritorna (cartella, file della release)."""
    work = _work(cfg, folder_name)
    arc = work / ("download" + "".join(Path(asset["name"]).suffixes[-2:]))
    _download(asset["url"], arc, progress, _token(cfg))
    staged = work / "files"
    _extract(arc, staged, asset["name"])
    arc.unlink()
    for f in files_to_add or []:  # es. portable.txt: il port salva i dati nella sua cartella
        p = _inside(staged, f)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    return staged, release_files(staged)


def _inside(root: Path, rel: str) -> Path:
    """root/rel, rifiutando percorsi che escono da root (dati dal catalogo o dall'utente)."""
    p = (root / rel).resolve()
    if p != root.resolve() and root.resolve() not in p.parents:
        raise ValueError(f"percorso non valido: {rel}")
    return p


# ---------------------------------------------------------------- port installati

def read_manifest(folder: Path) -> dict | None:
    try:
        return json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_manifest(folder: Path, man: dict):
    tmp = folder / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / MANIFEST)


def installed(cfg) -> dict[str, dict]:
    """{slug: manifesto} dei port nella cartella Ports (le cartelle con il punto sono di servizio)."""
    out = {}
    for d in sorted(ports_dir(cfg).iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            m = read_manifest(d)
            if m and m.get("slug"):
                out[m["slug"]] = {**m, "folder": str(d)}
    return out


_SKIP_EXE = re.compile(r"unins|uninstall|crash|redist|vcredist|vc_redist|dxsetup|directx|dotnet|prereq|updater|"
                       r"elevate|helper|reporter", re.I)


def exe_candidates(folder: Path, preferred: list[str] | None = None) -> list[str]:
    """Eseguibili avviabili (relativi alla cartella): prima quelli indicati dal catalogo, poi i meno profondi."""
    pats = [p.lower() for p in preferred or []]
    exes = [p for p in folder.rglob("*.exe") if p.is_file() and not p.is_symlink()]
    exes = [p for p in exes if any(fnmatch.fnmatch(p.name.lower(), x) for x in pats) or not _SKIP_EXE.search(p.name)]
    exes.sort(key=lambda p: (not any(fnmatch.fnmatch(p.name.lower(), x) for x in pats),
                             len(p.relative_to(folder).parts), -p.stat().st_size))
    return [p.relative_to(folder).as_posix() for p in exes]


def user_files(folder: Path, man: dict) -> list[Path]:
    """File dell'utente (salvataggi, configurazione, mod): tutto cio' che non viene dalla release."""
    rel = set(man.get("files") or [])
    if not rel:  # cartella aggiunta a mano: non si sa cosa sia della release
        return []
    return [p for p in folder.rglob("*") if p.is_file() and p.name != MANIFEST
            and p.relative_to(folder).as_posix() not in rel]


def install(cfg, cache, slug: str, progress=None, say=print) -> dict:
    """Installa (o aggiorna, se c'e' gia') un port del catalogo all'ultima release Windows."""
    d = detail(cache, slug)
    e, proj = d.get("entry") or {}, d.get("project") or {}
    if not proj.get("repository"):
        raise RuntimeError("il catalogo non indica il repository di questo port")
    launcher = e.get("launcher") or {}
    say(f"Cerco l'ultima release di {proj['repository']}…")
    got = pick_release(releases(proj.get("provider"), proj["repository"], _token(cfg)), launcher.get("releaseAssetFilter") or "")
    if not got:
        raise RuntimeError("nessun file per Windows nelle release del progetto")
    rel, asset = got
    man = {"slug": slug, "name": e.get("name") or slug, "consoles": e.get("consoles") or [],
           "games": [g.get("title") for g in e.get("games") or [] if g.get("title")],
           "provider": proj.get("provider") or "github", "repository": proj["repository"],
           "art": e.get("libraryArt") or {}, "description": e.get("description") or "",
           "developer": (e.get("developer") or {}).get("name") or "",
           "preferred": (launcher.get("preferredExecutables") or {}).get("windows") or [],
           "files_to_add": launcher.get("filesToAdd") or [], "asset_filter": launcher.get("releaseAssetFilter") or "",
           "mods": launcher.get("mods") or {}}
    return deploy(cfg, launcher.get("folderName") or slug, man, rel, asset, progress, say)


def deploy(cfg, folder_name: str, man: dict, rel: dict, asset: dict, progress=None, say=print) -> dict:
    """Mette la release nella cartella del port. Se il port c'e' gia': copia di sicurezza della cartella
    (Ports\\.backup\\<cartella>, una sola) e i file dell'utente passano alla versione nuova."""
    folder = _inside(ports_dir(cfg), folder_name)
    say(f"Scarico {asset['name']}…")
    staged, files = _stage(cfg, asset, folder.name, man.get("files_to_add"), progress)
    old = read_manifest(folder) if folder.exists() else None
    if folder.exists():
        bak = ports_dir(cfg) / ".backup" / folder.name
        shutil.rmtree(bak, ignore_errors=True)
        bak.parent.mkdir(parents=True, exist_ok=True)
        try:
            folder.rename(bak)
        except OSError:
            shutil.rmtree(staged.parent, ignore_errors=True)
            raise RuntimeError("la cartella del port e' in uso: chiudi il gioco e riprova")
        staged.rename(folder)
        old_rel = set((old or {}).get("files") or [])
        kept = 0
        for f in bak.rglob("*"):
            r = f.relative_to(bak).as_posix()
            if not f.is_file() or f.name == MANIFEST or r in old_rel or (not old_rel and r in files):
                continue  # file della vecchia release (o, senza elenco, gia' presente nella nuova)
            dst = folder / r
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            kept += 1
        say(f"Copia di sicurezza in {bak}; {kept} file tuoi (salvataggi, configurazione, mod) conservati")
    else:
        folder.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(folder)
    shutil.rmtree(staged.parent, ignore_errors=True)
    cands = exe_candidates(folder, man.get("preferred"))
    exe = (old or {}).get("exe")
    man = {**(old or {}), **man, "version": rel["tag"], "release_url": rel["url"], "asset": asset["name"],
           "files": files, "installed_at": time.time(),
           "exe": exe if exe in cands else (cands[0] if cands else ""), "skip": None}
    write_manifest(folder, man)
    say(f"Installato {man['name']} {rel['tag']}")
    return {**man, "folder": str(folder), "candidates": cands}


def add_folder(cfg, path: str, name: str = "", consoles: list[str] | None = None, repo: str = "") -> dict:
    """Port aggiunto a mano (8.7): una cartella gia' scaricata, facoltativamente legata a un repository."""
    folder = Path(path)
    if not folder.is_dir():
        raise ValueError("cartella non trovata")
    cands = exe_candidates(folder)
    if not cands:
        raise ValueError("nessun .exe nella cartella")
    slug = "manual-" + re.sub(r"[^a-z0-9]+", "-", (name or folder.name).lower()).strip("-")
    man = {**(read_manifest(folder) or {}), "slug": slug, "name": name or folder.name, "consoles": consoles or [],
           "games": [name or folder.name], "provider": "github", "repository": repo, "manual": True,
           "exe": cands[0], "installed_at": time.time()}
    if folder.parent.resolve() != ports_dir(cfg).resolve():
        # fuori dalla cartella Ports: si ricorda dove sta (installed() guarda solo dentro Ports)
        links = dict(cfg.app.get("port_links") or {})
        links[slug] = str(folder)
        C.save_settings(cfg, app={"port_links": links})
    write_manifest(folder, man)
    return {**man, "folder": str(folder), "candidates": cands}


def add_repo(cfg, repo: str, name: str = "", consoles: list[str] | None = None, progress=None, say=print) -> dict:
    """Port aggiunto a mano da un repository qualsiasi (utente/repo, GitHub)."""
    repo = re.sub(r"^https?://github\.com/|\.git$|/$", "", repo.strip())
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("scrivi il repository come utente/progetto")
    got = pick_release(releases("github", repo, _token(cfg)))
    if not got:
        raise RuntimeError("nessun file per Windows nelle release del progetto")
    name = name or repo.split("/")[1]
    man = {"slug": "manual-" + re.sub(r"[^a-z0-9]+", "-", repo.lower()).strip("-"), "name": name,
           "consoles": consoles or [], "games": [name], "provider": "github", "repository": repo, "manual": True}
    return deploy(cfg, re.sub(r"[^\w.-]+", "-", repo.replace("/", "-")), man, *got, progress=progress, say=say)


def all_installed(cfg) -> dict[str, dict]:
    """Port nella cartella Ports + quelli aggiunti a mano da altre cartelle."""
    out = installed(cfg)
    for slug, path in (cfg.app.get("port_links") or {}).items():
        m = read_manifest(Path(path))
        if m and slug not in out:
            out[slug] = {**m, "folder": path}
    return out


def set_manifest(cfg, slug: str, **values) -> dict:
    m = all_installed(cfg).get(slug)
    if not m:
        raise KeyError(slug)
    folder = Path(m.pop("folder"))
    m.update(values)
    write_manifest(folder, m)
    return {**m, "folder": str(folder)}


def uninstall(cfg, slug: str):
    """Sposta la cartella del port nel Cestino (cancellazione definitiva solo fuori da Windows)."""
    m = all_installed(cfg).get(slug)
    if not m:
        raise KeyError(slug)
    folder = Path(m["folder"])
    links = dict(cfg.app.get("port_links") or {})
    if slug in links:  # aggiunto a mano da un'altra cartella: si toglie solo dalla libreria, il disco resta com'e'
        links.pop(slug)
        C.save_settings(cfg, app={"port_links": links or None})
        (folder / MANIFEST).unlink(missing_ok=True)
        return
    _inside(ports_dir(cfg), folder.name)
    if sys.platform == "win32":
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::"
                            "DeleteDirectory($env:GS_PORT_DIR, 'OnlyErrorDialogs', 'SendToRecycleBin')"],
                           env={**os.environ, "GS_PORT_DIR": str(folder)}, capture_output=True, timeout=120,
                           creationflags=0x08000000)
        if r.returncode or folder.exists():
            raise RuntimeError("non riesco a spostare la cartella nel Cestino (il gioco e' aperto?)")
    else:
        shutil.rmtree(folder)


# ---------------------------------------------------------------- aggiornamenti (8.5)

def latest(cache, man: dict, token: str = "", force: bool = False, offline: bool = False) -> dict | None:
    """Ultima release Windows del port: {"tag", "url"}; in cache 12 ore per repository."""
    if not man.get("repository"):
        return None
    key = f"ports:rel:{man.get('provider')}:{man['repository']}:{man.get('asset_filter') or ''}"
    hit = cache.kv_get(key)
    if offline or (hit and not force and time.time() - hit.get("time", 0) < RELEASE_TTL):
        return hit
    try:
        got = pick_release(releases(man.get("provider"), man["repository"], token), man.get("asset_filter") or "")
    except (httpx.HTTPError, ValueError, RuntimeError):
        return hit
    hit = {"time": time.time(), "tag": got[0]["tag"] if got else "", "url": got[0]["url"] if got else ""}
    cache.kv_set(key, hit)
    return hit


def update_info(cache, man: dict, token: str = "", force: bool = False, offline: bool = False) -> dict:
    lt = latest(cache, man, token, force, offline) or {}
    tag = lt.get("tag") or ""
    newer = bool(tag) and tag != man.get("version") and tag != man.get("skip")
    return {"latest": tag, "latest_url": lt.get("url") or "", "update": newer}


# ---------------------------------------------------------------- ROM della libreria (8.4, 8.6)

def rom_index(games: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """(console, titolo normalizzato) -> giochi ROM della libreria (stessa corrispondenza di «Giochi gratis»)."""
    idx: dict = {}
    for g in games:
        if not g.get("store") and g.get("files"):
            idx.setdefault((g["platform"], normalize(g["title"])), []).append(g)
    return idx


def roms_for(entry: dict, idx: dict) -> list[dict]:
    titles = {normalize(t) for t in [*(g.get("title") for g in entry.get("games") or [] if isinstance(g, dict)),
                                      *(entry.get("games") or [] if entry.get("manual") else []), entry.get("name")]
              if isinstance(t, str) and t}
    out = []
    for c in entry.get("consoles") or []:
        pid = platform_of(c)
        for t in titles:
            out += [g for g in idx.get((pid, t), []) if g not in out]
    return out


def _hash(f, algo: str) -> str:
    h = hashlib.new(algo)
    for b in iter(lambda: f.read(1 << 20), b""):
        h.update(b)
    return h.hexdigest().lower()


def find_rom(sf: dict, paths: list[Path]) -> tuple[Path, str] | None:
    """(file, membro dello zip o "") della ROM con uno degli hash accettati dal port."""
    want = {(a.get("algo") or "sha1").lower(): set() for a in sf.get("accepted") or []}
    for a in sf.get("accepted") or []:
        want[(a.get("algo") or "sha1").lower()].add((a.get("value") or "").lower())
    exts = {e.lower().lstrip(".") for e in sf.get("extensions") or []}
    for p in paths:
        try:
            if p.suffix.lower() == ".zip":
                with zipfile.ZipFile(p) as z:
                    for m in z.infolist():
                        if not m.is_dir() and (not exts or m.filename.rsplit(".", 1)[-1].lower() in exts):
                            for algo, vals in want.items():
                                with z.open(m) as f:
                                    if _hash(f, algo) in vals:
                                        return p, m.filename
            elif p.is_file():
                for algo, vals in want.items():
                    with open(p, "rb") as f:
                        if _hash(f, algo) in vals:
                            return p, ""
        except (OSError, zipfile.BadZipFile, ValueError):
            continue
    return None


def setup_rom(folder: Path, sf: dict, src: Path, member: str = "") -> Path:
    """Copia la ROM giusta dove il port la vuole (destinazione del catalogo, dentro la cartella del port)."""
    dst = _inside(folder, sf["destination"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    if member:
        with zipfile.ZipFile(src) as z, z.open(member) as f, open(dst, "wb") as out:
            shutil.copyfileobj(f, out, 1 << 20)
    else:
        shutil.copy2(src, dst)
    return dst


# ---------------------------------------------------------------- mod (8.9)

def mod_list(cache, mods: dict) -> list[dict]:
    """Mod da Thunderstore (elenco in cache 12 ore). GameBanana non ha un'API di download comoda: solo il link."""
    out = []
    for s in mods.get("sources") or []:
        m = re.search(r"thunderstore\.io/c/([^/]+)", s.get("sourceUrl") or "")
        if s.get("provider") != "thunderstore" or not m:
            continue
        key = "ports:ts:" + m.group(1)
        pk = cache.kv_get(key, max_age=RELEASE_TTL)
        if pk is None:
            try:
                raw = _get(f"https://thunderstore.io/c/{m.group(1)}/api/v1/package/")
            except (httpx.HTTPError, ValueError):
                raw = None
            if raw is None:
                pk = cache.kv_get(key) or []
            else:
                pk = [{"id": p["full_name"], "name": p["name"].replace("_", " "), "owner": p["owner"],
                       "description": v.get("description") or "", "icon": v.get("icon") or "",
                       "version": v.get("version_number") or "", "url": v.get("download_url") or "",
                       "page": p.get("package_url") or "", "downloads": sum(x.get("downloads") or 0 for x in p["versions"]),
                       "nsfw": p.get("has_nsfw_content")}
                      for p in raw if p.get("versions") and not p.get("is_deprecated") for v in [p["versions"][0]]]
                cache.kv_set(key, pk)
        out += pk
    return sorted(out, key=lambda x: -x["downloads"])


def install_mod(cfg, slug: str, mod: dict) -> dict:
    m = all_installed(cfg).get(slug)
    if not m:
        raise KeyError(slug)
    folder = Path(m["folder"])
    if not str(mod.get("url") or "").startswith("https://thunderstore.io/"):
        raise ValueError("mod non valida")
    target = _inside(folder, (m.get("mods") or {}).get("path") or "mods")
    work = _work(cfg, "mod-" + slug)
    try:
        _download(mod["url"], work / "mod.zip")
        files = []
        with zipfile.ZipFile(work / "mod.zip") as z:
            for i in z.infolist():
                if i.is_dir() or Path(i.filename).name.lower() in MOD_META:
                    continue
                dst = _inside(target, i.filename)
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as f, open(dst, "wb") as out:
                    shutil.copyfileobj(f, out)
                files.append(dst.relative_to(folder).as_posix())
    finally:
        shutil.rmtree(work, ignore_errors=True)
    mods = dict(m.get("installed_mods") or {})
    mods[mod["id"]] = {"name": mod.get("name") or mod["id"], "version": mod.get("version") or "", "files": files}
    return set_manifest(cfg, slug, installed_mods=mods)


def remove_mod(cfg, slug: str, mod_id: str) -> dict:
    m = all_installed(cfg).get(slug)
    if not m:
        raise KeyError(slug)
    folder = Path(m["folder"])
    mods = dict(m.get("installed_mods") or {})
    for f in (mods.pop(mod_id, None) or {}).get("files", []):
        _inside(folder, f).unlink(missing_ok=True)
    return set_manifest(cfg, slug, installed_mods=mods)
