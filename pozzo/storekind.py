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

# Sistemi con cataloghi storici di DLC/patch scaricabili: solo qui il peso puo' confermare una DLC.
DLC_SIZE_PLATFORMS = frozenset({
    "3ds", "psp", "ps3", "psvita", "ps4", "ps5", "wii", "wiiu", "switch", "xbox", "xbox360",
})

# parole intere, per non colpire giochi veri ("Pack-Man", "Bundle of Joy")
DLC = re.compile(r"\b(dlc|add-?ons?|add-?on content|aoc|season pass|expansion pass|expansion|espansione|"
                 r"story pack|mission pack|level pack|character pack|content pack|texture pack|starter pack|"
                 r"booster pack|cosmetic pack|skin pack|weapon pack|costume pack|upgrade pack|supporter pack|"
                 r"pre-?order (?:bonus|entitlement)|entitlement)\b",
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


# etichette No-Intro/Redump nel nome del file: «Gioco (Europe) (DLC).zip», «Gioco (USA) (v1.03) (Patch).zip»
_TAGS = re.compile(r"\(([^)]*)\)|\[([^\]]*)\]")
TAG_KINDS = (("update", re.compile(r"\b(update|patch)\b", re.I)),
             ("extra", re.compile(r"\b(theme|dynamic theme|avatars?|soundtrack|wallpaper|bios)\b", re.I)),
             ("dlc", re.compile(r"\b(dlc|add-?on|unlock(?: key)?|addcont\w*)\b", re.I)))
# console in cui un gioco non puo' pesare meno di 1 MB: sotto e' una licenza, un tema o un avatar
BIG_GAMES = {"psvita", "ps3", "ps4", "ps5", "switch", "wiiu", "xbox360", "xboxone"}
MIN_GAME_SIZE = 1024 ** 2


def catalog_kind(name: str, title: str, size: int | None = None, pid: str = "") -> str:
    """Tipo di una voce di catalogo console: prima le etichette del nome originale (il titolo pulito le ha
    perse), poi le parole del titolo, infine il peso minimo dei giochi della console."""
    tags = [a or b for a, b in _TAGS.findall(name or "")]
    for kind, rx in TAG_KINDS:
        if any(rx.search(t) for t in tags):
            return kind
    kind = content_kind(title)
    if kind == "game" and re.search(r"\b(?:dynamic )?theme\s*$", title or "", re.I):
        return "extra"  # «Gioco - Academy Theme»: un tema del menu, non «Theme Park»
    if kind == "game" and pid in BIG_GAMES and size and size < MIN_GAME_SIZE:
        return "extra"
    return kind


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
