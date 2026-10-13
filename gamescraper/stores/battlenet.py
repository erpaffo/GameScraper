"""Battle.net: installati dal registro di Windows.

Blizzard non offre un modo pubblico per leggere la libreria posseduta: tra i "non installati"
compaiono i giochi gratuiti (che chiunque puo' installare) e quelli gia' installati in passato
su questo PC (dalla configurazione di Battle.net).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .base import StoreGame, StoreProvider, reg_get, reg_subkeys

# uid usato da Battle.net -> (codice per battlenet://, titolo, gratuito)
PRODUCTS = {
    "wow": ("WoW", "World of Warcraft", False),
    "wow_classic": ("WoWC", "World of Warcraft Classic", False),
    "d3": ("D3", "Diablo III", False),
    "fenris": ("Fen", "Diablo IV", False),
    "osi": ("OSI", "Diablo II: Resurrected", False),
    "anbs": ("ANBS", "Diablo Immortal", True),
    "hs_beta": ("WTCG", "Hearthstone", True),
    "heroes": ("Hero", "Heroes of the Storm", True),
    "pro": ("Pro", "Overwatch 2", True),
    "s1": ("S1", "StarCraft: Remastered", False),
    "s2": ("S2", "StarCraft II", True),
    "w3": ("W3", "Warcraft III: Reforged", False),
    "gryphon": ("GRY", "Warcraft Rumble", True),
}
UNINSTALL = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
CONFIG = Path(os.path.expandvars(r"%APPDATA%\Battle.net\Battle.net.config"))


class BattleNet(StoreProvider):
    id = "battlenet"
    name = "Battle.net"
    owned_note = "Blizzard non permette di leggere la libreria: tra i non installati ci sono i giochi gratuiti e quelli usati in passato"

    def client_exe(self) -> str | None:
        for sub in reg_subkeys("HKLM", UNINSTALL):
            v = reg_get("HKLM", UNINSTALL + "\\" + sub) or {}
            if v.get("DisplayName") == "Battle.net" and v.get("InstallLocation"):
                for exe in ("Battle.net Launcher.exe", "Battle.net.exe"):
                    p = Path(v["InstallLocation"]) / exe
                    if p.exists():
                        return str(p)
        return None

    def detected(self) -> bool:
        return bool(self.client_exe()) or CONFIG.exists()

    def account(self) -> dict:
        return {"connected": self.detected(), "note": self.owned_note}

    def _game(self, uid: str, installed: bool, install_dir: str = "") -> StoreGame:
        code, title, _ = PRODUCTS[uid]
        exe = self.client_exe()
        launch = ({"type": "exe", "target": exe, "args": [f"--exec=launch {code}"], "cwd": str(Path(exe).parent)}
                  if exe and installed else {"type": "uri", "target": f"battlenet://{code}"})
        return StoreGame(store=self.id, id=uid, title=title, installed=installed, install_dir=install_dir,
                         launch=launch, install_uri=f"battlenet://{code}", page="https://shop.battle.net/")

    async def fetch(self) -> list[StoreGame]:
        games: dict[str, StoreGame] = {}
        for sub in reg_subkeys("HKLM", UNINSTALL):
            v = reg_get("HKLM", UNINSTALL + "\\" + sub) or {}
            if "Blizzard" not in str(v.get("Publisher", "")):
                continue
            m = re.search(r"--uid=([\w-]+)", str(v.get("UninstallString", "")))
            if m and m.group(1) in PRODUCTS:
                games[m.group(1)] = self._game(m.group(1), True, v.get("InstallLocation", ""))
        known = set()
        if CONFIG.exists():
            try:
                known = set((json.loads(CONFIG.read_text(encoding="utf-8")).get("Games") or {}).keys())
            except ValueError:
                pass
        for uid, (_, _, free) in PRODUCTS.items():
            if uid not in games and (free or uid in known):
                games[uid] = self._game(uid, False)
        self.log(f"Battle.net: {sum(g.installed for g in games.values())} installati, "
                 f"{sum(not g.installed for g in games.values())} disponibili")
        return list(games.values())
