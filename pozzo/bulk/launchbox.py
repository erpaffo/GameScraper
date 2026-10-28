"""LaunchBox Games Database (gamesdb.launchbox-app.com): ~190.000 giochi, 127.000 con valutazione
della community, descrizioni, sviluppatori, generi, ESRB, video, 1,3 milioni di immagini.

Un solo download settimanale (Metadata.zip, ~110 MB) e poi tutto e' locale: e' la fonte ideale per
le valutazioni di decine di migliaia di giochi senza fare una richiesta per gioco. Le immagini si
scaricano dal CDN di LaunchBox con un ritmo moderato (vedi policy "launchbox_images").
"""

from __future__ import annotations

import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from ..matching import normalize
from .base import BulkDB

IMG_BASE = "https://images.launchbox-app.com/"

# piattaforma LaunchBox -> id PozzoLauncher
PLATFORMS = {
    "3DO Interactive Multiplayer": "3do", "Nintendo Entertainment System": "nes", "Super Nintendo Entertainment System": "snes",
    "Nintendo 64": "n64", "Nintendo Game Boy": "gb", "Nintendo Game Boy Color": "gbc", "Nintendo Game Boy Advance": "gba",
    "Nintendo DS": "nds", "Nintendo 3DS": "3ds", "Nintendo GameCube": "gc", "Nintendo Wii": "wii", "Nintendo Wii U": "wiiu",
    "Nintendo Switch": "switch", "Nintendo Virtual Boy": "virtualboy", "Nintendo Famicom Disk System": "fds",
    "Nintendo Pokemon Mini": "pokemini", "Nintendo Game & Watch": "gameandwatch",
    "Sega Genesis": "megadrive", "Sega Mega Drive": "megadrive", "Sega Master System": "mastersystem", "Sega Game Gear": "gamegear",
    "Sega 32X": "sega32x", "Sega CD": "segacd", "Sega Saturn": "saturn", "Sega Dreamcast": "dreamcast", "Sega SG-1000": "sg-1000",
    "Sega Naomi": "naomi", "Sammy Atomiswave": "atomiswave",
    "Sony Playstation": "psx", "Sony Playstation 2": "ps2", "Sony Playstation 3": "ps3", "Sony Playstation 4": "ps4",
    "Sony Playstation 5": "ps5",
    "Sony PSP": "psp", "Sony Playstation Vita": "psvita",
    "Microsoft Xbox": "xbox", "Microsoft Xbox 360": "xbox360", "Windows": "windows", "MS-DOS": "dos",
    "Arcade": "arcade", "SNK Neo Geo AES": "neogeo", "SNK Neo Geo MVS": "neogeo", "SNK Neo Geo CD": "neogeocd",
    "SNK Neo Geo Pocket": "ngp", "SNK Neo Geo Pocket Color": "ngpc", "WonderSwan": "wonderswan", "WonderSwan Color": "wonderswancolor",
    "NEC TurboGrafx-16": "pcengine", "NEC TurboGrafx-CD": "pcenginecd", "NEC PC-FX": "pcfx", "NEC PC-9801": "pc98",
    "Atari 2600": "atari2600", "Atari 5200": "atari5200", "Atari 7800": "atari7800", "Atari Lynx": "atarilynx",
    "Atari Jaguar": "atarijaguar", "Atari Jaguar CD": "atarijaguarcd", "Atari ST": "atarist", "Atari 800": "atari800",
    "ColecoVision": "coleco", "Mattel Intellivision": "intellivision", "GCE Vectrex": "vectrex", "Magnavox Odyssey 2": "videopac",
    "Fairchild Channel F": "channelf", "Commodore 64": "c64", "Commodore VIC-20": "vic20", "Commodore Amiga": "amiga",
    "Commodore Amiga CD32": "amigacd32", "Microsoft MSX": "msx", "Microsoft MSX2": "msx2", "Amstrad CPC": "amstradcpc",
    "Sinclair ZX Spectrum": "zxspectrum", "Sinclair ZX-81": "zx81", "Sharp X68000": "x68000", "ScummVM": "scummvm",
}
# le piattaforme arcade di PozzoLauncher cercano tutte nella piattaforma "Arcade" di LaunchBox
ALIASES = {"fbneo": "arcade", "cps1": "arcade", "cps2": "arcade", "cps3": "arcade", "mame": "arcade"}

IMAGE_TYPES = {
    "Box - Front": "cover", "Box - Front - Reconstructed": "cover", "Fanart - Box - Front": "cover",
    "Screenshot - Gameplay": "screenshot", "Screenshot - Game Title": "title", "Clear Logo": "wheel",
    "Fanart - Background": "fanart", "Box - 3D": "box3d", "Arcade - Marquee": "marquee", "Banner": "banner",
}
REGION_MAP = {"North America": "us", "United States": "us", "Europe": "eu", "World": "wor", "Japan": "jp", "Italy": "it",
              "United Kingdom": "uk", "Germany": "de", "France": "fr", "Spain": "sp", "Australia": "au", "Korea": "kr",
              "Brazil": "br", "Asia": "asi", None: ""}

SCHEMA = """
CREATE TABLE games(id INTEGER PRIMARY KEY, pid TEXT, platform TEXT, name TEXT, norm TEXT, release TEXT,
                   developer TEXT, publisher TEXT, genres TEXT, rating REAL, rating_count INT, players TEXT,
                   esrb TEXT, overview TEXT, video TEXT, wiki TEXT, steam_appid TEXT);
CREATE TABLE alt(norm TEXT, id INT, region TEXT);
CREATE TABLE files(pid TEXT, file TEXT, name TEXT);
CREATE TABLE images(id INT, kind TEXT, region TEXT, file TEXT);
"""
INDEXES = """
CREATE INDEX g_pid_norm ON games(pid, norm);
CREATE INDEX g_steam ON games(steam_appid);
CREATE INDEX a_norm ON alt(norm);
CREATE INDEX f_file ON files(pid, file);
CREATE INDEX i_id ON images(id);
"""


class LaunchBoxDB(BulkDB):
    id = "launchbox"
    name = "LaunchBox Games Database"
    url = "https://gamesdb.launchbox-app.com/Metadata.zip"
    filename = "Metadata.zip"
    refresh_days = 7
    description = "190.000 giochi con valutazioni della community, descrizioni, generi, ESRB e immagini"

    def con(self) -> sqlite3.Connection:
        if self._con is None and self.ready():
            self._map_new_platforms()
        return super().con()

    def _map_new_platforms(self):
        """Console aggiunte a PLATFORMS dopo l'indicizzazione (es. PS5): le loro righe hanno pid vuoto e non si
        troverebbero fino al prossimo download (7 giorni). Si assegnano qui, senza rifare l'indice."""
        try:
            db = sqlite3.connect(self.db_path)
            try:
                todo = [p for (p,) in db.execute("SELECT DISTINCT platform FROM games WHERE pid IS NULL")
                        if p in PLATFORMS]
                if todo:
                    db.executemany("UPDATE games SET pid=? WHERE platform=? AND pid IS NULL",
                                   [(PLATFORMS[p], p) for p in todo])
                    db.commit()
            finally:
                db.close()
        except sqlite3.Error:  # database in uso o in sola lettura: si riprova alla prossima apertura
            pass

    def build_index(self, raw: Path, out: Path, log) -> int:
        db = sqlite3.connect(out)
        db.executescript(SCHEMA)
        n = 0
        games, alts, imgs = [], [], []

        def flush():
            db.executemany("INSERT OR IGNORE INTO games VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", games)
            db.executemany("INSERT INTO alt VALUES(?,?,?)", alts)
            db.executemany("INSERT INTO images VALUES(?,?,?,?)", imgs)
            games.clear(), alts.clear(), imgs.clear()

        with zipfile.ZipFile(raw) as z:
            with z.open("Metadata.xml") as f:
                for _, el in ET.iterparse(f, events=("end",)):
                    tag = el.tag
                    if tag == "Game":
                        t = lambda k: (el.findtext(k) or "").strip() or None  # noqa: E731
                        plat = t("Platform")
                        name = t("Name") or ""
                        rating = float(t("CommunityRating")) if t("CommunityRating") else None
                        rel = t("ReleaseDate") or t("ReleaseYear")
                        games.append((int(t("DatabaseID")), PLATFORMS.get(plat), plat, name, normalize(name),
                                      rel[:10] if rel else None, t("Developer"), t("Publisher"), t("Genres"),
                                      rating, int(t("CommunityRatingCount") or 0), t("MaxPlayers"), t("ESRB"),
                                      t("Overview"), t("VideoURL"), t("WikipediaURL"), t("SteamAppId")))
                        n += 1
                        el.clear()
                    elif tag == "GameAlternateName":
                        name = el.findtext("AlternateName") or ""
                        alts.append((normalize(name), int(el.findtext("DatabaseID") or 0), el.findtext("Region") or ""))
                        el.clear()
                    elif tag == "GameImage":
                        kind = IMAGE_TYPES.get(el.findtext("Type") or "")
                        if kind:
                            imgs.append((int(el.findtext("DatabaseID") or 0), kind, el.findtext("Region"), el.findtext("FileName")))
                        el.clear()
                    if len(games) + len(imgs) > 50000:
                        flush()
            flush()
            if "Files.xml" in z.namelist():
                rows = []
                with z.open("Files.xml") as f:
                    for _, el in ET.iterparse(f, events=("end",)):
                        if el.tag == "File":
                            pid = PLATFORMS.get(el.findtext("Platform") or "")
                            if pid:
                                rows.append((pid, (el.findtext("FileName") or "").lower(), el.findtext("GameName")))
                            el.clear()
                db.executemany("INSERT INTO files VALUES(?,?,?)", rows)
        db.executescript(INDEXES)
        db.commit()
        db.close()
        return n

    # ------------------------------------------------------------ ricerca
    @lru_cache(maxsize=64)
    def _norms(self, pid: str) -> tuple[list[str], list[int]]:
        rows = self.con().execute("SELECT norm, id FROM games WHERE pid=?", (pid,)).fetchall()
        return [r[0] for r in rows], [r[1] for r in rows]

    def _index(self, pid: str) -> dict:
        """Nomi file, titoli e titoli alternativi della console in memoria: una query per console invece di tre per gioco
        (la libreria ne cerca decine di migliaia per gli store online)."""
        cache = self.__dict__.setdefault("_idx", {})
        if pid not in cache:
            c = self.con()
            files, norm, alt = {}, defaultdict(list), defaultdict(list)
            for f, name in c.execute("SELECT file, name FROM files WHERE pid=?", (pid,)):
                files.setdefault(f, name)  # come fetchone(): la prima riga
            for n, gid in c.execute("SELECT norm, id FROM games WHERE pid=?", (pid,)):
                norm[n].append(gid)
            for n, gid in c.execute("SELECT a.norm, g.id FROM alt a JOIN games g ON g.id=a.id WHERE g.pid=?", (pid,)):
                alt[n].append(gid)
            cur = c.execute("SELECT * FROM games WHERE pid=?", (pid,))
            self.__dict__["_cols"] = [d[0] for d in cur.description]
            rows = {r[0]: r for r in cur.fetchall()}  # tuple (id e' la prima colonna): il dict solo per i giochi trovati
            cache[pid] = {"files": files, "norm": dict(norm), "alt": dict(alt), "rows": rows}
        return cache[pid]

    def find(self, pid: str, titles: list[str], files: list[str] = (), year: int | None = None,
             steam_appid: str | None = None, fuzzy: int = 92) -> dict | None:
        """Cerca un gioco: SteamAppId, nome file (Files.xml), nome esatto/alternativo, poi fuzzy."""
        pid = ALIASES.get(pid, pid)
        c = self.con()
        ix = self._index(pid)
        ids: list[int] = []
        if steam_appid:
            ids = [r[0] for r in c.execute("SELECT id FROM games WHERE steam_appid=?", (str(steam_appid),))]
        for f in files:
            if ids:
                break
            name = ix["files"].get(f.lower())
            if name is not None:
                ids = list(ix["norm"].get(normalize(name), ()))
        norms = [normalize(t) for t in titles if t]
        for n in norms:
            if ids:
                break
            ids = list(ix["norm"].get(n, ())) or list(ix["alt"].get(n, ()))
        if not ids and norms and fuzzy:
            try:
                from rapidfuzz import fuzz, process
                all_norms, all_ids = self._norms(pid)
                if all_norms:
                    hit = process.extractOne(norms[0], all_norms, scorer=fuzz.ratio, score_cutoff=fuzzy)
                    if hit:
                        ids = [all_ids[hit[2]]]
            except ImportError:
                pass
        if not ids:
            return None
        rows = [dict(zip(self._cols, ix["rows"][i])) for i in dict.fromkeys(ids) if i in ix["rows"]]
        if len(rows) < len(set(ids)):  # SteamAppId di un'altra console: non e' nell'indice
            cols = [d[0] for d in c.execute("SELECT * FROM games LIMIT 0").description]
            rows = [dict(zip(cols, r)) for r in c.execute(f"SELECT * FROM games WHERE id IN ({','.join('?' * len(ids))})", ids)]
        if year:
            rows.sort(key=lambda r: (abs(int((r["release"] or "0")[:4] or 0) - year) if r["release"] else 99, -(r["rating_count"] or 0)))
        else:
            rows.sort(key=lambda r: -(r["rating_count"] or 0))
        return rows[0]

    def images(self, gid: int, regions: list[str]) -> dict[str, str]:
        """{tipo: url} scegliendo la regione preferita per ogni tipo."""
        pref = {r: i for i, r in enumerate(regions + ["wor", "", "us", "eu", "uk", "jp"])}
        best: dict[str, tuple[int, str]] = {}
        for kind, region, file in self.con().execute("SELECT kind, region, file FROM images WHERE id=?", (gid,)):
            rank = pref.get(REGION_MAP.get(region, ""), 50)
            if kind not in best or rank < best[kind][0]:
                best[kind] = (rank, file)
        return {k: IMG_BASE + f for k, (_, f) in best.items()}
