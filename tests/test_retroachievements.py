"""Test offline per il modulo RetroAchievements (gamescraper/retroachievements.py).

Usa httpx.MockTransport e le fixture nel formato documentato da api-docs.retroachievements.org.

Esegui: python tests/test_retroachievements.py
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from gamescraper import platforms as P  # noqa: E402
from gamescraper import retroachievements as ra  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_console_ids_mapping():
    expected = {
        "megadrive": 1, "n64": 2, "snes": 3, "gb": 4, "gba": 5, "gbc": 6, "nes": 7,
        "pcengine": 8, "segacd": 9, "sega32x": 10, "mastersystem": 11, "psx": 12,
        "atarilynx": 13, "ngp": 14, "gamegear": 15, "gc": 16, "atarijaguar": 17,
        "nds": 18, "ps2": 21, "saturn": 39, "dreamcast": 40, "psp": 41,
    }
    for pid, cid in expected.items():
        assert pid in P.PLATFORMS, f"{pid} non presente in platforms.py"
        assert ra.CONSOLE_IDS.get(pid) == cid, (pid, ra.CONSOLE_IDS.get(pid))
        assert ra.resolve_console_id(pid) == cid
    print("ok  CONSOLE_IDS verificato rispetto a platforms.py e RAWeb")


def test_endpoints_and_session_cache():
    consoles_body = (FIXTURES / "ra_consoles.json").read_text(encoding="utf-8")
    gamelist_body = (FIXTURES / "ra_gamelist.json").read_text(encoding="utf-8")
    uprog_body = (FIXTURES / "ra_user_progress.json").read_text(encoding="utf-8")
    gprog_body = (FIXTURES / "ra_game_progress.json").read_text(encoding="utf-8")

    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        calls.append((request.url.path, q))
        if q.get("y") != "FAKE_RA_KEY":
            return httpx.Response(401, json={"message": "Unauthorized"})
        path = request.url.path
        if path.endswith("/API_GetConsoleIDs.php"):
            return httpx.Response(200, text=consoles_body, headers={"content-type": "application/json"})
        if path.endswith("/API_GetGameList.php"):
            assert q.get("i") == "1"
            assert q.get("h") == "1"
            return httpx.Response(200, text=gamelist_body, headers={"content-type": "application/json"})
        if path.endswith("/API_GetUserCompletionProgress.php"):
            assert q.get("u") == "TestPlayer"
            return httpx.Response(200, text=uprog_body, headers={"content-type": "application/json"})
        if path.endswith("/API_GetGameInfoAndUserProgress.php"):
            assert q.get("u") == "TestPlayer"
            assert q.get("g") == "1"
            return httpx.Response(200, text=gprog_body, headers={"content-type": "application/json"})
        return httpx.Response(404)

    with ra.client("TestPlayer", "FAKE_RA_KEY", transport=httpx.MockTransport(handler)) as c:
        systems = ra.console_ids(c)
        assert len(systems) == 4 and systems[0]["id"] == 1 and systems[0]["name"] == "Mega Drive"

        games = ra.game_list(c, "megadrive")
        assert len(games) == 3
        assert games[0]["id"] == 3684
        assert "8bd4a97783cda077c342173df0a9b51e" in games[0]["hashes"]

        # Seconda chiamata identica servita dalla cache in memoria della sessione
        n_before = len(calls)
        games_cached = ra.game_list("megadrive")
        assert len(calls) == n_before, "la seconda chiamata a game_list non doveva interrogare la rete"
        assert games_cached == games

        uprog = ra.user_progress(c)
        assert len(uprog) == 2
        assert uprog[0]["game_id"] == 1
        assert uprog[0]["highest_award_kind"] == "mastered"
        assert uprog[0]["num_awarded_hardcore"] == 23

        gprog = ra.game_progress(c, 1)
        assert gprog["id"] == 1
        assert gprog["title"] == "Sonic the Hedgehog"
        assert gprog["completion_hardcore"] == "50.00%"
        assert len(gprog["achievements"]) == 2
        a0, a1 = gprog["achievements"]
        assert a0["id"] == 9 and a0["unlocked"] is True and a0["unlocked_hardcore"] is True
        assert a0["badge_url"] == "https://media.retroachievements.org/Badge/250336.png"
        assert a1["id"] == 10 and a1["unlocked"] is False

    print("ok  endpoint console_ids, game_list, user_progress, game_progress e cache di sessione")


def test_matching_and_rom_hash():
    gamelist_body = (FIXTURES / "ra_gamelist.json").read_text(encoding="utf-8")
    with ra.client("TestPlayer", "FAKE_RA_KEY",
                   transport=httpx.MockTransport(lambda req: httpx.Response(200, text=gamelist_body))) as c:
        catalog = ra.game_list(c, 1)

    # 1. Abbinamento per hash MD5 esatto
    by_hash = ra.match_game("Qualsiasi Titolo", catalog, rom_md5="a13ab653a20fb383337fab1e52ddb0df")
    assert by_hash is not None and by_hash["id"] == 3684, by_hash

    # 2. Abbinamento per titolo normalizzato (ignora ~Hack~ anche se contiene "Sonic the Hedgehog")
    by_title = ra.match_game("Sonic The Hedgehog (USA, Europe)", catalog)
    assert by_title is not None and by_title["id"] == 1, by_title

    # 3. Se si cerca esplicitamente l'hack, viene trovato
    by_hack = ra.match_game("~Hack~ Knuckles the Echidna in Sonic the Hedgehog", catalog)
    assert by_hack is not None and by_hack["id"] == 20246, by_hack

    # 4. Calcolo hash ROM per cartuccia normale (Mega Drive) e NES con header iNES (16 byte)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        payload = b"ROM_PAYLOAD_BYTES_123456"
        expected_md5 = hashlib.md5(payload).hexdigest()

        md_rom = tmp / "Sonic.md"
        md_rom.write_bytes(payload)
        assert ra.rom_hash(md_rom, "megadrive") == expected_md5

        nes_rom = tmp / "Mario.nes"
        nes_rom.write_bytes(b"NES\x1a" + b"\x00" * 12 + payload)
        assert ra.rom_hash(nes_rom, "nes") == expected_md5

        arcade_rom = tmp / "mslug.zip"
        arcade_rom.write_bytes(b"PK\x03\x04")
        assert ra.rom_hash(arcade_rom, "neogeo") == hashlib.md5(b"mslug").hexdigest()

    print("ok  match_game (per hash e per titolo normalizzato) e rom_hash (cartucce, iNES e arcade)")


def test_errors():
    try:
        ra.client("", "key")
        raise AssertionError("doveva rifiutare username vuoto")
    except ra.RAError:
        pass

    with ra.client("TestPlayer", "WRONG_KEY",
                   transport=httpx.MockTransport(lambda req: httpx.Response(401, text="Unauthorized"))) as c:
        try:
            ra.user_progress(c)
            raise AssertionError("doveva sollevare RAError su HTTP 401")
        except ra.RAError as e:
            assert "Credenziali" in str(e), e

    with ra.client("TestPlayer", "KEY",
                   transport=httpx.MockTransport(lambda req: httpx.Response(429, text="Too Many Requests"))) as c:
        try:
            ra.user_progress(c)
            raise AssertionError("doveva sollevare RAError su HTTP 429")
        except ra.RAError as e:
            assert "429" in str(e), e

    print("ok  gestione errori (credenziali mancanti, HTTP 401 e HTTP 429)")


if __name__ == "__main__":
    test_console_ids_mapping()
    test_endpoints_and_session_cache()
    test_matching_and_rom_hash()
    test_errors()
    print("TUTTI I TEST OK")
