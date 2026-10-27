"""Test end-to-end della pipeline a catena con API IGDB / ScreenScraper / TheGamesDB simulate (nessuna rete).

Il finto IGDB si comporta come quello reale (verificato):
  - /multiquery con `search`  -> lista vuota
  - /multiquery con `where name ~ "..."` -> risultati per nome esatto
  - /games con `search` -> ricerca fuzzy

Esegui: python tests/test_pipeline.py
"""
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from rich.progress import Progress  # noqa: E402

from gamescraper import config, net, pipeline, platforms as P  # noqa: E402
from gamescraper.cache import Cache  # noqa: E402
from gamescraper.scanner import Game  # noqa: E402

# senza attese fra le richieste: col ritmo vero (ScreenScraper < 1 al secondo) il test durava oltre 2 minuti
net.RateLimiter.interval = property(lambda self: 0, lambda self, v: None)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 1000


def make_handler(calls, ss_quota=20000):
    def igdb_game(i, name):
        return {"id": i, "name": name, "summary": "desc " + name, "first_release_date": 694224000,
                "genres": [{"name": "Platform"}],
                "involved_companies": [{"company": {"name": "Dev"}, "developer": True},
                                       {"company": {"name": "Pub"}, "publisher": True}],
                "total_rating": 87.5, "cover": {"image_id": f"c{i}"}, "game_modes": [{"slug": "single-player"}]}

    def handler(req: httpx.Request):
        u = str(req.url)
        if "id.twitch.tv" in u:
            return httpx.Response(200, json={"access_token": "T", "expires_in": 5000000})
        if "api.igdb.com/v4/multiquery" in u:
            calls["mq"] += 1
            out = []
            for q in re.findall(r'query games "(\d+)" \{(.*?)\};', req.content.decode()):
                idx, body = q
                if "search" in body:  # come l'API reale: search non funziona in multiquery
                    continue
                name = re.search(r'name ~ "([^"]*)"', body).group(1)
                res = [] if name.startswith(("Unknown", "Fuzzy")) else [igdb_game(int(idx), name)]
                out.append({"name": idx, "result": res})
            return httpx.Response(200, json=out)
        if "api.igdb.com/v4/games" in u:
            calls["search"] += 1
            name = re.search(r'search "([^"]*)"', req.content.decode()).group(1)
            if name.startswith("Fuzzy"):
                return httpx.Response(200, json=[igdb_game(999, name.replace("Fuzzy", "Fuzzy:"))])
            return httpx.Response(200, json=[])
        if "ssuserInfos" in u:
            return httpx.Response(200, json={"response": {"ssuser": {
                "id": "me", "maxthreads": "4", "requeststoday": "0", "maxrequestsperday": str(ss_quota)}}})
        if "jeuInfos" in u:
            calls["ss"] += 1
            if "Unknown" in u:
                return httpx.Response(404, text="Erreur : Rom/Iso/Dossier non trouvée !")
            return httpx.Response(200, json={"response": {
                "ssuser": {"requeststoday": str(calls["ss"]), "maxrequestsperday": str(ss_quota)},
                "jeu": {"id": "1", "noms": [{"region": "ss", "text": "SS Title"}],
                        "synopsis": [{"langue": "it", "text": "descrizione IT"}],
                        "medias": [{"type": "wheel-hd", "region": "wor", "url": "https://ss.media/wheel.png"},
                                   {"type": "box-2D", "region": "eu", "url": "https://ss.media/box.png"}],
                        "genres": [{"noms": [{"langue": "it", "text": "Piattaforme"}]}], "note": {"text": "16"}}}})
        if "jeuRecherche" in u:
            calls["ss"] += 1
            return httpx.Response(200, json={"response": {"jeux": [{}]}})
        if "images.igdb.com" in u or "ss.media" in u:
            calls["img"] += 1
            return httpx.Response(200, content=PNG)
        return httpx.Response(404)
    return handler


def make_games(n=35):
    snes = P.get("snes")
    games = [Game(Path(f"Game {i}.sfc"), snes, 100 + i, 0, crc=f"{i:08X}") for i in range(n)]
    games.append(Game(Path("Unknown Thing.sfc"), snes, 1, 0, crc="FFFFFFFF"))
    games.append(Game(Path("Fuzzy Name.sfc"), snes, 2, 0, crc="FFFFFFFE"))
    return games


async def run(cfg, cache, games, calls, **kw):
    pipeline.make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(make_handler(calls, **kw)))
    with Progress(disable=True) as prog:
        await pipeline.scrape(cfg, cache, games, cfg.sources, False, prog, lambda *a: None)


async def test_cascade(tmp):
    cfg = config.Config(cache=Path(tmp) / "cache", output=Path(tmp) / "out")
    cfg.igdb = {"client_id": "x", "client_secret": "y"}
    cfg.ss = {"devid": "d", "devpassword": "p", "user": "u", "password": "pw"}
    cfg.media = ["cover", "wheel"]
    cfg.sources = ["igdb", "screenscraper"]
    cfg.field_priority = {"description": ["screenscraper", "igdb"]}
    cache = Cache(cfg.cache)
    calls = {"mq": 0, "search": 0, "ss": 0, "img": 0}
    games = make_games()
    await run(cfg, cache, games, calls)
    g = games[0]
    assert calls["mq"] == 4, calls  # 37 giochi -> 4 multiquery da 10
    assert calls["search"] == 2, calls  # ricerca fuzzy solo per i 2 senza nome esatto
    assert g.data["title"] == "Game 0" and g.origin["title"] == "igdb"
    assert g.data["description"] == "descrizione IT"  # priorita' per campo
    assert g.origin["media:wheel"] == "screenscraper" and g.origin["media:cover"] == "igdb"
    assert (cfg.cache / g.data["media:wheel"]).exists()
    fuzzy = games[-1]
    assert fuzzy.origin.get("title") == "igdb", fuzzy.data  # trovato dalla ricerca fuzzy

    # seconda esecuzione: niente da rifare
    calls.update(mq=0, search=0, ss=0)
    games2 = make_games()
    await run(cfg, cache, games2, calls)
    assert calls["mq"] == 0 and calls["search"] == 0 and calls["ss"] == 0, calls
    assert games2[0].data["media:wheel"]
    cache.close()
    print("ok  cascata IGDB -> ScreenScraper, priorita' per campo, ripresa dalla cache")


async def test_quota_stop(tmp):
    cfg = config.Config(cache=Path(tmp) / "cache2", output=Path(tmp) / "out2")
    cfg.ss = {"devid": "d", "devpassword": "p", "user": "u", "password": "pw", "quota_reserve": 0}
    cfg.media = ["wheel"]
    cfg.sources = ["screenscraper"]
    cache = Cache(cfg.cache)
    calls = {"mq": 0, "search": 0, "ss": 0, "img": 0}
    games = make_games()
    await run(cfg, cache, games, calls, ss_quota=10)
    found = sum(1 for g in games if g.data.get("media:wheel"))
    assert calls["ss"] <= 12, calls  # si ferma alla quota (piu' le richieste gia' in volo)
    assert 0 < found < len(games)
    status = cache.lookup_status([g.key for g in games], "screenscraper")
    assert len(status) < len(games)  # i giochi non interrogati restano da fare
    cache.close()
    print(f"ok  quota ScreenScraper: fermo dopo {calls['ss']} richieste, {len(games) - len(status)} giochi rimandati")


async def test_ko_limit_and_secondary(tmp):
    """Limite giornaliero dei 'non trovati' + versioni secondarie non mandate a ScreenScraper."""
    import sqlite3
    from gamescraper.library.build import SCHEMA
    cfg = config.Config(cache=Path(tmp) / "cache3", output=Path(tmp) / "out3")
    cfg.ss = {"devid": "d", "devpassword": "p", "user": "u", "password": "pw", "ko_reserve": 0}
    cfg.media = ["wheel"]
    cfg.sources = ["screenscraper"]
    cache = Cache(cfg.cache)
    snes = P.get("snes")
    unknown = [Game(Path(f"Unknown {i}.sfc"), snes, 10 + i, 0, crc=f"AA{i:06X}") for i in range(20)]
    secondary = Game(Path("Game 0 (Japan).sfc"), snes, 999, 0, crc="BBBBBBBB")
    # libreria finta in cui "Game 0 (Japan)" e' una versione secondaria
    db = sqlite3.connect(cfg.cache / "library.db")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO games(key, is_primary) VALUES(?, 0)", (secondary.key,))
    db.commit()
    db.close()
    calls = {"mq": 0, "search": 0, "ss": 0, "img": 0}
    base = make_handler(calls)

    def handler(req):  # account con soli 5 'non trovati' al giorno
        if "ssuserInfos" in str(req.url):
            return httpx.Response(200, json={"response": {"ssuser": {"id": "me", "maxthreads": "1", "requeststoday": "0",
                                  "maxrequestsperday": "20000", "requestskotoday": "0", "maxrequestskoperday": "5"}}})
        return base(req)

    pipeline.make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    games = unknown + [secondary]
    with Progress(disable=True) as prog:
        await pipeline.scrape(cfg, cache, games, cfg.sources, False, prog, lambda *a: None)
    jeu_calls = calls["ss"]
    assert jeu_calls <= 6, calls  # si ferma ai 'non trovati' consentiti (5) senza superarli
    status = cache.lookup_status([g.key for g in games], "screenscraper")
    assert secondary.key not in status, "la versione secondaria non doveva essere interrogata"
    cache.close()
    print(f"ok  limite 'non trovati': fermo dopo {jeu_calls} richieste; versione secondaria saltata")


async def test_retry_missing(tmp):
    """'Completa i dati mancanti': riprova le fonti che non avevano trovato il gioco, non quelle che l'avevano trovato."""
    cfg = config.Config(cache=Path(tmp) / "cache4", output=Path(tmp) / "out4")
    cfg.igdb = {"client_id": "x", "client_secret": "y"}
    cfg.media = ["cover"]
    cfg.sources = ["igdb"]
    cache = Cache(cfg.cache)
    snes = P.get("snes")
    found = Game(Path("Game 1.sfc"), snes, 101, 0, crc="00000001")
    was_missing = Game(Path("Game 2.sfc"), snes, 102, 0, crc="00000002")
    cache.set_lookup(found.key, "igdb", "found")
    cache.put(found.key, "igdb", "title", "Game 1")
    cache.set_lookup(was_missing.key, "igdb", "notfound")  # es. cercato in passato con un altro nome
    cache.commit()
    calls = {"mq": 0, "search": 0, "ss": 0, "img": 0}
    await run(cfg, cache, [found, was_missing], calls)
    assert calls["mq"] == 0, calls  # modalita' normale: niente da rifare
    pipeline.make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(make_handler(calls)))
    games = [Game(g.path, snes, g.size, 0, crc=g.crc) for g in (found, was_missing)]
    with Progress(disable=True) as prog:
        await pipeline.scrape(cfg, cache, games, cfg.sources, False, prog, lambda *a: None, retry_missing=True)
    assert calls["mq"] == 1, calls
    assert games[1].data.get("title") == "Game 2" and games[1].origin["title"] == "igdb"
    cache.close()
    print("ok  completa i dati mancanti: riprovato solo il gioco 'non trovato'")


def test_lock(tmp):
    lock_dir = Path(tmp) / "lock"
    lock_dir.mkdir()
    with pipeline.ScrapeLock(lock_dir):
        try:
            with pipeline.ScrapeLock(lock_dir):
                raise AssertionError("il secondo scraping doveva essere bloccato")
        except RuntimeError:
            pass
    with pipeline.ScrapeLock(lock_dir):  # rilasciato: si puo' ripartire
        pass
    print("ok  due scraping contemporanei impediti")


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
    asyncio.run(test_cascade(t))
    asyncio.run(test_quota_stop(t))
    asyncio.run(test_ko_limit_and_secondary(t))
    asyncio.run(test_retry_missing(t))
    test_lock(t)
print("TUTTI I TEST OK")
