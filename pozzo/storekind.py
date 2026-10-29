"""Tipo di contenuto di una voce di store: gioco, DLC (add-on), extra non giocabile o aggiornamento.

Sugli store PC il tipo lo dichiara lo store (Steam `type`, Epic `categories`, GOG `type`) e qui si usa quello.
Nei cataloghi delle console il tipo non esiste: restano le parole del nome e, solo come conferma, il peso
rispetto al gioco omonimo (vedi build._dlc_by_size). «Add-on» e' il nome commerciale delle DLC (Xbox,
PlayStation, «Add-On Content» su Nintendo): una categoria sola, come «DLC».
"""

from __future__ import annotations

import re

KINDS = ("game", "dlc", "extra", "update", "app")
SIZE_RATIO = 0.35  # meno di un terzo del gioco omonimo: quasi sempre una DLC

# parole intere, per non colpire giochi veri ("Pack-Man", "Bundle of Joy")
DLC = re.compile(r"\b(dlc|add-?ons?|add-?on content|aoc|season pass|expansion pass|expansion|espansione|"
                 r"story pack|mission pack|level pack|character pack|content pack|texture pack|starter pack|"
                 r"booster pack|cosmetic pack|skin pack|weapon pack|costume pack|upgrade pack|supporter pack)\b",
                 re.I)
EXTRA = re.compile(r"\b(soundtrack|original score|art ?book|digital comic|comic book|wallpaper|avatars?|"
                   r"bonus content|bundle|dedicated server|sdk|benchmark)\b", re.I)
# «Update» seguito da una versione, da una parentesi o in fondo al titolo: non colpisce giochi tipo «Patch Quest»
UPDATE = re.compile(r"\b(?:update|patch|upd)\b(?:\s*[\(\[]?\s*v?\d|\s*[\)\]]|\s*$)", re.I)
_VERSION_TAIL = re.compile(r"\s*\bv?\d+(?:\.\d+)*\s*$", re.I)
_BRACKETS = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")


def content_kind(title: str, declared: str = "") -> str:
    """'game' | 'dlc' | 'extra' | 'update' | 'app'.

    Prima i segnali non ambigui del nome (aggiornamento, contenuto non giocabile), poi il tipo dichiarato
    dallo store (solo «dlc» e «app»: uno store che dice «game» non sa cosa sia il resto), infine le parole
    che indicano una DLC."""
    t = title or ""
    if UPDATE.search(t):
        return "update"
    if EXTRA.search(t):
        return "extra"
    if declared in ("dlc", "app"):
        return declared
    if DLC.search(t):
        return "dlc"
    return "game"


def update_base(title: str) -> str:
    """'Gioco [Update v1.2.0]' -> 'Gioco': il nome con cui l'aggiornamento si aggancia al gioco base."""
    t = _BRACKETS.sub(" ", title or "")
    t = UPDATE.sub(" ", t)
    t = _VERSION_TAIL.sub("", t)
    return re.sub(r"\s+", " ", t).strip(" -–—:.")


def update_version(title: str) -> str:
    """Versione leggibile di un aggiornamento ('v1.2.0', 'Update 65536'), '' se il nome non la contiene."""
    for pattern in (r"\b(?:update|upd)\s*v?(\d+(?:\.\d+)*)", r"\bv(\d+(?:\.\d+)+)\b", r"\b(\d+(?:\.\d+)+)\b"):
        m = re.search(pattern, title or "", re.I)
        if m:
            return m.group(1)
    return ""


def switch_kind(title_id: str) -> str:
    """Tipo dal Title ID di Switch: ultime 3 cifre esadecimali 000 = gioco, 800 = aggiornamento, altro = DLC."""
    t = str(title_id or "").strip()
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", t):
        return ""
    low = int(t[-3:], 16)
    return "game" if low == 0 else "update" if low == 0x800 else "dlc"
