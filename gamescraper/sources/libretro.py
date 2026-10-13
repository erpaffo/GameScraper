"""libretro-database + thumbnails.libretro.com

E' la chiave della velocita': i DAT No-Intro/Redump vengono scaricati una volta (cache 30 giorni)
e le ROM sono identificate OFFLINE per CRC (o per nome esatto/normalizzato), senza alcuna
chiamata API. Gli stessi DAT forniscono sviluppatore, editore, genere, anno, giocatori.
Le immagini (box, screenshot, title screen) arrivano da thumbnails.libretro.com, senza quota.

Il nome canonico identificato qui viene poi usato dalle altre sorgenti come termine di
ricerca, migliorando molto i match di IGDB/TheGamesDB rispetto al nome file "sporco".
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from .. import matching
from ..net import RateLimiter, request
from ..scanner import Game, clean_title, regions_of
from .base import Result, Source

RAW = "https://raw.githubusercontent.com/libretro/libretro-database/master/"
THUMBS = "https://thumbnails.libretro.com/"
MAIN_DIRS = ("metadat/no-intro", "metadat/redump", "dat")
META_DIRS = ("developer", "publisher", "genre", "releaseyear", "releasemonth", "maxusers", "franchise", "serial")
THUMB_DIRS = {"cover": "Named_Boxarts", "screenshot": "Named_Snaps", "title": "Named_Titles"}

_GAME = re.compile(r"^game \($(.*?)^\)$", re.M | re.S)
_KV = re.compile(r'^\s*(\w+)\s+(?:"((?:[^"\\]|\\.)*)"|([^\s(]\S*))\s*$', re.M)
_ROM = re.compile(r"^\s*rom \( (.*) \)\s*$", re.M)
_ROMKV = re.compile(r'(\w+) (?:"([^"]*)"|(\S+))')


def _clean(v: str) -> str:
    # alcuni DAT libretro hanno caratteri giapponesi persi come "???"
    return re.sub(r"\s*\?{2,}", "", v).strip()


def parse_dat(text: str):
    for m in _GAME.finditer(text):
        body = m.group(1)
        g = {k: _clean(q or b) for k, q, b in _KV.findall(body)}
        g["roms"] = [{k: (q if q else b) for k, q, b in _ROMKV.findall(r)} for r in _ROM.findall(body)]
        yield g


class Index:
    def __init__(self):
        self.system = ""
        self.by_crc: dict[str, str] = {}
        self.by_name: dict[str, str] = {}
        self.by_norm: dict[str, list[str]] = defaultdict(list)
        self.meta_crc: dict[str, dict] = defaultdict(dict)
        self.meta_name: dict[str, dict] = defaultdict(dict)
        self.rom_info: dict[str, dict] = {}  # stem del file (o nome gioco a ROM singola) -> crc/size/md5/sha1

    def add_main(self, text: str):
        for g in parse_dat(text):
            name = g.get("name") or g.get("comment")
            if not name:
                continue
            self.by_name.setdefault(name.lower(), name)
            self.by_norm[matching.normalize(name)].append(name)
            for r in g["roms"]:
                if r.get("crc"):
                    self.by_crc.setdefault(r["crc"].upper(), name)
                if r.get("name"):
                    self.by_name.setdefault(Path(r["name"]).stem.lower(), name)
            serial = next((r["serial"] for r in g["roms"] if r.get("serial")), None)
            if serial:  # i DAT di NDS/3DS/GC/Wii hanno il codice prodotto nella riga della ROM
                self.meta_name[name.lower()].setdefault("serial", serial)
            if len(g["roms"]) == 1 and g["roms"][0].get("crc"):
                r = g["roms"][0]
                info = {"dat_crc": r["crc"].upper(), "dat_size": int(r.get("size") or 0),
                        "dat_md5": (r.get("md5") or "").upper(), "dat_sha1": (r.get("sha1") or "").upper()}
                self.rom_info.setdefault(name.lower(), info)
                if r.get("name"):
                    self.rom_info.setdefault(Path(r["name"]).stem.lower(), info)

    def add_meta(self, text: str):
        for g in parse_dat(text):
            vals = {k: v for k, v in g.items() if k not in ("roms", "comment", "name")}
            if g.get("comment"):
                self.meta_name[g["comment"].lower()].update(vals)
            for r in g["roms"]:
                if r.get("crc"):
                    self.meta_crc[r["crc"].upper()].update(vals)

    def identify_by_name(self, game: Game) -> tuple[str, dict] | None:
        """Solo corrispondenza ESATTA del nome file con il DAT: affidabile e senza leggere il file."""
        for stem in (game.stem, Path(game.inner_name).stem if game.inner_name else ""):
            if stem and stem.lower() in self.by_name:
                return self.by_name[stem.lower()], self.rom_info.get(stem.lower(), {})
        return None

    def identify(self, game: Game, regions: list[str]) -> str | None:
        for crc in (game.crc, game.crc_nh, game.dat_crc):
            if crc and crc in self.by_crc:
                return self.by_crc[crc]
        for stem in (game.stem, Path(game.inner_name).stem if game.inner_name else ""):
            if stem and stem.lower() in self.by_name:
                return self.by_name[stem.lower()]
        cands = self.by_norm.get(matching.normalize(game.stem))
        if cands:
            want = regions_of(game.stem) + regions
            def rank(n):
                rs = regions_of(n)
                return min((want.index(r) for r in rs if r in want), default=99), len(n)
            return min(cands, key=rank)
        return None

    def meta(self, game: Game, name: str) -> dict:
        m = dict(self.meta_name.get(name.lower(), {}))
        for crc in (game.crc, game.crc_nh, game.dat_crc):
            if crc and crc in self.meta_crc:
                m.update(self.meta_crc[crc])
        return m


def thumb_name(name: str) -> str:
    return re.sub(r'[&*/:`<>?\\|"]', "_", name)


class Libretro(Source):
    name = "libretro"
    provides = {"title", "developer", "publisher", "genres", "release_date", "players",
                "media:cover", "media:screenshot", "media:title"}
    concurrency = 256
    always = True  # identificazione offline: gira per tutti i giochi non ancora identificati

    def __init__(self, ctx):
        super().__init__(ctx)
        self.indexes: dict[str, Index] = {}
        self.media_limiter = RateLimiter(0, int(self.cfg.libretro.get("download_threads", 24)))
        self.refresh_days = float(self.cfg.libretro.get("refresh_days", 30))
        self.dir = Path(self.cfg.cache) / "libretro"

    def supports(self, platform) -> bool:
        return platform.id in self.indexes

    async def _fetch(self, rel: str) -> str | None:
        dest = self.dir / rel
        if dest.exists() and time.time() - dest.stat().st_mtime < self.refresh_days * 86400:
            return dest.read_text(encoding="utf-8", errors="ignore")
        try:
            r = await request(self.client, "GET", RAW + quote(rel), retries=3)
        except Exception:
            r = None
        if r is None or r.status_code != 200:
            if dest.exists():  # offline: usa la copia vecchia
                return dest.read_text(encoding="utf-8", errors="ignore")
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return r.text

    async def _build(self, platform) -> Index | None:
        idx = Index()
        ok = False
        for system in platform.libretro:
            texts = await asyncio.gather(*(self._fetch(f"{d}/{system}.dat") for d in MAIN_DIRS))
            for t in texts:
                if t:
                    idx.add_main(t)
                    ok = True
            metas = await asyncio.gather(*(self._fetch(f"metadat/{d}/{system}.dat") for d in META_DIRS))
            for t in metas:
                if t:
                    idx.add_meta(t)
            idx.system = idx.system or system
        if not idx.system:
            return None
        if not ok:
            self.log(f"[yellow]libretro: nessun DAT per {platform.id}, solo thumbnails per nome file[/]")
        return idx

    async def setup(self, platforms) -> bool:
        targets = [p for p in platforms if p.libretro and not p.arcade]
        built = await asyncio.gather(*(self._build(p) for p in targets))
        for p, idx in zip(targets, built):
            if idx:
                self.indexes[p.id] = idx
                self.log(f"libretro: {p.id}: {len(idx.by_crc):,} CRC, {len(idx.by_name):,} nomi indicizzati")
        return bool(self.indexes)

    def name_first(self, games: list[Game]) -> list[Game]:
        """Identifica per nome esatto i giochi non ancora hashati. Ritorna quelli riconosciuti
        (per cui non serve leggere il file: CRC/MD5/SHA1 arrivano dal DAT)."""
        hits = []
        for g in games:
            idx = self.indexes.get(g.platform.id)
            if not idx or g.is_dir:
                continue
            r = idx.identify_by_name(g)
            if r:
                name, info = r
                g.apply_file_data(info)
                g.canonical = g.canonical or name
                hits.append(g)
        return hits

    async def lookup(self, game: Game) -> Result | None:
        idx = self.indexes[game.platform.id]
        name = idx.identify(game, self.cfg.regions)
        res = Result()
        thumb = name or game.stem  # molte collezioni usano gia' i nomi No-Intro
        res.media = {t: f"{THUMBS}{quote(idx.system)}/{d}/{quote(thumb_name(thumb))}.png" for t, d in THUMB_DIRS.items()}
        if not name:
            return res if self.cfg.libretro.get("thumbs_by_filename", True) else None
        f = res.fields
        f["canonical"] = name
        f["title"] = clean_title(name)
        f["region"] = ",".join(regions_of(name))
        m = idx.meta(game, name)
        f["developer"] = m.get("developer")
        f["publisher"] = m.get("publisher")
        if m.get("genre"):
            f["genres"] = [x.strip() for x in re.split(r"[/,]", m["genre"]) if x.strip()]
        if m.get("releaseyear"):
            f["release_date"] = m["releaseyear"] + (f"-{int(m['releasemonth']):02d}" if m.get("releasemonth", "").isdigit() else "")
        if m.get("users"):
            f["players"] = m["users"]
        if m.get("franchise"):
            f["franchises"] = [m["franchise"]]
        if m.get("serial"):
            f["serial"] = m["serial"]
        return res
