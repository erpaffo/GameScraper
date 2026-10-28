"""Metadati offline GameTDB per Nintendo Switch (`switchtdb.xml`).

GameTDB pubblica un unico XML con, per ogni gioco: titolo per lingua, sinossi, editore, data,
generi, numero di giocatori e le copertine (`art.gametdb.com/switch/coverHQ/<regione>/<ID>.jpg`).

Serve agli store online, dove le voci non sono file ma pagine web: cosi' copertina e descrizione
arrivano senza una richiesta al sito per ogni gioco. Il file si scarica una volta (circa 3 MB in zip)
e si riusa per 30 giorni; l'indice compatto (JSON) evita di rileggere 23 MB di XML a ogni avvio.
"""

from __future__ import annotations

import json
import io
import os
import re
import shutil
import time
import unicodedata
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

TDB_ZIP = "https://www.gametdb.com/switchtdb.zip?TIME=1"
TDB_XML = "switchtdb.xml"
COVER = "https://art.gametdb.com/switch/coverHQ/{region}/{id}.jpg"
COVER_SMALL = "https://art.gametdb.com/switch/cover/{region}/{id}.jpg"
MAX_AGE = 30 * 86400  # il database cambia spesso: si riscarica una volta al mese
MAX_SYNOPSIS = 1500
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

# regioni delle copertine: quelle del gioco per prime, poi le piu' diffuse
_REGION_COVER = {"USA": "US", "EUR": "EN", "JPN": "JA", "KOR": "KO", "TWN": "ZH", "CHN": "ZH"}
_FALLBACK_COVERS = ("US", "EN", "JA", "FR", "DE")

_PARENS = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_VERSION = re.compile(r"\bv\d+(?:\.\d+)*\b", re.I)
_NOISE = re.compile(r"\b(nsp|xci|eshop|switch|update|dlc|base game|repack|multi\d*|ita|eng|usa|eur|jpn|jap|"
                    r"europe|japanese|korean|chinese|asia)\b", re.I)


def _text(node) -> str:
    return re.sub(r"\s+", " ", (node.text or "")).strip() if node is not None else ""


def title_key(text: str) -> str:
    """Chiave di confronto: minuscole, senza accenti, solo lettere e cifre."""
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def clean_title(text: str) -> str:
    """Titolo confrontabile: via parentesi, versioni e parole tipiche dei siti di ROM."""
    s = _PARENS.sub(" ", text or "")
    s = _VERSION.sub(" ", s)
    s = _NOISE.sub(" ", s)
    s = s.replace("™", " ").replace("®", " ")
    return re.sub(r"\s+", " ", s).strip()


def keys_for(text: str) -> list[str]:
    """Chiavi da provare per un titolo (intero e ripulito)."""
    out = []
    for candidate in (clean_title(text), text):
        k = title_key(candidate)
        if k and k not in out:
            out.append(k)
    return out


def _pick_locale(elem):
    """Locale da usare: italiano, poi inglese, poi il primo disponibile."""
    locales = elem.findall("locale")
    for lang in ("IT", "EN"):
        for loc in locales:
            if (loc.get("lang") or "").upper() == lang and (_text(loc.find("synopsis")) or _text(loc.find("title"))):
                return loc
    return locales[0] if locales else None


def _covers(record: dict) -> list[str]:
    """Copertine candidate: prima la regione del gioco, poi le piu' diffuse (HQ e non)."""
    regions: list[str] = []
    for r in (record.get("region") or "").split(","):
        code = _REGION_COVER.get(r.strip().upper())
        if code and code not in regions:
            regions.append(code)
    for r in _FALLBACK_COVERS:
        if r not in regions:
            regions.append(r)
    gid = record.get("id") or ""
    if not gid:
        return []
    return [COVER.format(region=r, id=gid) for r in regions] + [COVER_SMALL.format(region=r, id=gid) for r in regions]


def _record(elem) -> dict | None:
    """Record compatto da un elemento <game> (None se senza ID)."""
    gid = _text(elem.find("id"))
    if not gid:
        return None
    loc = _pick_locale(elem)
    locales = elem.findall("locale")
    titles = [_text(l.find("title")) for l in locales if _text(l.find("title"))]
    name_attr = _PARENS.sub(" ", elem.get("name") or "")
    date = elem.find("date")
    day = (
        f"{int(date.get('year') or 0):04d}-{int(date.get('month') or 1):02d}-{int(date.get('day') or 1):02d}"
        if date is not None and str(date.get("year") or "").isdigit() else ""
    )
    inp = elem.find("input")
    players = 0
    if inp is not None:
        try:
            players = int(inp.get("players") or 0)
        except ValueError:
            players = 0
    record = {
        "id": gid,
        "title": (_text(loc.find("title")) if loc is not None else "") or (titles[0] if titles else name_attr),
        "synopsis": (_text(loc.find("synopsis")) if loc is not None else "")[:MAX_SYNOPSIS],
        "publisher": _text(elem.find("publisher")),
        "developer": _text(elem.find("developer")),
        "release_date": day,
        "genres": [g.strip() for g in (_text(elem.find("genre")) or "").split(",") if g.strip()],
        "players": players,
        "region": _text(elem.find("region")),
    }
    record["covers"] = _covers(record)
    record["_titles"] = [t for t in ([record["title"], name_attr, *titles]) if t]
    return record


def parse_xml(source) -> dict:
    """Indice {"version", "games": {id: record}, "titles": {chiave: id}} da file, stream o testo XML."""
    if isinstance(source, (str, bytes)) and (source.lstrip()[:1] in ("<", b"<")):
        source = io.StringIO(source.decode("utf-8") if isinstance(source, bytes) else source)
    version, games, titles = "", {}, {}
    for _event, elem in ET.iterparse(source, events=("end",)):
        if elem.tag == "SwitchTDB":
            version = elem.get("version") or ""
            continue
        if elem.tag != "game":
            continue
        rec = _record(elem)
        elem.clear()
        if not rec:
            continue
        games[rec["id"]] = rec
        for title in rec.pop("_titles"):
            for key in keys_for(title):
                titles.setdefault(key, rec["id"])
    return {"version": version, "built": time.time(), "games": games, "titles": titles}


def parse_xml_text(text: str) -> dict:
    return parse_xml(text)


class GameTDB:
    """Database GameTDB locale: scarica/aggiorna il file e offre la ricerca per titolo o per ID."""

    def __init__(self, folder, log=print):
        self.folder = Path(folder) / "gametdb"
        self.log = log
        self._index: dict | None = None

    # ---------------------------------------------------------------- file
    @property
    def xml_path(self) -> Path:
        env = os.environ.get("POZZO_GAMETDB")
        return Path(env) if env else self.folder / TDB_XML

    @property
    def index_path(self) -> Path:
        return self.folder / "index.json"

    def ready(self) -> bool:
        return self.index_path.exists() and self.index_path.stat().st_size > 1024

    def age(self) -> float:
        p = self.xml_path if self.xml_path.exists() else self.index_path
        return time.time() - p.stat().st_mtime if p.exists() else float("inf")

    def download(self) -> bool:
        """Scarica switchtdb.zip e ne estrae l'XML (una volta al mese)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / "switchtdb.zip.part"
        try:
            req = urllib.request.Request(TDB_ZIP, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            with zipfile.ZipFile(tmp) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".xml")]
                if not names:
                    raise RuntimeError("zip senza XML")
                with z.open(names[0]) as src, open(self.folder / TDB_XML, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            self.log(f"GameTDB: database scaricato ({self.xml_path.stat().st_size // 1024 // 1024} MB)")
            return True
        except Exception as e:
            self.log(f"GameTDB: download non riuscito ({type(e).__name__}: {e})")
            return False
        finally:
            tmp.unlink(missing_ok=True)

    def ensure(self, force: bool = False) -> bool:
        """Prepara l'indice: scarica il database se serve, poi lo converte in JSON compatto."""
        xml = self.xml_path
        external = bool(os.environ.get("POZZO_GAMETDB"))  # file indicato a mano: non lo si sovrascrive
        if not external:
            stale = not xml.exists() or force or (time.time() - xml.stat().st_mtime > MAX_AGE)
            if stale and not self.download() and not xml.exists():
                return self.ready()
        if not xml.exists():
            return self.ready()
        if self.index_path.exists() and self.index_path.stat().st_mtime >= xml.stat().st_mtime:
            return True
        self.log("GameTDB: preparazione dell'indice…")
        try:
            with open(xml, "rb") as f:
                index = parse_xml(f)
        except Exception as e:
            self.log(f"GameTDB: XML non leggibile ({type(e).__name__}: {e})")
            return self.ready()
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.index_path)
        self.log(f"GameTDB: {len(index['games']):,} giochi indicizzati")
        return True

    # ---------------------------------------------------------------- ricerca
    def load(self) -> dict:
        if self._index is None:
            try:
                self._index = json.loads(self.index_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._index = {"games": {}, "titles": {}}
        return self._index

    def by_id(self, game_id: str) -> dict | None:
        if not game_id:
            return None
        return self.load()["games"].get(game_id.strip().upper())

    def find(self, title: str) -> dict | None:
        """Record GameTDB per un titolo (confronto senza accenti, parentesi e parole dei siti ROM)."""
        if not title:
            return None
        index = self.load()
        for key in keys_for(title):
            gid = index["titles"].get(key)
            if gid:
                return index["games"].get(gid)
        return None

    def lookup(self, title: str = "", title_id: str = "") -> dict | None:
        """Come find(), ma prova prima l'ID (Title ID della pagina del gioco, molto piu' preciso)."""
        return self.by_id(title_id) or self.find(title)
