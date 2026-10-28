"""Client per la Web API di RetroAchievements (https://api-docs.retroachievements.org/).

Autenticazione: parametro query `y=<web_api_key>` (chiave personale nel profilo di
retroachievements.org) e `u=<username>` per gli endpoint relativi al progresso dell'utente.
Include una cache in memoria per sessione (per evitare chiamate duplicate verso endpoint pesanti
come `API_GetGameList.php`), timeout brevi, messaggi di errore chiari e abbinamento dei giochi
per hash RA (MD5) o titolo normalizzato (`pozzo.matching`).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx

from . import matching

BASE_URL = "https://retroachievements.org/API"
MEDIA_URL = "https://media.retroachievements.org"
UA = {"User-Agent": "PozzoLauncher/1.0"}

# Mappa dagli id piattaforma di PozzoLauncher (pozzo/platforms.py) agli id di RetroAchievements
# (verificata su https://github.com/RetroAchievements/RAWeb/blob/master/config/systems.php).
CONSOLE_IDS: dict[str, int] = {
    # Nintendo
    "nes": 7,
    "fds": 81,
    "snes": 3,
    "n64": 2,
    "gb": 4,
    "gbc": 6,
    "gba": 5,
    "nds": 18,
    "3ds": 62,
    "virtualboy": 28,
    "pokemini": 24,
    "gameandwatch": 60,
    "gc": 16,
    "wii": 19,
    "wiiu": 20,
    # Sega
    "sg-1000": 33,
    "mastersystem": 11,
    "megadrive": 1,
    "gamegear": 15,
    "sega32x": 10,
    "segacd": 9,
    "saturn": 39,
    "dreamcast": 40,
    # Sony
    "psx": 12,
    "ps2": 21,
    "psp": 41,
    # Xbox
    "xbox": 22,
    # Atari
    "atari2600": 25,
    "atari5200": 50,
    "atari7800": 51,
    "atarilynx": 13,
    "atarijaguar": 17,
    "atarist": 36,
    # NEC / SNK / Bandai / altri
    "pcengine": 8,
    "pcenginecd": 76,
    "pcfx": 49,
    "neogeocd": 56,
    "ngp": 14,
    "ngpc": 14,
    "wonderswan": 53,
    "wonderswancolor": 53,
    "3do": 43,
    "coleco": 44,
    "intellivision": 45,
    "vectrex": 46,
    "videopac": 23,
    "channelf": 57,
    # Computer
    "c64": 30,
    "vic20": 34,
    "amiga": 35,
    "msx": 29,
    "msx2": 29,
    "amstradcpc": 37,
    "zxspectrum": 59,
    "zx81": 31,
    "x68000": 52,
    "pc98": 48,
    "dos": 26,
    # Arcade
    "arcade": 27,
    "fbneo": 27,
    "neogeo": 27,
    "cps1": 27,
    "cps2": 27,
    "cps3": 27,
    "naomi": 27,
    "atomiswave": 27,
}

# Console a cartuccia per cui l'hash RA e' l'MD5 diretto del file ROM (eventualmente senza header fisso)
_RAW_MD5_CONSOLES = {
    "gb", "gbc", "gba", "megadrive", "mastersystem", "gamegear", "sega32x", "sg-1000",
    "virtualboy", "pokemini", "ngp", "ngpc", "wonderswan", "wonderswancolor",
    "atari2600", "atari5200", "coleco", "intellivision", "vectrex", "videopac", "channelf",
}
_ARCADE_NAME_MD5 = {"arcade", "fbneo", "neogeo", "cps1", "cps2", "cps3", "naomi", "atomiswave"}

_SPECIAL_TAG_RE = re.compile(r"^~(hack|homebrew|prototype|unlicensed|demo|subset[^~]*)~\s*", re.IGNORECASE)
_SUBSET_SUFFIX_RE = re.compile(r"\s*\[subset\s*-\s*[^\]]+\]", re.IGNORECASE)


class RAError(Exception):
    """Errore di autenticazione, rete o parametri con la Web API di RetroAchievements."""


_last_client: RAClient | None = None


class RAClient:
    """Client leggero per RetroAchievements con cache in memoria per sessione."""

    def __init__(self, username: str, key: str, transport: httpx.BaseTransport | None = None,
                 timeout: float = 12.0):
        username = (username or "").strip()
        key = (key or "").strip()
        if not username or not key:
            raise RAError("Nome utente e Web API key di RetroAchievements sono obbligatori")
        self.username = username
        self.key = key
        self._http = httpx.Client(
            headers=UA,
            timeout=httpx.Timeout(timeout, connect=8.0),
            transport=transport,
            follow_redirects=True,
        )
        self._cache: dict[tuple, object] = {}

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> RAClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_json(self, endpoint: str, params: dict | None = None, use_cache: bool = True):
        q = {"y": self.key}
        if params:
            q.update({k: v for k, v in params.items() if v is not None})
        cache_key = (endpoint, tuple(sorted((k, str(v)) for k, v in q.items() if k != "y")))
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        try:
            r = self._http.get(url, params=q)
        except httpx.HTTPError as e:
            raise RAError(f"Errore di rete verso RetroAchievements: {e}") from e

        if r.status_code in (401, 403):
            raise RAError("Credenziali RetroAchievements non valide (verifica nome utente e Web API key)")
        if r.status_code in (404, 422):
            raise RAError(f"Utente o risorsa RetroAchievements non trovati (HTTP {r.status_code})")
        if r.status_code == 429:
            raise RAError("Limite di richieste RetroAchievements superato (HTTP 429): riprova piu' tardi")
        if r.status_code != 200:
            raise RAError(f"RetroAchievements ha risposto HTTP {r.status_code}: {r.text[:120]}")

        try:
            data = r.json()
        except ValueError as e:
            raise RAError("Risposta JSON non valida da RetroAchievements") from e

        if isinstance(data, dict) and data.get("errors"):
            msg = "; ".join(str(x.get("title") or x.get("detail") or x) for x in data["errors"] if isinstance(x, dict))
            raise RAError(msg or "Errore restituito da RetroAchievements")

        if use_cache:
            self._cache[cache_key] = data
        return data


def client(username: str, key: str, transport: httpx.BaseTransport | None = None,
           timeout: float = 12.0) -> RAClient:
    """Crea un client configurato per la Web API di RetroAchievements."""
    global _last_client
    c = RAClient(username, key, transport=transport, timeout=timeout)
    _last_client = c
    return c


def _resolve_client(c: RAClient | None) -> RAClient:
    if isinstance(c, RAClient):
        return c
    if _last_client is not None:
        return _last_client
    raise RAError("Nessun client RetroAchievements inizializzato: chiama prima client(username, key)")


def _media_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    return f"{MEDIA_URL}/{path.lstrip('/')}"


def console_ids(c: RAClient | None = None, active_only: bool = False, games_only: bool = False) -> list[dict]:
    """Elenco delle console supportate da RetroAchievements (`API_GetConsoleIDs.php`)."""
    rc = _resolve_client(c)
    params = {}
    if active_only:
        params["a"] = 1
    if games_only:
        params["g"] = 1
    raw = rc.get_json("API_GetConsoleIDs.php", params)
    out = []
    for item in (raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        out.append({
            "id": int(item.get("ID") or item.get("id") or 0),
            "name": str(item.get("Name") or item.get("name") or ""),
            "icon_url": item.get("IconURL") or item.get("iconUrl"),
            "active": bool(item.get("Active", item.get("active", True))),
            "is_game_system": bool(item.get("IsGameSystem", item.get("isGameSystem", True))),
        })
    return out


def user_progress(c: RAClient | None = None, username: str | None = None,
                  count: int = 500, offset: int = 0) -> list[dict]:
    """Progresso di completamento dell'utente su tutti i giochi giocati (`API_GetUserCompletionProgress.php`)."""
    if isinstance(c, str) and username is None:
        username, c = c, None
    rc = _resolve_client(c)
    target_user = (username or rc.username).strip()
    c_clamped = max(1, min(500, int(count)))
    raw = rc.get_json("API_GetUserCompletionProgress.php", {"u": target_user, "c": c_clamped, "o": int(offset)})
    results = (raw.get("Results") if isinstance(raw, dict) else None) or []
    out = []
    for r in results:
        if not isinstance(r, dict):
            continue
        gid = int(r.get("GameID") or r.get("gameId") or 0)
        max_p = int(r.get("MaxPossible") or r.get("maxPossible") or 0)
        awarded = int(r.get("NumAwarded") or r.get("numAwarded") or 0)
        awarded_hc = int(r.get("NumAwardedHardcore") or r.get("numAwardedHardcore") or 0)
        out.append({
            "game_id": gid,
            "title": str(r.get("Title") or r.get("title") or ""),
            "console_id": int(r.get("ConsoleID") or r.get("consoleId") or 0),
            "console_name": str(r.get("ConsoleName") or r.get("consoleName") or ""),
            "image_icon": _media_url(r.get("ImageIcon") or r.get("imageIcon")),
            "max_possible": max_p,
            "num_awarded": awarded,
            "num_awarded_hardcore": awarded_hc,
            "most_recent_awarded_date": r.get("MostRecentAwardedDate") or r.get("mostRecentAwardedDate"),
            "highest_award_kind": r.get("HighestAwardKind") or r.get("highestAwardKind"),
            "highest_award_date": r.get("HighestAwardDate") or r.get("highestAwardDate"),
        })
    return out


def game_progress(c_or_game_id: RAClient | int, game_id: int | None = None,
                  username: str | None = None, include_awards: bool = True,
                  *, client: RAClient | None = None) -> dict:
    """Dettaglio di un gioco e obiettivi sbloccati dall'utente (`API_GetGameInfoAndUserProgress.php`)."""
    if isinstance(c_or_game_id, RAClient):
        rc = c_or_game_id
        if game_id is None:
            raise ValueError("game_id obbligatorio")
        gid = int(game_id)
    else:
        rc = _resolve_client(client)
        gid = int(c_or_game_id)

    target_user = (username or rc.username).strip()
    params = {"u": target_user, "g": gid}
    if include_awards:
        params["a"] = 1
    raw = rc.get_json("API_GetGameInfoAndUserProgress.php", params)
    if not isinstance(raw, dict):
        raise RAError(f"Risposta inattesa per il gioco {gid}")

    ach_raw = raw.get("Achievements") or raw.get("achievements") or {}
    ach_items = ach_raw.values() if isinstance(ach_raw, dict) else (ach_raw if isinstance(ach_raw, list) else [])
    achievements = []
    for a in ach_items:
        if not isinstance(a, dict):
            continue
        badge = str(a.get("BadgeName") or a.get("badgeName") or "").strip()
        de = a.get("DateEarned") or a.get("dateEarned")
        de_hc = a.get("DateEarnedHardcore") or a.get("dateEarnedHardcore")
        achievements.append({
            "id": int(a.get("ID") or a.get("id") or 0),
            "title": str(a.get("Title") or a.get("title") or ""),
            "description": str(a.get("Description") or a.get("description") or ""),
            "points": int(a.get("Points") or a.get("points") or 0),
            "true_ratio": int(a.get("TrueRatio") or a.get("trueRatio") or 0),
            "author": a.get("Author") or a.get("author") or "",
            "badge_name": badge,
            "badge_url": f"{MEDIA_URL}/Badge/{badge}.png" if badge else None,
            "badge_locked_url": f"{MEDIA_URL}/Badge/{badge}_lock.png" if badge else None,
            "display_order": int(a.get("DisplayOrder") or a.get("displayOrder") or 0),
            "type": a.get("type") or a.get("Type"),
            "date_earned": de,
            "date_earned_hardcore": de_hc,
            "unlocked": bool(de or de_hc),
            "unlocked_hardcore": bool(de_hc),
        })
    achievements.sort(key=lambda x: (x["display_order"], x["id"]))

    return {
        "id": int(raw.get("ID") or raw.get("id") or gid),
        "title": str(raw.get("Title") or raw.get("title") or ""),
        "console_id": int(raw.get("ConsoleID") or raw.get("consoleId") or 0),
        "console_name": str(raw.get("ConsoleName") or raw.get("consoleName") or ""),
        "developer": raw.get("Developer") or raw.get("developer") or "",
        "publisher": raw.get("Publisher") or raw.get("publisher") or "",
        "genre": raw.get("Genre") or raw.get("genre") or "",
        "released": raw.get("Released") or raw.get("released"),
        "image_icon": _media_url(raw.get("ImageIcon") or raw.get("imageIcon")),
        "image_title": _media_url(raw.get("ImageTitle") or raw.get("imageTitle")),
        "image_ingame": _media_url(raw.get("ImageIngame") or raw.get("imageIngame")),
        "image_boxart": _media_url(raw.get("ImageBoxArt") or raw.get("imageBoxArt")),
        "num_achievements": int(raw.get("NumAchievements") or raw.get("numAchievements") or len(achievements)),
        "num_awarded": int(raw.get("NumAwardedToUser") or raw.get("numAwardedToUser") or 0),
        "num_awarded_hardcore": int(raw.get("NumAwardedToUserHardcore") or raw.get("numAwardedToUserHardcore") or 0),
        "completion": str(raw.get("UserCompletion") or raw.get("userCompletion") or "0.00%"),
        "completion_hardcore": str(raw.get("UserCompletionHardcore") or raw.get("userCompletionHardcore") or "0.00%"),
        "user_total_playtime": raw.get("UserTotalPlaytime") or raw.get("userTotalPlaytime"),
        "highest_award_kind": raw.get("HighestAwardKind") or raw.get("highestAwardKind"),
        "highest_award_date": raw.get("HighestAwardDate") or raw.get("highestAwardDate"),
        "achievements": achievements,
    }


def resolve_console_id(console: int | str) -> int:
    """Converte un id piattaforma di PozzoLauncher ('snes', 'megadrive'...) o un intero nell'id console RA."""
    if isinstance(console, int):
        return console
    s = str(console).strip().lower()
    if s.isdigit():
        return int(s)
    if s in CONSOLE_IDS:
        return CONSOLE_IDS[s]
    raise KeyError(f"Piattaforma {console!r} non presente in CONSOLE_IDS")


def game_list(c_or_console: RAClient | int | str, console_id: int | str | None = None,
              with_hashes: bool = True, only_with_achievements: bool = True,
              *, client: RAClient | None = None) -> list[dict]:
    """Elenco dei giochi di una console con gli hash supportati (`API_GetGameList.php?i=...&h=1&f=1`)."""
    if isinstance(c_or_console, RAClient):
        rc = c_or_console
        if console_id is None:
            raise ValueError("console_id obbligatorio")
        cid = resolve_console_id(console_id)
    else:
        rc = _resolve_client(client)
        cid = resolve_console_id(c_or_console)

    params = {
        "i": cid,
        "h": 1 if with_hashes else 0,
        "f": 1 if only_with_achievements else 0,
    }
    raw = rc.get_json("API_GetGameList.php", params)
    out = []
    for g in (raw if isinstance(raw, list) else []):
        if not isinstance(g, dict):
            continue
        hashes = g.get("Hashes") or g.get("hashes") or []
        out.append({
            "id": int(g.get("ID") or g.get("id") or 0),
            "title": str(g.get("Title") or g.get("title") or ""),
            "console_id": int(g.get("ConsoleID") or g.get("consoleId") or cid),
            "console_name": str(g.get("ConsoleName") or g.get("consoleName") or ""),
            "image_icon": _media_url(g.get("ImageIcon") or g.get("imageIcon")),
            "num_achievements": int(g.get("NumAchievements") or g.get("numAchievements") or 0),
            "num_leaderboards": int(g.get("NumLeaderboards") or g.get("numLeaderboards") or 0),
            "points": int(g.get("Points") or g.get("points") or 0),
            "date_modified": g.get("DateModified") or g.get("dateModified"),
            "forum_topic_id": g.get("ForumTopicID") or g.get("forumTopicId"),
            "hashes": [str(h).lower() for h in hashes if h],
        })
    return out


def rom_hash(path: Path, platform_id: str) -> str | None:
    """Calcola l'hash MD5 RetroAchievements per le console a cartuccia/arcade piu' comuni.

    Per sistemi su CD/DVD o con hashing parziale specifico (NDS, N64 byteswap, PS1, PS2, PSP...)
    ritorna None cosi' da usare l'abbinamento per titolo o un hash precalcolato.
    """
    pid = (platform_id or "").strip().lower()
    path = Path(path)
    if pid in _ARCADE_NAME_MD5:
        return hashlib.md5(path.stem.encode("ascii", "ignore")).hexdigest().lower()
    if not path.is_file() or path.suffix.lower() in (".zip", ".7z"):
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None

    if pid in _RAW_MD5_CONSOLES:
        return hashlib.md5(data).hexdigest().lower()
    if pid == "nes":
        if data[:4] == b"NES\x1a" and len(data) > 16:
            data = data[16:]
        return hashlib.md5(data).hexdigest().lower()
    if pid == "fds":
        if data[:4] == b"FDS\x1a" and len(data) > 16:
            data = data[16:]
        return hashlib.md5(data).hexdigest().lower()
    if pid in ("snes", "pcengine"):
        if len(data) > 512 and (len(data) % 1024) == 512:
            data = data[512:]
        return hashlib.md5(data).hexdigest().lower()
    if pid == "atari7800":
        if len(data) > 128 and data[1:10] == b"ATARI7800":
            data = data[128:]
        return hashlib.md5(data).hexdigest().lower()
    if pid == "atarilynx":
        if len(data) > 64 and data[:4] == b"LYNX":
            data = data[64:]
        return hashlib.md5(data).hexdigest().lower()
    return None


def _candidate_titles(ra_title: str) -> list[str]:
    """Estrae le varianti di titolo confrontabili da un titolo RetroAchievements."""
    t = _SPECIAL_TAG_RE.sub("", ra_title or "").strip()
    t = _SUBSET_SUFFIX_RE.sub("", t).strip()
    if not t:
        return []
    parts = [p.strip() for p in t.split("|") if p.strip()]
    return parts or [t]


def match_game(title: str, catalog: list[dict], rom_md5: str | None = None,
               min_score: float = 82.0) -> dict | None:
    """Abbina un gioco della libreria a una voce del catalogo RetroAchievements.

    1. Se `rom_md5` e' fornito, cerca prima la corrispondenza esatta tra gli `hashes` della voce.
    2. In mancanza (o se non trovato), usa `pozzo.matching.best` sul titolo normalizzato,
       escludendo ~Hack~/~Homebrew~/~Prototype~/~Subset~ a meno che non siano richiesti nel titolo cercato.
    """
    if rom_md5:
        target_h = rom_md5.strip().lower()
        for entry in catalog:
            hashes = entry.get("hashes") or entry.get("Hashes") or []
            if any(str(h).lower() == target_h for h in hashes):
                return entry

    q = (title or "").strip()
    if not q:
        return None
    wants_special = bool(_SPECIAL_TAG_RE.match(q)) or "hack" in q.lower() or "subset" in q.lower()

    filtered = []
    for entry in catalog:
        raw_t = str(entry.get("title") or entry.get("Title") or "")
        if not raw_t:
            continue
        is_special = bool(_SPECIAL_TAG_RE.match(raw_t)) or bool(_SUBSET_SUFFIX_RE.search(raw_t))
        if is_special and not wants_special:
            continue
        filtered.append(entry)

    winner, _ = matching.best(
        q,
        filtered,
        key=lambda e: _candidate_titles(str(e.get("title") or e.get("Title") or "")),
        min_score=min_score,
    )
    return winner
