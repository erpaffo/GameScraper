"""Immagini di console e store per l'interfaccia.

Console: disegni "systematic" di RetroArch (github.com/libretro/retroarch-assets, licenza CC BY 4.0),
         scaricati una sola volta e tenuti in cache (cache/icons).
Store:   icona del sito (apple-touch-icon / favicon / <link rel="icon"> della pagina principale);
         se il sito non la fornisce, un'icona disegnata dall'app con l'iniziale e un colore.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx

from .. import platforms as P

ASSETS = "https://raw.githubusercontent.com/libretro/retroarch-assets/master/xmb/systematic/png/"
CREDIT = "Immagini delle console: RetroArch assets (libretro), licenza CC BY 4.0"

# nomi delle immagini quando non coincidono con i nomi libretro della piattaforma
EXTRA = {
    "wiiu": ["Nintendo - Wii U"], "switch": ["Nintendo - Switch", "Nintendo - Nintendo Switch"],
    "ps3": ["Sony - PlayStation 3 (PSN)"], "ps4": ["Sony - PlayStation 4"], "ps5": ["Sony - PlayStation 5"],
    "psvita": ["Sony - PlayStation Vita"],
    "xbox360": ["Microsoft - Xbox 360"], "xbox": ["Microsoft - Xbox"], "atomiswave": ["Atomiswave"],
    "atari800": ["Atari - 8-bit Family"], "gameandwatch": ["Nintendo - Game & Watch"], "neogeo": ["SNK - Neo Geo"],
    "arcade": ["MAME"], "fbneo": ["FBNeo - Arcade Games"], "cps1": ["MAME"], "cps2": ["MAME"], "cps3": ["MAME"],
    "dos": ["DOS"], "megadrive": ["Sega - Mega Drive - Genesis"],
}
STORE_SITES = {
    "steam": ["https://store.steampowered.com"], "epic": ["https://store.epicgames.com", "https://www.epicgames.com"],
    "gog": ["https://www.gog.com"], "amazon": ["https://gaming.amazon.com"],
    "battlenet": ["https://shop.battle.net", "https://battle.net"], "xbox": ["https://www.xbox.com"],
    "riot": ["https://www.riotgames.com"],
}
STORE_BADGE = {"steam": ("S", "#1b2838"), "epic": ("E", "#2a2a2a"), "gog": ("G", "#6e2a9b"), "amazon": ("A", "#ff9900"),
               "battlenet": ("B", "#148eff"), "xbox": ("X", "#107c10"), "riot": ("R", "#d13639"), "minecraft": ("M", "#5b8731"),
               "manual": ("PC", "#3b4a63"), "ports": ("P", "#c0562b"), "online": ("🌐", "#2d5a8c")}

PC_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#5b8cff"/><stop offset="1" stop-color="#7c5cff"/></linearGradient></defs>
<rect x="10" y="18" width="108" height="72" rx="8" fill="#1c2030" stroke="url(#g)" stroke-width="6"/>
<rect x="20" y="28" width="88" height="52" rx="3" fill="url(#g)" opacity=".85"/>
<path d="M52 90h24l6 18H46z" fill="#2a3045"/><rect x="34" y="106" width="60" height="8" rx="4" fill="#3a4156"/></svg>"""

_LOCK = threading.Semaphore(3)
_ICON_LINK = re.compile(r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]*>', re.I)
_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)


def badge_svg(text: str, color: str) -> str:
    size = 64 if len(text) == 1 else 44
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><rect width="128" height="128" rx="28" fill="{color}"/>'
            f'<text x="64" y="64" dy=".35em" text-anchor="middle" font-family="Segoe UI,Arial" font-weight="800" '
            f'font-size="{size}" fill="#fff">{text}</text></svg>')


def _sniff(data: bytes) -> str | None:
    from ..net import sniff
    if data[:4] == b"\x00\x00\x01\x00":
        return "ico"
    head = data[:300].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "svg"
    return sniff(data[:16])


class Icons:
    def __init__(self, cache_folder):
        self.dir = Path(cache_folder) / "icons"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _cached(self, key: str) -> Path | None:
        for f in self.dir.glob(key + ".*"):
            if f.suffix == ".none":
                if time.time() - f.stat().st_mtime < 7 * 86400:
                    return f  # non disponibile (si riprova dopo una settimana)
                f.unlink(missing_ok=True)
                return None
            return f
        return None

    def _save(self, key: str, data: bytes | None, ext: str | None) -> Path:
        if not data or not ext:
            p = self.dir / f"{key}.none"
            p.touch()
            return p
        p = self.dir / f"{key}.{ext}"
        p.write_bytes(data)
        return p

    @staticmethod
    def _get(client, url):
        try:
            r = client.get(url, timeout=15, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 64:
                ext = _sniff(r.content)
                if ext:
                    return r.content, ext
        except httpx.HTTPError:
            pass
        return None, None

    def platform(self, pid: str) -> Path | None:
        if pid == "windows":
            p = self.dir / "platform_windows.svg"
            if not p.exists():
                p.write_text(PC_SVG, encoding="utf-8")
            return p
        key = f"platform_{pid}"
        hit = self._cached(key)
        if hit:
            return None if hit.suffix == ".none" else hit
        plat = P.PLATFORMS.get(pid)
        names = EXTRA.get(pid, []) + (list(plat.libretro) + [plat.name] if plat else [])
        with _LOCK, httpx.Client(headers={"User-Agent": "PozzoLauncher"}) as c:
            for n in dict.fromkeys(names):
                data, ext = self._get(c, ASSETS + quote(n + ".png"))
                if data:
                    return self._save(key, data, ext)
        self._save(key, None, None)
        return None

    def site(self, url: str, key: str | None = None, badge: tuple[str, str] | None = None) -> Path | None:
        """Icona di un sito (store PC o store online di una console)."""
        host = urlparse(url).netloc or url
        key = key or "site_" + hashlib.sha1(host.encode()).hexdigest()[:12]
        hit = self._cached(key)
        if hit and hit.suffix != ".none":
            return hit
        if not hit:
            base = f"{urlparse(url).scheme or 'https'}://{host}"
            with _LOCK, httpx.Client(headers={"User-Agent": "Mozilla/5.0 PozzoLauncher"}) as c:
                for path in ("/apple-touch-icon.png", "/favicon.ico"):
                    data, ext = self._get(c, base + path)
                    if data:
                        return self._save(key, data, ext)
                try:  # icone dichiarate nella pagina principale
                    page = c.get(base + "/", timeout=15, follow_redirects=True).text
                    links = [_HREF.search(l).group(1) for l in _ICON_LINK.findall(page) if _HREF.search(l)]
                    links.sort(key=lambda h: ("apple" not in h, ".svg" in h))
                    for h in links[:4]:
                        data, ext = self._get(c, urljoin(base + "/", h))
                        if data:
                            return self._save(key, data, ext)
                except httpx.HTTPError:
                    pass
            self._save(key, None, None)
        if badge:  # il sito non fornisce un'icona: badge disegnato
            p = self.dir / f"{key}_badge.svg"
            if not p.exists():
                p.write_text(badge_svg(*badge), encoding="utf-8")
            return p
        return None

    def store(self, sid: str) -> Path | None:
        if sid == "minecraft":
            p = self.platform_asset("minecraft", ["Minecraft"])
            if p:
                return p
        badge = STORE_BADGE.get(sid, (sid[:1].upper(), "#3b4a63"))
        for site in STORE_SITES.get(sid, []):
            p = self.site(site, key=f"store_{sid}" if site == STORE_SITES[sid][0] else f"store_{sid}_alt")
            if p and "_badge" not in p.name:
                return p
        p = self.dir / f"store_{sid}_badge.svg"
        if not p.exists():
            p.write_text(badge_svg(*badge), encoding="utf-8")
        return p

    def platform_asset(self, key: str, names: list[str]) -> Path | None:
        hit = self._cached("asset_" + key)
        if hit:
            return None if hit.suffix == ".none" else hit
        with _LOCK, httpx.Client() as c:
            for n in names:
                data, ext = self._get(c, ASSETS + quote(n + ".png"))
                if data:
                    return self._save("asset_" + key, data, ext)
        self._save("asset_" + key, None, None)
        return None
