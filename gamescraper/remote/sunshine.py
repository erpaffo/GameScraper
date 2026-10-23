"""Streaming dei giochi con Sunshine (server) e Moonlight (app su telefono, tablet, TV, altri PC).

GameScraper non reinventa lo streaming: Sunshine (open source, https://app.lizardbyte.dev/Sunshine)
cattura e codifica lo schermo del PC, Moonlight (https://moonlight-stream.org) lo riceve con latenza
bassissima e manda indietro controller, tastiera e touch. Qui si pubblicano in Sunshine, tramite la sua
API web (https://localhost:47990), le "app" che Moonlight mostra:
  - "GameScraper Big Picture": la Big Picture a schermo intero, da cui si sceglie qualsiasi gioco;
  - i giochi scelti (in corso, preferiti, recenti), ognuno con la sua copertina: Moonlight li avvia
    direttamente e lo streaming si chiude quando il gioco finisce.
Le app di GameScraper si riconoscono dal comando (" play --key " / " bigpicture"): le altre app di
Sunshine (Desktop, Steam Big Picture, ...) non vengono toccate.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API = "https://localhost:47990"
MARK_PLAY, MARK_BP = " play --key ", " bigpicture"


def install_dir() -> Path | None:
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        p = Path(base) / "Sunshine" / "sunshine.exe"
        if base and p.exists():
            return p.parent
    return None


def service_state() -> str:
    """'running', 'stopped' o '' (servizio non installato / non Windows)."""
    if sys.platform != "win32":
        return ""
    try:
        out = subprocess.run(["sc", "query", "SunshineService"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return "running" if "RUNNING" in out else "stopped" if "STOPPED" in out else ""


def start_service() -> bool:
    """Avvia il servizio di Sunshine chiedendo i permessi di amministratore (finestra di Windows)."""
    if sys.platform != "win32":
        return False
    import ctypes
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "sc.exe", "start SunshineService", None, 0)
    return r > 32


def launcher_cmd(cfg) -> tuple[str, str]:
    """(comando base per avviare GameScraper da Sunshine, cartella di lavoro)."""
    conf = f' -c "{cfg.config_path}"' if getattr(cfg, "config_path", None) else ""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        cli = exe.with_name("gamescraper-cli.exe" if sys.platform == "win32" else "gamescraper-cli")
        return f'"{cli if cli.exists() else exe}"{conf}', str(exe.parent)
    root = Path(__file__).resolve().parents[2]
    return f'"{sys.executable}" -m gamescraper{conf}', str(root)


class Sunshine:
    def __init__(self, user: str, password: str, base: str = API):
        import httpx
        self.c = httpx.Client(base_url=base, auth=(user, password), verify=False, timeout=15)  # certificato autofirmato locale

    def close(self):
        self.c.close()

    def _check(self, r):
        if r.status_code == 401:
            raise PermissionError("Credenziali di Sunshine sbagliate (le stesse dell'interfaccia web di Sunshine)")
        r.raise_for_status()
        return r

    def apps(self) -> list[dict]:
        return self._check(self.c.get("/api/apps")).json().get("apps", [])

    def save(self, app: dict, index: int = -1):
        self._check(self.c.post("/api/apps", json={**app, "index": index}))

    def delete(self, index: int):
        self._check(self.c.delete(f"/api/apps/{index}"))


def ours(app: dict) -> bool:
    cmd = str(app.get("cmd") or "")
    return MARK_PLAY in cmd or cmd.endswith(MARK_BP)


def publish(cfg, user: str, password: str, games: list[dict], covers_dir: Path, cover_of) -> dict:
    """Sostituisce le app di GameScraper in Sunshine. games: [{key, title, id}];
    cover_of(game) -> Path di un'immagine o None. Ritorna {"published", "removed"}."""
    base, wd = launcher_cmd(cfg)
    covers_dir.mkdir(parents=True, exist_ok=True)
    s = Sunshine(user, password)
    try:
        old = s.apps()
        removed = 0
        for i in sorted((i for i, a in enumerate(old) if ours(a)), reverse=True):  # dall'ultimo: gli indici restano validi
            s.delete(i)
            removed += 1
        bp_img = covers_dir / "bigpicture.png"
        _make_bp_image(bp_img)
        s.save({"name": "GameScraper Big Picture", "cmd": base + MARK_BP, "working-dir": wd, "image-path": str(bp_img),
                "auto-detach": True, "wait-all": True, "exit-timeout": 5})
        n = 1
        for g in games:
            img = _cover_png(cover_of(g), covers_dir / f"{g['id']}.png")
            app = {"name": g["title"][:60], "cmd": f'{base}{MARK_PLAY}"{g["key"]}"', "working-dir": wd,
                   "auto-detach": True, "wait-all": True, "exit-timeout": 5}
            if img:
                app["image-path"] = str(img)
            s.save(app)
            n += 1
        return {"published": n, "removed": removed}
    finally:
        s.close()


def _cover_png(src: Path | None, dest: Path) -> Path | None:
    """Copertina in PNG 600x800 (formato delle copertine di Moonlight)."""
    if not src or not Path(src).exists():
        return None
    from PIL import Image, ImageOps
    try:
        with Image.open(src) as im:
            im = ImageOps.fit(im.convert("RGB"), (600, 800), Image.Resampling.LANCZOS)
            im.save(dest, "PNG", optimize=True)
        return dest
    except Exception:
        return None


def _make_bp_image(dest: Path):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (600, 800))
    px = im.load()
    for y in range(800):
        for x in range(600):
            t = (x / 600 + y / 800) / 2
            px[x, y] = (int(0x2a + 0x20 * t), int(0x3c + 0x10 * t), int(0x8c + 0x40 * t))
    d = ImageDraw.Draw(im)
    c, r, r2 = (300, 330), 150, 60
    d.polygon([(c[0], c[1] - r), (c[0] + r, c[1]), (c[0], c[1] + r), (c[0] - r, c[1])], fill="white")
    d.polygon([(c[0], c[1] - r2), (c[0] + r2, c[1]), (c[0], c[1] + r2), (c[0] - r2, c[1])], fill=(0x5b, 0x6c, 0xe6))
    try:
        from PIL import ImageFont
        f = ImageFont.truetype("segoeuib.ttf" if sys.platform == "win32" else "DejaVuSans-Bold.ttf", 54)
        f2 = ImageFont.truetype("segoeui.ttf" if sys.platform == "win32" else "DejaVuSans.ttf", 40)
    except Exception:
        f = f2 = None
    d.text((300, 590), "GameScraper", fill="white", anchor="mm", font=f)
    d.text((300, 650), "Big Picture", fill=(220, 225, 255), anchor="mm", font=f2)
    im.save(dest, "PNG", optimize=True)
