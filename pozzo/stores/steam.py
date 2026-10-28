"""Steam: installati dai manifest locali (tutte le librerie), posseduti tramite Steam Web API.

La chiave Web API e' gratuita: https://steamcommunity.com/dev/apikey . Lo SteamID viene
rilevato dall'account usato piu' di recente (config/loginusers.vdf), oppure impostato a mano.
"""

from __future__ import annotations

from pathlib import Path

from ..net import request
from .base import StoreGame, StoreProvider, parse_vdf, reg_get

CDN = "https://cdn.akamai.steamstatic.com/steam/apps/{id}/"
# strumenti e redistribuibili che Steam considera "app" ma non sono giochi
SKIP_APPS = {"228980", "1007", "1070560", "1391110", "1628350", "1826330", "2180100", "961940", "1493710",
             "1580130", "1887720", "2348590", "2805730", "250820", "1113280"}
SKIP_WORDS = ("redistributable", "steamworks", "proton ", "steam linux runtime", "soundtrack", "dedicated server",
              "sdk", "benchmark")


def images(appid: str) -> dict:
    base = CDN.format(id=appid)
    return {"cover": base + "library_600x900_2x.jpg", "hero": base + "library_hero.jpg", "logo": base + "logo.png",
            "header": base + "header.jpg"}


class Steam(StoreProvider):
    id = "steam"
    name = "Steam"
    login = "apikey"

    def path(self) -> Path | None:
        p = self.settings.get("path") or reg_get("HKCU", r"Software\Valve\Steam", "SteamPath")
        return Path(p) if p and Path(p).exists() else None

    def detected(self) -> bool:
        return self.path() is not None

    def steamid(self) -> str:
        if self.settings.get("steamid"):
            return str(self.settings["steamid"])
        p = self.path()
        if not p or not (p / "config" / "loginusers.vdf").exists():
            return ""
        users = parse_vdf((p / "config" / "loginusers.vdf").read_text(encoding="utf-8", errors="ignore")).get("users", {})
        recent = [sid for sid, u in users.items() if u.get("MostRecent") == "1"] or list(users)
        return recent[0] if recent else ""

    def account(self) -> dict:
        sid = self.steamid()
        users = {}
        p = self.path()
        if p and (p / "config" / "loginusers.vdf").exists():
            users = parse_vdf((p / "config" / "loginusers.vdf").read_text(encoding="utf-8", errors="ignore")).get("users", {})
        return {"steamid": sid, "persona": users.get(sid, {}).get("PersonaName", ""),
                "accounts": [{"steamid": k, "persona": v.get("PersonaName", "")} for k, v in users.items()],
                "has_key": bool(self.settings.get("api_key")), "connected": bool(self.settings.get("api_key") and sid)}

    def libraries(self) -> list[Path]:
        p = self.path()
        if not p:
            return []
        libs = [p]
        vdf = p / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            data = parse_vdf(vdf.read_text(encoding="utf-8", errors="ignore")).get("libraryfolders", {})
            for v in data.values():
                if isinstance(v, dict) and v.get("path"):
                    lp = Path(v["path"])
                    if lp.exists() and lp not in libs:
                        libs.append(lp)
        return libs

    def installed(self) -> dict[str, StoreGame]:
        out = {}
        for lib in self.libraries():
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                st = parse_vdf(acf.read_text(encoding="utf-8", errors="ignore")).get("AppState", {})
                appid, name = st.get("appid"), st.get("name")
                if not appid or not name or appid in SKIP_APPS or any(w in name.lower() for w in SKIP_WORDS):
                    continue
                full = int(st.get("StateFlags", "0") or 0) & 4 == 4
                out[appid] = self._game(appid, name, installed=full,
                                        install_dir=str(lib / "steamapps" / "common" / st.get("installdir", "")),
                                        last_played=float(st.get("LastPlayed") or 0))
                out[appid].size = int(st.get("SizeOnDisk") or 0)
        return out

    def _game(self, appid, name, installed=False, install_dir="", playtime=0.0, last_played=0.0) -> StoreGame:
        return StoreGame(
            store=self.id, id=str(appid), title=name, installed=installed, install_dir=install_dir,
            launch={"type": "uri", "target": f"steam://rungameid/{appid}"},
            install_uri=f"steam://install/{appid}", page=f"https://store.steampowered.com/app/{appid}",
            playtime=playtime, last_played=last_played, images=images(str(appid)))

    async def fetch(self) -> list[StoreGame]:
        games = self.installed()
        key, sid = self.settings.get("api_key"), self.steamid()
        if key and sid:
            r = await request(self.client, "GET", "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
                              params={"key": key, "steamid": sid, "include_appinfo": 1,
                                      "include_played_free_games": 1, "format": "json"})
            if r is None or r.status_code != 200:
                self.log(f"Steam: libreria posseduta non disponibile (HTTP {r.status_code if r else '?'}). "
                         "Controlla la chiave API e che i dettagli dei giochi del profilo siano visibili.")
            else:
                owned = r.json().get("response", {}).get("games", [])
                for o in owned:
                    appid, name = str(o["appid"]), o.get("name", "")
                    if appid in SKIP_APPS or not name or any(w in name.lower() for w in SKIP_WORDS):
                        continue
                    g = games.get(appid) or self._game(appid, name)
                    g.playtime = float(o.get("playtime_forever", 0)) * 60
                    g.last_played = max(g.last_played, float(o.get("rtime_last_played") or 0))
                    games[appid] = g
                self.log(f"Steam: {len(owned):,} giochi posseduti, {sum(g.installed for g in games.values())} installati")
        else:
            self.log(f"Steam: {len(games)} giochi installati (per vedere anche quelli non installati collega la chiave API)")
        # niente recensioni online: i metadati degli store vengono solo dalle fonti offline
        return list(games.values())

    async def _reviews(self, games: list[StoreGame]):
        """Valutazione dalle recensioni degli utenti Steam (percentuale positive, numero di recensioni).
        Una richiesta per gioco a ritmo lento (policy steam_reviews), risposta riusata per 14 giorni."""
        import asyncio as _a

        from .. import net
        from ..net import RateLimiter
        from ..policy import Ledger, policy_for
        own_cache = net.HTTP_CACHE is None
        if own_cache:
            net.HTTP_CACHE = net.HttpCache(self.cache.folder / "cache.db")
        ledger = Ledger(self.cache.folder / "cache.db")
        lim = RateLimiter(1, 1).guard(policy_for(self.cfg, "steam_reviews"), ledger, log=self.log)
        done = 0

        async def one(g):
            nonlocal done
            try:
                r = await request(self.client, "GET", f"https://store.steampowered.com/appreviews/{g.id}", limiter=lim,
                                  cache_ttl=14 * 86400, params={"json": 1, "language": "all", "purchase_type": "all",
                                                                "num_per_page": 0, "filter": "summary"})
            except Exception:
                return
            if r is None or r.status_code != 200:
                return
            q = (r.json() or {}).get("query_summary") or {}
            total = q.get("total_reviews") or 0
            if total:
                g.reviews = {"rating": round(q.get("total_positive", 0) / total, 3), "rating_count": total,
                             "label": q.get("review_score_desc", "")}
                done += 1

        try:
            await _a.gather(*(one(g) for g in games))
        finally:
            ledger.close()
            if own_cache:
                net.HTTP_CACHE.close()
                net.HTTP_CACHE = None
        if done:
            self.log(f"Steam: valutazioni dalle recensioni per {done} giochi")
