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
# app senza comando: Moonlight mostra lo schermo del PC, cioe' il gioco gia' avviato dal telefono o dal PC
CURRENT = "▶ Gioco in corso"


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


def start_mode() -> str:
    """'auto' (parte con Windows), 'manual', 'disabled' o '' se non si sa."""
    if sys.platform != "win32":
        return ""
    try:
        out = subprocess.run(["sc", "qc", "SunshineService"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return "auto" if "AUTO_START" in out else "manual" if "DEMAND_START" in out else "disabled" if "DISABLED" in out else ""


def start_service() -> bool:
    """Avvia il servizio di Sunshine e lo imposta per partire con Windows, con UNA richiesta dei permessi di
    amministratore. Con l'avvio manuale lo streaming smette di funzionare dopo ogni riavvio (o dopo «Esci»
    dall'icona di Sunshine) e Moonlight non trova piu' il PC."""
    if sys.platform != "win32":
        return False
    import ctypes
    args = "/c sc config SunshineService start= auto & sc start SunshineService"
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", args, None, 0)
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


def read_conf() -> dict[str, str]:
    """sunshine.conf (chiave = valore). Le voci assenti usano i valori predefiniti di Sunshine."""
    d = install_dir()
    p = d / "config" / "sunshine.conf" if d else None
    out: dict[str, str] = {}
    if p and p.is_file():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.strip().startswith("#"):
                out[k.strip()] = v.strip()
    return out


def pc_link() -> dict:
    """Scheda di rete con cui il PC esce sulla rete di casa: cavo o Wi-Fi, velocita' (Mbit/s)."""
    import re
    import socket

    import psutil
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # UDP: non invia nulla, serve solo a sapere quale indirizzo locale si usa
            ip = s.getsockname()[0]
    except OSError:
        return {}
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        if any(a.address == ip for a in addrs):
            st = stats.get(name)
            return {"name": name, "ip": ip, "wifi": bool(re.search(r"wi-?fi|wireless|wlan", name, re.I)),
                    "speed": st.speed if st else 0}
    return {"ip": ip}


def optimize_checks() -> list[dict]:
    """Controlli sul PC per la latenza dello streaming: [{level, title, detail}] (ok / warn / info)."""
    out = []
    add = lambda level, title, detail="": out.append({"level": level, "title": title, "detail": detail})  # noqa: E731
    if not install_dir():
        add("warn", "Sunshine non è installato", "Serve sul PC per lo streaming verso TV e telefono.")
        return out
    state, mode = service_state(), start_mode()
    if state != "running":
        add("warn", "Sunshine è fermo", "Avvialo dalla sezione qui sopra: senza, Moonlight non trova il PC.")
    elif mode and mode != "auto":
        add("warn", "Sunshine non parte con Windows", "Dopo un riavvio lo streaming smette di funzionare.")
    else:
        add("ok", "Sunshine attivo e avviato con Windows")
    conf = read_conf()
    preset = conf.get("nvenc_preset", "1")
    if preset.isdigit() and int(preset) > 1:
        add("warn", f"Encoder NVIDIA su qualità P{preset}", "P1 è il più veloce: interfaccia di Sunshine › Configuration › "
            "NVIDIA NVENC Encoder › Performance preset › P1 (fastest). Ogni livello in più aggiunge millisecondi di codifica.")
    else:
        add("ok", "Encoder NVIDIA nel preset più veloce (P1)")
    twopass = conf.get("nvenc_twopass", "quarter_res")
    if twopass != "disabled":
        add("info", f"Two-pass attivo ({'risoluzione ridotta' if twopass == 'quarter_res' else 'piena'})",
            "Migliora la qualità ma aggiunge un po' di lavoro per ogni fotogramma. Per la latenza minima: "
            "Configuration › NVIDIA NVENC Encoder › Two-pass mode › Disabled (alza un po' il bitrate in Moonlight).")
    fec = conf.get("fec_percentage", "20")
    if fec.isdigit() and int(fec) > 30:
        add("info", f"Correzione degli errori al {fec}%", "Sulla rete di casa basta il valore predefinito (20%): "
            "Configuration › Network › FEC percentage.")
    link = pc_link()
    if link.get("wifi"):
        add("warn", f"Il PC è collegato in Wi-Fi ({link.get('name')})",
            "Il cavo Ethernet sul PC (e se possibile sulla TV o sul box) è il miglioramento più grande: "
            "meno latenza e soprattutto niente scatti.")
    elif link.get("name"):
        sp = link.get("speed") or 0
        add("ok" if not sp or sp >= 1000 else "info", f"PC collegato via cavo ({link['name']}"
            + (f", {sp} Mbit/s)" if sp else ")"),
            "" if not sp or sp >= 1000 else "A 100 Mbit/s il bitrate di Moonlight va tenuto sotto i 60-70 Mbit/s.")
    return out


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
    return MARK_PLAY in cmd or cmd.endswith(MARK_BP) or (app.get("name") == CURRENT and not cmd)


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
        cur_img = covers_dir / "current.png"
        _make_bp_image(cur_img, "Gioco in corso")
        s.save({"name": CURRENT, "image-path": str(cur_img)})
        n = 2
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


def _make_bp_image(dest: Path, subtitle: str = "Big Picture"):
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
    d.text((300, 650), subtitle, fill=(220, 225, 255), anchor="mm", font=f2)
    im.save(dest, "PNG", optimize=True)
