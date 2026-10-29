"""Giochi PC aggiunti a mano (qualsiasi .exe) e giochi browser (URL): salvati nei dati utente, non toccati dalle ricostruzioni."""

from __future__ import annotations

from pathlib import Path

from .base import StoreGame, StoreProvider


class Manual(StoreProvider):
    id = "manual"
    name = "Aggiunti a mano"

    def detected(self) -> bool:
        return True

    def account(self) -> dict:
        return {"connected": True}

    async def fetch(self) -> list[StoreGame]:
        from ..library.userdata import UserData
        ud = UserData(Path(self.cfg.cache) / "userdata.db")
        games = []
        for m in ud.manual_games():
            if m["kind"] == "browser":  # exe = URL; si apre nel browser predefinito come un URI di store
                meta = m.get("meta") or {}
                games.append(StoreGame(
                    store=self.id, id=str(m["id"]), title=m["title"], installed=True, page=m["exe"],
                    description=meta.get("description") or "", images={"cover": meta["image"]} if meta.get("image") else {},
                    launch={"type": "uri", "target": m["exe"]}))
                continue
            exe = Path(m["exe"])
            games.append(StoreGame(
                store=self.id, id=str(m["id"]), title=m["title"], installed=exe.exists(), install_dir=str(exe.parent),
                kind=m["kind"],
                launch={"type": "exe", "target": str(exe), "args": m["args"], "cwd": m.get("cwd") or str(exe.parent)}))
        return games
