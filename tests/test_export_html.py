"""Esportazione della libreria come pagina web statica (index.html + covers/). Nessuna rete."""
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamescraper import frontends as F  # noqa: E402


def _library(cache: Path, rows: list[dict]) -> None:
    """Crea un library.db minimo con le colonne usate da _load_primary_games."""
    cache.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cache / "library.db")
    con.executescript("""
        CREATE TABLE games (key TEXT, platform TEXT, title TEXT, sort_title TEXT, year INT,
          release_date TEXT, developer TEXT, publisher TEXT, genres TEXT, players TEXT, rating REAL,
          description TEXT, files TEXT, media TEXT, is_primary INT, store TEXT);
    """)
    for r in rows:
        con.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            r["key"], r["platform"], r["title"], r.get("sort_title", r["title"]), r.get("year"),
            r.get("release_date", ""), r.get("developer", ""), r.get("publisher", ""),
            json.dumps(r.get("genres", [])), r.get("players", ""), r.get("rating"),
            r.get("description", ""), json.dumps(r.get("files", [])), json.dumps(r.get("media", {})),
            1, None))
    con.commit()
    con.close()


def test_pagina_generata(tmp_path):
    cache = tmp_path / "cache"
    cover = cache / "media" / "cover" / "zelda.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"\x89PNG finta")
    _library(cache, [
        {"key": "switch/zelda", "platform": "switch", "title": "Zelda", "year": 2023,
         "developer": "Nintendo", "genres": ["Avventura"], "players": "1", "rating": 0.95,
         "description": "Un'avventura <con> caratteri & strani", "files": ["/roms/switch/Zelda.nsp"],
         "media": {"cover": "media/cover/zelda.png"}},
        {"key": "snes/mario", "platform": "snes", "title": "Mario", "year": 1990, "files": ["/roms/snes/Mario.sfc"]},
    ])
    out = tmp_path / "web"
    summary = F.export_html(cache, out, title="Prova <b>")
    assert summary == {"snes": 1, "switch": 1}, summary

    html = (out / "index.html").read_text(encoding="utf-8")
    assert "<title>Prova &lt;b&gt;</title>" in html          # titolo passato dall'utente, escapato
    assert "window.__GAMES__" in html
    assert "covers/switch-Zelda.png" in html                 # copertina copiata e linkata
    assert (out / "covers" / "switch-Zelda.png").is_file()
    # niente richieste esterne: la pagina si apre anche con file://
    assert "http://" not in html and "https://" not in html
    assert "<style>" in html and "IntersectionObserver" in html
    # i dati sono JSON valido e con i campi attesi
    start = html.index("window.__GAMES__ = ") + len("window.__GAMES__ = ")
    end = html.index(";</script>", start)
    data = json.loads(html[start:end])
    by = {g["t"]: g for g in data}
    assert by["Zelda"]["platName"] == "Nintendo Switch" or by["Zelda"]["platName"] == "switch"
    assert by["Zelda"]["rating"] == 0.95 and by["Zelda"]["cover"].endswith(".png")
    assert by["Mario"]["cover"] == ""                        # senza copertina: segnaposto colorato
    print("ok  pagina web: index.html + copertine copiate, dati JSON, nessuna risorsa esterna")


def test_filtro_console(tmp_path):
    cache = tmp_path / "cache"
    _library(cache, [
        {"key": "switch/a", "platform": "switch", "title": "A", "files": ["/r/a.nsp"]},
        {"key": "snes/b", "platform": "snes", "title": "B", "files": ["/r/b.sfc"]},
    ])
    out = tmp_path / "web"
    summary = F.export_html(cache, out, ["switch"])
    assert summary == {"switch": 1}, summary
    assert ">B<" not in (out / "index.html").read_text(encoding="utf-8")
    print("ok  filtro per console rispettato")


def test_libreria_assente(tmp_path):
    out = tmp_path / "web"
    summary = F.export_html(tmp_path / "vuota", out)
    assert summary == {}
    assert (out / "index.html").is_file()  # pagina vuota ma valida
    print("ok  libreria assente: pagina vuota, nessun errore")


def test_doppioni_nomi_copertina(tmp_path):
    """Due giochi con lo stesso nome file: la seconda copertina non sovrascrive la prima."""
    cache = tmp_path / "cache"
    for i, plat in enumerate(("switch", "ps2")):
        c = cache / "media" / f"c{i}.png"
        c.parent.mkdir(parents=True, exist_ok=True)
        c.write_bytes(b"x")
    _library(cache, [
        {"key": "switch/g", "platform": "switch", "title": "Gioco", "files": ["/r/Gioco.nsp"],
         "media": {"cover": "media/c0.png"}},
        {"key": "ps2/g", "platform": "ps2", "title": "Gioco", "files": ["/r/Gioco.iso"],
         "media": {"cover": "media/c0.png"}},
    ])
    out = tmp_path / "web"
    F.export_html(cache, out)
    covers = sorted(p.name for p in (out / "covers").iterdir())
    assert len(covers) == 2, covers  # nomi diversi (switch-Gioco, ps2-Gioco)
    print("ok  copertine con nomi unici:", covers)


if __name__ == "__main__":
    import tempfile
    for fn in (test_pagina_generata, test_filtro_console, test_libreria_assente, test_doppioni_nomi_copertina):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fn(Path(t))
    print("TUTTI I TEST OK")
