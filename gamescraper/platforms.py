"""Tabella delle piattaforme supportate.

Ogni piattaforma ha:
- id breve (usato da CLI, cartelle di output, cache)
- estensioni dei file ROM riconosciute
- id della piattaforma presso ScreenScraper / IGDB / TheGamesDB
- nomi dei sistemi libretro (DAT No-Intro/Redump + thumbnails)
- alias: nomi di cartella riconosciuti automaticamente durante la scansione

Gli id ScreenScraper derivano dal codice originale di Skyscraper (screenscraper.cpp).
Se per TheGamesDB l'id non e' noto, viene risolto a runtime per nome (/v1/Platforms).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ARCHIVES = ("zip", "7z")


@dataclass(frozen=True)
class Platform:
    id: str
    name: str
    exts: tuple[str, ...]
    ss: int | None = None
    igdb: tuple[int, ...] = ()
    tgdb: int | None = None
    libretro: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    arcade: bool = False
    cd: bool = False  # sistemi su disco: niente hash completo, identificazione per nome/dimensione
    headered: int = 0  # byte di header da saltare per calcolare il CRC "No-Intro" (es. iNES)
    virtual: bool = False  # non e' una cartella ROM (es. giochi PC dagli store)
    dir_games: bool = False  # ogni sottocartella e' un gioco (PS3, Vita, Xbox 360, Switch con base+update)
    long_names: tuple[str, ...] = ()  # nomi di cartella stile No-Intro/Redump riconosciuti anche come prefisso
    extra: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def all_exts(self) -> tuple[str, ...]:
        return self.exts + ARCHIVES


def _p(id, name, exts, ss=None, igdb=(), tgdb=None, libretro=(), aliases=(), **kw):
    if isinstance(igdb, int):
        igdb = (igdb,)
    if isinstance(libretro, str):
        libretro = (libretro,)
    return Platform(id, name, tuple(exts.split()), ss, tuple(igdb), tgdb, tuple(libretro), tuple(aliases), **kw)


CD = dict(cd=True)

PLATFORMS: dict[str, Platform] = {p.id: p for p in [
    # --- Nintendo ---
    _p("nes", "Nintendo Entertainment System", "nes unf unif", 3, 18, 7,
       "Nintendo - Nintendo Entertainment System", ("famicom", "nintendo", "nintendo entertainment system"), headered=16),
    _p("fds", "Famicom Disk System", "fds", 106, 51, None,
       "Nintendo - Family Computer Disk System", ("famicom disk system", "disksystem"), headered=16),
    _p("snes", "Super Nintendo", "sfc smc fig swc mgd bin", 4, 19, 6,
       "Nintendo - Super Nintendo Entertainment System", ("super nintendo", "superfamicom", "sfc", "super famicom", "snesna")),
    _p("n64", "Nintendo 64", "z64 n64 v64", 14, 4, 3, "Nintendo - Nintendo 64", ("nintendo 64",)),
    _p("gb", "Game Boy", "gb", 9, 33, 4, "Nintendo - Game Boy", ("gameboy", "game boy")),
    _p("gbc", "Game Boy Color", "gbc", 10, 22, 41, "Nintendo - Game Boy Color", ("gameboy color", "game boy color")),
    _p("gba", "Game Boy Advance", "gba", 12, 24, 5, "Nintendo - Game Boy Advance", ("gameboy advance", "game boy advance")),
    _p("nds", "Nintendo DS", "nds", 15, 20, 8, "Nintendo - Nintendo DS", ("ds", "nintendo ds")),
    _p("3ds", "Nintendo 3DS", "3ds cia cci", 17, 37, 4912, "Nintendo - Nintendo 3DS", ("n3ds", "nintendo 3ds")),
    _p("virtualboy", "Virtual Boy", "vb", 11, 87, 4918, "Nintendo - Virtual Boy", ("vb", "virtual boy")),
    _p("pokemini", "Pokemon Mini", "min", 211, 166, 4957, "Nintendo - Pokemon Mini", ("pokemon mini",)),
    _p("gameandwatch", "Game & Watch", "mgw", 52, 307, 4950, "Handheld Electronic Game", ("game and watch", "gw")),
    _p("gc", "GameCube", "iso gcm gcz rvz ciso m3u", 13, 21, 2, "Nintendo - GameCube", ("gamecube", "ngc"), **CD),
    _p("wii", "Wii", "iso wbfs rvz gcz ciso", 16, 5, 9, "Nintendo - Wii", (), **CD),
    _p("wiiu", "Wii U", "wud wux rpx wua iso", 18, 41, 38, (), ("wii u",), long_names=("Nintendo - Wii U",), **CD),
    _p("switch", "Nintendo Switch", "xci nsp nsz xcz", 225, 130, 4971, (), ("nintendo switch",),
       long_names=("Nintendo - Nintendo Switch",), dir_games=True, **CD),
    # --- Sega ---
    _p("sg-1000", "SG-1000", "sg bin", 109, 84, 4949, "Sega - SG-1000", ("sg1000",)),
    _p("mastersystem", "Master System", "sms", 2, 64, 35, "Sega - Master System - Mark III", ("sms", "master system", "mark iii")),
    _p("megadrive", "Mega Drive / Genesis", "md gen smd bin", 1, 29, 18,
       "Sega - Mega Drive - Genesis", ("genesis", "md", "mega drive", "sega genesis")),
    _p("gamegear", "Game Gear", "gg", 21, 35, 20, "Sega - Game Gear", ("gg", "game gear")),
    _p("sega32x", "Sega 32X", "32x bin", 19, 30, 33, "Sega - 32X", ("32x",)),
    _p("segacd", "Mega-CD / Sega CD", "cue chd iso m3u", 20, 78, 21, "Sega - Mega-CD - Sega CD", ("megacd", "sega cd", "mega cd"), **CD),
    _p("saturn", "Sega Saturn", "cue chd iso m3u mdf", 22, 32, 17, "Sega - Saturn", ("sega saturn",),
       long_names=("Sega - Sega Saturn",), **CD),
    _p("dreamcast", "Dreamcast", "cdi gdi chd cue m3u", 23, 23, 16, "Sega - Dreamcast", ("dc",), **CD),
    _p("naomi", "Sega NAOMI", "zip chd", 56, 52, 23, "Sega - Naomi", (), arcade=True),
    _p("atomiswave", "Atomiswave", "zip chd", 53, 52, 23, "Sammy - Atomiswave", (), arcade=True),
    # --- Sony ---
    _p("psx", "PlayStation", "cue chd pbp m3u iso img ccd", 57, 7, 10, "Sony - PlayStation", ("ps1", "playstation", "psone"), **CD),
    _p("ps2", "PlayStation 2", "iso chd cso gz", 58, 8, 11, "Sony - PlayStation 2", ("playstation 2",), **CD),
    _p("psp", "PlayStation Portable", "iso cso pbp chd", 61, 38, 13, "Sony - PlayStation Portable", ("playstation portable",), **CD),
    _p("ps3", "PlayStation 3", "iso pkg", 59, 9, 12, "Sony - PlayStation 3", ("playstation 3",), dir_games=True, **CD),
    _p("psvita", "PlayStation Vita", "vpk", 62, 46, 39, "Sony - PlayStation Vita", ("vita", "ps vita", "playstation vita"),
       dir_games=True, **CD),
    _p("ps4", "PlayStation 4", "pkg", None, 48, None, (), ("playstation 4", "sony playstation 4"),
       dir_games=True, **CD),
    # --- PC: giochi degli store (Steam, Epic, GOG...) e aggiunti a mano, non da cartelle ROM ---
    _p("windows", "PC (Windows)", "", None, 6, 1, (), (), virtual=True),
    # --- Microsoft ---
    _p("xbox", "Xbox", "iso xiso", 32, 11, 14, "Microsoft - Xbox", ("xbox classic",), dir_games=True, **CD),
    _p("xbox360", "Xbox 360", "iso xex god", 33, 12, 15, "Microsoft - Xbox 360", ("x360", "xbox 360"), dir_games=True, **CD),
    # --- Atari ---
    _p("atari2600", "Atari 2600", "a26 bin", 26, 59, 22, "Atari - 2600", ("2600", "vcs")),
    _p("atari5200", "Atari 5200", "a52 bin", 40, 66, 26, "Atari - 5200", ("5200",)),
    _p("atari7800", "Atari 7800", "a78 bin", 41, 60, 27, "Atari - 7800", ("7800",), headered=128),
    _p("atarilynx", "Atari Lynx", "lnx", 28, 61, 4924, "Atari - Lynx", ("lynx",), headered=64),
    _p("atarijaguar", "Atari Jaguar", "j64 jag", 27, 62, 28, "Atari - Jaguar", ("jaguar",)),
    _p("atarist", "Atari ST", "st stx msa", 42, 63, None, "Atari - ST", ("st",)),
    _p("atari800", "Atari 8-bit", "atr xex xfd car", 43, 65, None, "Atari - 8-bit", ("atari 800", "atari8bit")),
    # --- NEC / SNK / Bandai / altri ---
    _p("pcengine", "PC Engine / TurboGrafx-16", "pce sgx", 31, 86, 34,
       "NEC - PC Engine - TurboGrafx 16", ("tg16", "turbografx16", "turbografx", "pc engine")),
    _p("pcenginecd", "PC Engine CD", "cue chd m3u", 114, 150, None,
       "NEC - PC Engine CD - TurboGrafx-CD", ("tg-cd", "tgcd", "turbografxcd"), **CD),
    _p("pcfx", "PC-FX", "cue chd ccd", 72, 274, None, "NEC - PC-FX", (), **CD),
    _p("neogeo", "Neo Geo", "zip", 142, (80, 79), 24, "SNK - Neo Geo", ("neo geo", "aes", "mvs"), arcade=True),
    _p("neogeocd", "Neo Geo CD", "cue chd", 70, 136, None, "SNK - Neo Geo CD", ("neo geo cd",), **CD),
    _p("ngp", "Neo Geo Pocket", "ngp", 25, 119, 4922, "SNK - Neo Geo Pocket", ()),
    _p("ngpc", "Neo Geo Pocket Color", "ngc", 82, 120, 4923, "SNK - Neo Geo Pocket Color", ()),
    _p("wonderswan", "WonderSwan", "ws", 45, 57, 4925, "Bandai - WonderSwan", ("ws",)),
    _p("wonderswancolor", "WonderSwan Color", "wsc", 46, 123, 4926, "Bandai - WonderSwan Color", ("wsc",)),
    _p("3do", "3DO", "iso chd cue", 29, 50, 25, "The 3DO Company - 3DO", ("panasonic 3do",), **CD),
    _p("coleco", "ColecoVision", "col rom bin", 48, 68, 31, "Coleco - ColecoVision", ("colecovision",)),
    _p("intellivision", "Intellivision", "int rom bin", 115, 67, 32, "Mattel - Intellivision", ("intv",)),
    _p("vectrex", "Vectrex", "vec gam bin", 102, 70, 4939, "GCE - Vectrex", ()),
    _p("videopac", "Odyssey 2 / Videopac", "bin", 104, 133, 4927, "Magnavox - Odyssey2", ("odyssey2", "o2em")),
    _p("channelf", "Fairchild Channel F", "chf bin", 80, 127, 4928, "Fairchild - Channel F", ("channel f",)),
    # --- Computer ---
    _p("c64", "Commodore 64", "d64 t64 prg crt tap g64", 66, 15, 40, "Commodore - 64", ("commodore 64",)),
    _p("vic20", "Commodore VIC-20", "crt prg d64 tap", 73, 71, None, "Commodore - VIC-20", ("vic-20",)),
    _p("amiga", "Amiga", "adf lha hdf ipf dms", 64, 16, 4911, "Commodore - Amiga", ("commodore amiga",)),
    _p("amigacd32", "Amiga CD32", "cue chd iso", 130, 114, 4947, "Commodore - CD32", ("cd32",), **CD),
    _p("msx", "MSX", "rom mx1 mx2 dsk cas", 113, 27, 4929, "Microsoft - MSX", ()),
    _p("msx2", "MSX2", "rom mx2 dsk", 116, 53, 4929, "Microsoft - MSX2", ()),
    _p("amstradcpc", "Amstrad CPC", "dsk cdt sna", 65, 25, 4914, "Amstrad - CPC", ("cpc",)),
    _p("zxspectrum", "ZX Spectrum", "tzx tap z80 sna dsk trd", 76, 26, 4913, "Sinclair - ZX Spectrum", ("spectrum", "zx spectrum")),
    _p("zx81", "ZX81", "p tzx t81", 77, (), None, "Sinclair - ZX 81", ()),
    _p("x68000", "Sharp X68000", "dim hdf m3u", 79, 121, None, "Sharp - X68000", ("x68k",)),
    _p("pc98", "NEC PC-98", "d98 hdi fdi", 208, 149, None, "NEC - PC-98", ()),
    _p("dos", "MS-DOS", "exe com bat dosz", 135, 13, 1, "DOS", ("pc", "msdos")),
    _p("scummvm", "ScummVM", "svm scummvm", 123, (), None, "ScummVM", ()),
    # --- Arcade ---
    _p("arcade", "Arcade (MAME)", "zip", 75, 52, 23, "MAME", ("mame", "mame-libretro", "mame2003", "mame2003-plus", "mame2010"), arcade=True),
    _p("fbneo", "FinalBurn Neo", "zip", 75, 52, 23, "FBNeo - Arcade Games", ("fba", "fbn", "finalburn neo"), arcade=True),
    _p("cps1", "Capcom CPS-1", "zip", 6, 52, 23, "MAME", (), arcade=True),
    _p("cps2", "Capcom CPS-2", "zip", 7, 52, 23, "MAME", (), arcade=True),
    _p("cps3", "Capcom CPS-3", "zip", 8, 52, 23, "MAME", (), arcade=True),
]}

# Portatili senza split screen: il "multiplayer" e' tra console diverse (cavo link, wireless locale)
NO_SPLIT = frozenset("gb gbc gba nds 3ds psp psvita virtualboy pokemini gameandwatch atarilynx gamegear "
                     "ngp ngpc wonderswan wonderswancolor".split())


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_ALIAS_INDEX: dict[str, str] = {}
_PREFIX_INDEX: list[tuple[str, str]] = []  # (nome normalizzato, id) dal piu' lungo al piu' corto
for _p_ in PLATFORMS.values():
    for _a in (_p_.id, _p_.name, *_p_.aliases):
        _ALIAS_INDEX.setdefault(norm(_a), _p_.id)
    for _l in (*_p_.libretro, *_p_.long_names):
        _ALIAS_INDEX.setdefault(norm(_l), _p_.id)
        if not _p_.arcade:
            _PREFIX_INDEX.append((norm(_l), _p_.id))
_PREFIX_INDEX.sort(key=lambda x: -len(x[0]))

# prefissi dei set Myrient/No-Intro da ignorare: "Non-Redump - Sega - Dreamcast" -> "Sega - Dreamcast"
_SET_PREFIX = re.compile(r"^(non-redump|unofficial|aftermarket|private|source code|misc)\s*-\s*", re.I)


def detect(folder_name: str, overrides: dict[str, str] | None = None) -> Platform | None:
    """Riconosce la piattaforma dal nome di una cartella ROM.

    Accetta id/alias ("snes", "Super Nintendo") e nomi dei set No-Intro/Redump anche con
    suffissi ("Nintendo - GameCube - NKit RVZ [zstd-19-128k]" -> gc)."""
    if overrides:
        for k, v in overrides.items():
            if norm(k) == norm(folder_name) and v in PLATFORMS:
                return PLATFORMS[v]
    n = norm(folder_name)
    pid = _ALIAS_INDEX.get(n)
    if not pid:
        n = norm(_SET_PREFIX.sub("", folder_name))
        pid = _ALIAS_INDEX.get(n) or next((i for name, i in _PREFIX_INDEX if n.startswith(name)), None)
    return PLATFORMS.get(pid) if pid else None


def get(pid: str) -> Platform:
    p = detect(pid)
    if not p:
        raise KeyError(f"Piattaforma sconosciuta: {pid!r}. Usa 'python -m gamescraper platforms' per l'elenco.")
    return p


# ---------------------------------------------------------------- console aggiunte dall'utente

def register_custom(items: list[dict]):
    """Aggiunge (o aggiorna) console definite dall'utente in settings.json -> "custom_platforms"."""
    for it in items or []:
        pid = re.sub(r"[^a-z0-9_-]", "", str(it.get("id", "")).lower())
        name = str(it.get("name") or pid).strip()
        if not pid or not name:
            continue
        exts = tuple(e.lower().lstrip(".") for e in str(it.get("exts", "")).replace(",", " ").split() if e)
        aliases = tuple(a.strip() for a in it.get("aliases", []) if a.strip())
        igdb = tuple(int(x) for x in str(it.get("igdb", "")).replace(",", " ").split() if x.isdigit())
        p = Platform(pid, name, exts, int(it["ss"]) if str(it.get("ss", "")).isdigit() else None, igdb,
                     int(it["tgdb"]) if str(it.get("tgdb", "")).isdigit() else None, (), aliases,
                     cd=bool(it.get("cd")), dir_games=bool(it.get("dir_games")), extra={"custom": True})
        PLATFORMS[pid] = p
        for a in (pid, name, *aliases):
            _ALIAS_INDEX[norm(a)] = pid


def unregister_custom(pid: str):
    p = PLATFORMS.get(pid)
    if p and p.extra.get("custom"):
        del PLATFORMS[pid]
        for k in [k for k, v in _ALIAS_INDEX.items() if v == pid]:
            del _ALIAS_INDEX[k]
