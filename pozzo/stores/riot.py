"""Riot Games: installati dal registro di Windows (chiavi «Riot Game <prodotto>.<patchline>» dell'utente).

Riot non offre un modo pubblico per leggere la libreria, ma i suoi giochi sono tutti gratuiti:
con il Riot Client installato tra i "non installati" compaiono gli altri giochi Riot.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .base import StoreGame, StoreProvider, reg_get, reg_subkeys

# prodotto del Riot Client -> (titolo, generi, anno, descrizione, pagina ufficiale)
PRODUCTS = {
    "league_of_legends": ("League of Legends", ["MOBA"], "2009",
                          "Il MOBA 5 contro 5 di Riot Games.", "https://www.leagueoflegends.com/"),
    "teamfighttactics": ("Teamfight Tactics", ["Strategia", "Auto battler"], "2019",
                         "Auto battler con i campioni di League of Legends.", "https://teamfighttactics.leagueoflegends.com/"),
    "valorant": ("VALORANT", ["Sparatutto in prima persona"], "2020",
                 "Sparatutto tattico 5 contro 5 con agenti dotati di abilità.", "https://playvalorant.com/"),
    "bacon": ("Legends of Runeterra", ["Carte collezionabili"], "2020",
              "Gioco di carte strategico ambientato nell'universo di League of Legends.", "https://playruneterra.com/"),
    "lion": ("2XKO", ["Picchiaduro"], "2025",
             "Picchiaduro a squadre 2 contro 2 con i campioni di League of Legends.", "https://2xko.riotgames.com/"),
}
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
INSTALLS = Path(os.path.expandvars(r"%PROGRAMDATA%\Riot Games\RiotClientInstalls.json"))
_ARGS = re.compile(r"--uninstall-product=([\w-]+)\s+--uninstall-patchline=(\w*)")


class Riot(StoreProvider):
    id = "riot"
    name = "Riot Games"
    owned_note = "Riot non permette di leggere la libreria: tra i non installati ci sono gli altri giochi Riot (tutti gratuiti)"

    def client_exe(self) -> str | None:
        try:
            exe = json.loads(INSTALLS.read_text(encoding="utf-8")).get("rc_default")
        except (OSError, ValueError):
            exe = None
        return str(Path(exe)) if exe and Path(exe).exists() else None

    def detected(self) -> bool:
        return bool(self.client_exe())

    def account(self) -> dict:
        return {"connected": self.detected(), "note": self.owned_note}

    def _game(self, pid: str, installed: bool, install_dir: str = "") -> StoreGame:
        title, genres, year, desc, page = PRODUCTS[pid]
        exe = self.client_exe()
        # il Riot Client avvia il gioco (o, se manca, apre la sua pagina con il pulsante Installa)
        launch = ({"type": "exe", "target": exe, "args": [f"--launch-product={pid}", "--launch-patchline=live"],
                   "cwd": str(Path(exe).parent)} if exe else {})
        return StoreGame(store=self.id, id=pid, title=title, installed=installed, install_dir=install_dir,
                         genres=genres, release_date=year, description=desc, developer="Riot Games",
                         publisher="Riot Games", launch=launch, install_uri=page, page=page)

    async def fetch(self) -> list[StoreGame]:
        games: dict[str, StoreGame] = {}
        for sub in reg_subkeys("HKCU", UNINSTALL):
            if not sub.startswith("Riot Game "):
                continue
            v = reg_get("HKCU", UNINSTALL + "\\" + sub) or {}
            m = _ARGS.search(str(v.get("UninstallString", "")))
            if m and m.group(1) in PRODUCTS and m.group(2) == "live":  # pbe = server di prova
                loc = v.get("InstallLocation")
                games[m.group(1)] = self._game(m.group(1), True, str(Path(loc)) if loc else "")
        if self.detected():
            for pid in PRODUCTS:
                games.setdefault(pid, self._game(pid, False))
        self.log(f"Riot Games: {sum(g.installed for g in games.values())} installati, "
                 f"{sum(not g.installed for g in games.values())} disponibili")
        return list(games.values())
