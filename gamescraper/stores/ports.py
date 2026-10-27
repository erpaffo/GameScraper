"""Port PC installati (vedi gamescraper/ports.py): diventano giochi PC con tempo di gioco, Big Picture, telefono."""

from __future__ import annotations

from pathlib import Path

from .base import StoreGame, StoreProvider


class Ports(StoreProvider):
    id = "ports"
    name = "Port PC"

    def detected(self) -> bool:
        return True

    def account(self) -> dict:
        return {"connected": True}

    async def fetch(self) -> list[StoreGame]:
        from .. import ports
        games = []
        for slug, m in ports.all_installed(self.cfg).items():
            folder = Path(m["folder"])
            exe = folder / m["exe"] if m.get("exe") else None
            art = m.get("art") or {}
            games.append(StoreGame(
                store=self.id, id=slug, title=m.get("name") or slug, installed=bool(exe and exe.is_file()),
                install_dir=str(folder), description=m.get("description") or "", developer=m.get("developer") or "",
                launch={"type": "exe", "target": str(exe), "args": [], "cwd": str(exe.parent)} if exe else {},
                page=f"https://github.com/{m['repository']}" if m.get("repository") else "",
                images={k: v for k, v in {"cover": art.get("capsule"), "hero": art.get("hero"), "logo": art.get("logo"),
                                          "header": art.get("header")}.items() if v}))
        return games
