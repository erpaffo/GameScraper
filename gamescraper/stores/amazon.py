"""Amazon Games (anche i giochi di Prime Gaming): tutto in locale, senza login.

I giochi posseduti sono nel database dell'app Amazon Games, cifrati con DPAPI di Windows:
si possono decifrare solo dall'utente Windows che usa l'app (cioe' tu, su questo PC).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .base import StoreGame, StoreProvider, dpapi_decrypt

SQL = Path(os.path.expandvars(r"%LOCALAPPDATA%\Amazon Games\Data\Games\Sql"))


class Amazon(StoreProvider):
    id = "amazon"
    name = "Amazon Games"

    def detected(self) -> bool:
        return (SQL / "Entitlements.sqlite").exists() or (SQL / "GameInstallInfo.sqlite").exists()

    def account(self) -> dict:
        return {"connected": self.detected(), "note": "letto dall'app Amazon Games, nessun login necessario"}

    @staticmethod
    def _read(db: Path, sql: str):
        if not db.exists():
            return []
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                return con.execute(sql).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return []

    async def fetch(self) -> list[StoreGame]:
        games: dict[str, StoreGame] = {}
        for (val,) in self._read(SQL / "Entitlements.sqlite", "SELECT value FROM game_entitlements"):
            raw = dpapi_decrypt(val) if isinstance(val, bytes) else (val or "").encode()
            try:
                e = json.loads(raw or b"{}")
            except ValueError:
                continue
            gid, title = e.get("Id") or e.get("ProductIdStr"), e.get("ProductTitle")
            if not gid or not title:
                continue
            games[gid] = StoreGame(
                store=self.id, id=gid, title=title,
                launch={"type": "uri", "target": f"amazon-games://play/{gid}"},
                install_uri=f"amazon-games://play/{gid}", page="https://gaming.amazon.com/",
                images={"icon": e["ProductIconUrl"]} if e.get("ProductIconUrl") else {},
                genres=[g for g in (e.get("Genres") or []) if isinstance(g, str)])
        by_title = {g.title.casefold(): g for g in games.values()}
        for gid, title, inst, path in self._read(SQL / "GameInstallInfo.sqlite",
                                                 "SELECT Id, ProductTitle, Installed, InstallDirectory FROM DbSet"):
            # il database degli installati usa un id diverso da quello dei posseduti: si unisce per titolo,
            # e si usa l'id degli installati per avviare (e' quello che l'app Amazon riconosce)
            same = by_title.get((title or "").casefold())
            if same and gid not in games:
                same.launch = {"type": "uri", "target": f"amazon-games://play/{gid}"}
                same.installed, same.install_dir = bool(inst), path or ""
                continue
            g = games.get(gid) or StoreGame(store=self.id, id=gid, title=title,
                                            launch={"type": "uri", "target": f"amazon-games://play/{gid}"},
                                            install_uri=f"amazon-games://play/{gid}", page="https://gaming.amazon.com/")
            g.installed = bool(inst)
            g.install_dir = path or ""
            games[gid] = g
        self.log(f"Amazon Games: {len(games)} giochi posseduti, {sum(g.installed for g in games.values())} installati")
        return list(games.values())
