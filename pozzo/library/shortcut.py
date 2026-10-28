"""Collegamento sul desktop (.lnk) che avvia un gioco con PozzoLauncher («play --key», come le app di Sunshine).

Il tempo di gioco viene registrato: se l'app e' chiusa, il comando apre un PozzoLauncher senza finestra finche' si gioca.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def desktop_dir() -> Path:
    if sys.platform == "win32":
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:  # CSIDL_DESKTOPDIRECTORY (anche su OneDrive)
            return Path(buf.value)
    return Path.home() / "Desktop"


def file_name(title: str) -> str:
    """Titolo del gioco come nome di file valido su Windows."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title).strip(" .")[:100] or "Gioco"


def split_cmd(base: str) -> tuple[str, str]:
    """'"C:\\x\\cli.exe" -c "a b"' -> ('C:\\x\\cli.exe', '-c "a b"')."""
    m = re.match(r'\s*"([^"]+)"\s*(.*)', base)
    return (m.group(1), m.group(2)) if m else (base, "")


def make_icon(image: Path, out: Path) -> Path | None:
    """Icona .ico quadrata dalla copertina (centrata su fondo trasparente)."""
    try:
        from PIL import Image
        with Image.open(image) as im:
            im = im.convert("RGBA")
            im.thumbnail((256, 256))
            sq = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            sq.paste(im, ((256 - im.width) // 2, (256 - im.height) // 2))
            out.parent.mkdir(parents=True, exist_ok=True)
            sq.save(out, format="ICO", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
        return out
    except Exception:
        return None


def create(title: str, base_cmd: str, workdir: str, key: str, icon: str | None) -> Path:
    """Crea (o sostituisce) il collegamento sul desktop e ne restituisce il percorso."""
    if sys.platform != "win32":
        raise RuntimeError("I collegamenti sul desktop si creano solo su Windows")
    target, args = split_cmd(base_cmd)
    lnk = desktop_dir() / f"{file_name(title)}.lnk"
    # valori passati come variabili d'ambiente: nessun titolo o percorso finisce dentro il codice PowerShell
    env = {**os.environ, "GS_LNK": str(lnk), "GS_TARGET": target, "GS_ARGS": f'{args} play --key "{key}"'.strip(),
           "GS_WD": workdir, "GS_ICON": icon or target, "GS_DESC": f"Gioca a {title} con PozzoLauncher"}
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:GS_LNK);$s.TargetPath=$env:GS_TARGET;"
          "$s.Arguments=$env:GS_ARGS;$s.WorkingDirectory=$env:GS_WD;$s.IconLocation=$env:GS_ICON;"
          "$s.Description=$env:GS_DESC;$s.WindowStyle=7;$s.Save()")  # 7 = ridotta a icona: la console del comando non si vede
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, capture_output=True,
                       text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode or not lnk.exists():
        raise RuntimeError(f"Collegamento non creato: {(r.stderr or r.stdout).strip()[:300]}")
    return lnk
