"""Giochi Switch letti dal file (NSP/XCI) con il prod.keys dell'utente: titolo, publisher, versione e icona.

Offline e senza quota: legge solo l'NCA di controllo dentro il pacchetto (vedi switchmeta.py). Sta in testa alla
catena, quindi le altre fonti interrogano la rete solo per quello che resta (descrizione, anno, voti...).
L'icona 256x256 diventa la copertina, cosi' ogni gioco ne ha sempre una.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .. import switchmeta
from ..scanner import Game
from .base import Result, Source


class Nsp(Source):
    name = "nsp"
    provides = {"title", "publisher", "media:cover"}
    concurrency = 4  # file su HDD: letture piccole ma sparse
    offline = True
    always = True  # anche dove titolo e copertina ci sono gia': il file e' la fonte piu' affidabile

    def __init__(self, ctx):
        super().__init__(ctx)
        self.keys: dict | None = None

    async def setup(self, platforms) -> bool:
        if not any(p.id == "switch" for p in platforms):
            return False
        try:
            self.keys = await asyncio.to_thread(switchmeta.load_keys, getattr(self.cfg, "switch_keys", ""))
        except switchmeta.SwitchError as e:
            self.log(f"[yellow]nsp: {e}: i giochi Switch useranno solo le fonti online[/]")
            return False
        return True

    def supports(self, platform) -> bool:
        return platform.id == "switch"

    @staticmethod
    def _package(game: Game) -> Path | None:
        """Il pacchetto base: nelle cartelle con aggiornamenti e DLC quello con il titolo che finisce per 000."""
        p = game.path
        files = [p] if not game.is_dir else [f for pat in ("*", "*/*") for f in p.glob(pat)
                                             if f.is_file() and f.suffix.lower() in (".nsp", ".xci")]
        base = [f for f in files if "000]" in f.name.upper() or "000 " in f.name.upper()]
        return max(base or files, key=lambda f: f.stat().st_size, default=None)

    def _read(self, game: Game) -> Result | None:
        pkg = self._package(game)
        if not pkg:
            return None
        try:
            m = switchmeta.read_meta(pkg, list(self.cfg.languages), self.keys)
        except (switchmeta.SwitchError, OSError, ValueError, KeyError):
            return None  # pacchetto non leggibile (es. NSZ o chiavi vecchie): decidono le altre fonti
        res = Result(fields={k: v for k, v in (("title", m["title"]), ("publisher", m["publisher"]),
                                               ("titleid", m["title_id"])) if v})
        if m["icon"]:
            icon = Path(self.cfg.cache) / "switch" / f"{m['title_id']}.jpg"
            icon.parent.mkdir(parents=True, exist_ok=True)
            icon.write_bytes(m["icon"])
            res.media = {"cover": f"file:{icon}"}
        return res

    async def lookup(self, game: Game) -> Result | None:
        return await asyncio.to_thread(self._read, game)
