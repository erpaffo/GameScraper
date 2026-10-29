"""GOG.com: installati dal registro di Windows, posseduti tramite login all'account GOG.

Senza GOG Galaxy, "Installa" apre la pagina dei download dell'account GOG (i giochi GOG
si installano con gli installer offline); con Galaxy apre direttamente il gioco in Galaxy.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs, quote, urlparse

from ..net import request
from .base import StoreGame, StoreProvider, reg_get, reg_subkeys

CLIENT_ID = "46899977096215655"
CLIENT_SECRET = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
REDIRECT = "https://embed.gog.com/on_login_success?origin=client"
LOGIN_URL = (f"https://auth.gog.com/auth?client_id={CLIENT_ID}&redirect_uri={quote(REDIRECT, safe='')}"
             "&response_type=code&layout=client2")
REG = r"SOFTWARE\WOW6432Node\GOG.com\Games"


class Gog(StoreProvider):
    id = "gog"
    name = "GOG.com"
    login = "oauth"
    login_url = LOGIN_URL

    def galaxy(self) -> str | None:
        return reg_get("HKLM", r"SOFTWARE\WOW6432Node\GOG.com\GalaxyClient\paths", "client")

    def detected(self) -> bool:
        return bool(reg_subkeys("HKLM", REG)) or bool(self.galaxy()) or bool(self.cache.kv_get("auth:gog"))

    def account(self) -> dict:
        a = self.cache.kv_get("auth:gog") or {}
        return {"connected": bool(a.get("refresh_token")), "user": a.get("user", ""), "galaxy": bool(self.galaxy())}

    @staticmethod
    def extract_code(text: str) -> str:
        """Accetta l'indirizzo finale del login (con code=...) o il solo codice."""
        import re
        text = (text or "").strip()
        m = re.search(r"[?&]code=([^&\s#]+)", text)
        return m.group(1) if m else text

    async def _token(self, params: dict) -> dict:
        r = await request(self.client, "GET", "https://auth.gog.com/token", ok404=False,
                          params={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, **params})
        if r is None or r.status_code != 200:
            raise RuntimeError(f"GOG: accesso non riuscito ({r.text[:150] if r is not None else 'nessuna risposta'})")
        d = r.json()
        prev = self.cache.kv_get("auth:gog") or {}
        self.cache.kv_set("auth:gog", {"refresh_token": d["refresh_token"], "user_id": d.get("user_id"),
                                       "user": prev.get("user", ""), "time": time.time()})
        return d

    async def finish_login(self, code_or_url: str):
        code = self.extract_code(code_or_url)
        if not code:
            raise RuntimeError("Codice di autorizzazione GOG mancante")
        d = await self._token({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT})
        r = await request(self.client, "GET", "https://embed.gog.com/userData.json",
                          headers={"Authorization": f"Bearer {d['access_token']}"})
        name = r.json().get("username", "") if r is not None and r.status_code == 200 else ""
        a = self.cache.kv_get("auth:gog") or {}
        a["user"] = name
        self.cache.kv_set("auth:gog", a)
        return name

    def installed(self) -> dict[str, StoreGame]:
        out = {}
        for gid in reg_subkeys("HKLM", REG):
            v = reg_get("HKLM", REG + "\\" + gid) or {}
            name = v.get("gameName") or v.get("GAMENAME")
            exe = v.get("exe") or v.get("EXE")
            if not name or not os.path.isdir(v.get("path") or ""):  # voce di registro rimasta senza il gioco
                continue
            launch ={"type": "exe", "target": exe, "args": (v.get("launchParam") or "").split(), "cwd": v.get("workingDir") or v.get("path", "")}
            out[gid] = StoreGame(store=self.id, id=gid, title=name, installed=True, install_dir=v.get("path", ""),
                                 launch=launch, install_uri=self._install_uri(gid, ""),
                                 page="https://www.gog.com/account")
        return out

    def _install_uri(self, gid: str, slug: str) -> str:
        if self.galaxy():
            return f"goggalaxy://openGameView/{gid}"
        return "https://www.gog.com/account"  # download degli installer dalla libreria GOG

    async def fetch(self) -> list[StoreGame]:
        games = self.installed()
        a = self.cache.kv_get("auth:gog") or {}
        if not a.get("refresh_token"):
            self.log(f"GOG: {len(games)} giochi installati (collega l'account per vedere anche quelli non installati)")
            return list(games.values())
        try:
            token = (await self._token({"grant_type": "refresh_token", "refresh_token": a["refresh_token"]}))["access_token"]
        except RuntimeError as e:
            self.log(f"{e} - ricollega l'account GOG dalle impostazioni")
            return list(games.values())
        h = {"Authorization": f"Bearer {token}"}
        products, page, pages = [], 1, 1
        while page <= pages and page <= 100:
            r = await request(self.client, "GET", "https://embed.gog.com/account/getFilteredProducts",
                              params={"mediaType": 1, "page": page}, headers=h)
            if r is None or r.status_code != 200:
                break
            d = r.json()
            products += d.get("products", [])
            pages = d.get("totalPages", 1)
            page += 1
        sem = asyncio.Semaphore(8)

        async def boxart(pid):
            async with sem:
                r = await request(self.client, "GET", f"https://api.gog.com/v2/games/{pid}", params={"locale": "it-IT"})
                if r is None or r.status_code != 200:
                    return {}
                links = r.json().get("_links", {})
                return {"cover": (links.get("boxArtImage") or {}).get("href"),
                        "hero": (links.get("backgroundImage") or {}).get("href"),
                        "logo": (links.get("logo") or {}).get("href"),
                        "icon": (links.get("iconSquare") or {}).get("href")}

        arts = await asyncio.gather(*(boxart(p["id"]) for p in products))
        for p, art in zip(products, arts):
            gid = str(p["id"])
            g = games.get(gid) or StoreGame(store=self.id, id=gid, title=p.get("title", ""),
                                            install_uri=self._install_uri(gid, p.get("slug", "")),
                                            launch={"type": "uri", "target": f"goggalaxy://openGameView/{gid}"} if self.galaxy() else {})
            g.title = p.get("title") or g.title
            # 1 = gioco, 3 = pack (DLC, artbook, contenuti extra): lo dice GOG
            g.kind = "game" if p.get("type") in (None, 1) else "dlc"
            g.page = "https://www.gog.com" + p.get("url", "") if p.get("url") else g.page
            img = {k: v for k, v in art.items() if v}
            if not img.get("cover") and p.get("image"):
                img["header"] = "https:" + p["image"] + "_product_tile_extended_432x243.jpg"
            g.images = img
            games[gid] = g
        self.log(f"GOG: {len(products)} giochi posseduti, {sum(g.installed for g in games.values())} installati")
        return list(games.values())
