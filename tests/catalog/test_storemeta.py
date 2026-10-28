"""Test della lettura di copertina/descrizione dalle pagine degli store. Nessuna rete.

Esegui: PYTHONUTF8=1 python tests/catalog/test_storemeta.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pozzo import storemeta  # noqa: E402

URL_NX = "https://nxbrew.net/zumba-world-the-marble-monster-adventure-switch-nsp-eshop/"
URL_ZP = "https://www.ziperto.com/esempio-switch-nsp/"

# stessa struttura della pagina reale: og:image, blocco metadati + "Description:", segnaposto 200x200
HTML_NXBREW = """<html><head>
<meta property="og:title" content="Zumba World - The Marble Monster Adventure Switch NSP (eShop) - NXBrew.net">
<meta property="og:image" content="https://nxbrew.net/wp-content/uploads/2026/05/Zumba-World.jpg">
<meta name="description" content="Title: Zumba WorldRelease Date: 2026-05-07">
</head><body><article class="post"><div class="entry">
  <figure class="wp-block-media-text__media"><img src="https://nxbrew.net/wp-content/uploads/2026/05/Zumba-World.jpg"></figure>
  <div class="wp-block-media-text__content">
    <p>Title: Zumba World – The Marble Monster Adventure Release Date: 2026-05-07 Category: Action, Puzzle
       Publisher: EpiXR Games Type: eShop Format: NSP Title ID: 0100BFC02401E000 Title Size: 996 MB
       Language: En, Ja, Pt, Es, Fr, De, It, Nl, Ru, Ko, Zh Download Now Description: Zumba World – The Monster
       Marble Adventure fuses classic marble-shooter action with an explorable hub world! You play as a fearless
       dinosaur hero carrying colorful marbles in your snout. Switch Zumba World – The Marble Monster Adventure NSP
       Download Links Base Game NSP (996 MB)</p>
    <img class="wp-post-image" src="https://nxbrew.net/wp-content/uploads/2026/10/0100D830204E0000-200x200.png">
    <img src="https://nxbrew.net/wp-content/uploads/2026/05/schermata1.jpg">
  </div>
</div></article></body></html>"""

HTML_ZIPERTO = """<html><head><title>Gioco Di Prova Switch NSP | Ziperto</title></head><body>
<div class="entry-content">
  <p><img class="aligncenter" src="/wp-content/uploads/2024/01/copertina.png"></p>
  <p>Questa è la descrizione completa del gioco di prova: si tratta di un platform con livelli a tema,
     modalità cooperativa e una colonna sonora originale che accompagna tutta l'avventura.</p>
  <p><a href="https://filecrypt.cc/Container/ABC.html">Download</a></p>
</div></body></html>"""


def test_nxbrew_detail():
    d = storemeta.parse_detail(HTML_NXBREW, URL_NX)
    assert d["title"] == "Zumba World - The Marble Monster Adventure", d["title"]
    assert d["cover"] == "https://nxbrew.net/wp-content/uploads/2026/05/Zumba-World.jpg", d["cover"]
    assert d["description"].startswith("Zumba World – The Monster Marble Adventure"), d["description"]
    assert d["title_id"] == "0100BFC02401E000", d.get("title_id")
    assert d["release_date"] == "2026-05-07", d.get("release_date")
    assert d["publisher"] == "EpiXR Games", d.get("publisher")
    assert d["size"] == 996 * 1024**2, d.get("size")
    assert d["genres"] == ["Action", "Puzzle"], d.get("genres")
    assert d["languages"][:3] == ["En", "Ja", "Pt"], d.get("languages")
    assert d["screenshots"] == ["https://nxbrew.net/wp-content/uploads/2026/05/schermata1.jpg"], d["screenshots"]
    print(f"ok  nxbrew: titolo, copertina, descrizione ({len(d['description'])} caratteri), metadati")


def test_nxbrew_placeholder_ignored():
    """Se og:image e' un segnaposto si passa ai selettori dell'articolo."""
    html = HTML_NXBREW.replace('content="https://nxbrew.net/wp-content/uploads/2026/05/Zumba-World.jpg"',
                               'content="https://nxbrew.net/wp-content/uploads/2026/10/0100D830204E0000-200x200.png"')
    d = storemeta.parse_detail(html, URL_NX)
    assert d["cover"].endswith("Zumba-World.jpg"), d["cover"]
    print("ok  segnaposto 200x200 scartato, copertina presa dall'articolo")


def test_ziperto_detail():
    d = storemeta.parse_detail(HTML_ZIPERTO, URL_ZP)
    assert d["cover"] == "https://www.ziperto.com/wp-content/uploads/2024/01/copertina.png", d["cover"]
    assert d["description"].startswith("Questa è la descrizione completa"), d["description"]
    assert "Download" not in d["description"], "il link di download non deve finire nella descrizione"
    assert d["title"] == "Gioco Di Prova", d["title"]
    print(f"ok  ziperto: copertina relativa risolta e descrizione dai paragrafi")


def test_site_suffix_only_for_domains():
    """«- NXBrew.net» si toglie, «- Part 2» (parte del titolo) no."""
    html = HTML_NXBREW.replace(
        'content="Zumba World - The Marble Monster Adventure Switch NSP (eShop) - NXBrew.net"',
        'content="Half-Life - Part 2 Switch NSP"')
    d = storemeta.parse_detail(html, URL_NX)
    assert d["title"] == "Half-Life - Part 2", d["title"]
    print("ok  titolo: il suffisso del sito si toglie solo se e' un dominio")


def test_clean_and_similarity():
    assert storemeta.clean_title("Zumba World - The Marble Adventure Switch NSP (eShop)") == "Zumba World - The Marble Adventure"
    assert storemeta.clean_title("Gioco v1.2.0 XCI") == "Gioco"
    assert round(storemeta.similarity("Silk", "Silk Road"), 2) == 0.5, "risultato troppo diverso: va scartato"
    assert storemeta.similarity("Zelda Tears of the Kingdom", "The Legend of Zelda Tears of the Kingdom NSP") >= 0.6
    assert storemeta.similarity("Gioco", "Altro Gioco") < 0.6
    print("ok  titoli: pulizia e somiglianza")


def test_best_match():
    html = """<div><h2 class="post-title"><a href="/the-legend-of-zelda-tears-of-the-kingdom-switch/">
      The Legend of Zelda Tears of the Kingdom Switch NSP</a></h2>
      <h2 class="post-title"><a href="/zelda-2/">Zelda 2</a></h2></div>"""
    hit = storemeta.best_match(html, "Zelda Tears of the Kingdom", "https://www.ziperto.com/", "ziperto.com")
    assert hit == "https://www.ziperto.com/the-legend-of-zelda-tears-of-the-kingdom-switch/", hit
    assert storemeta.best_match(html, "Silk", "https://www.ziperto.com/", "ziperto.com") is None
    assert storemeta.search_url("ziperto.com", "Zelda: Tears of the Kingdom") == \
        "https://www.ziperto.com/?s=Zelda%3A+Tears+of+the+Kingdom"
    print("ok  ricerca: miglior risultato scelto, risultati deboli scartati")


HTML_SUPERPSX = """<html><head>
<meta property="og:title" content="show21p4-437">
<meta property="og:description" content="MLB The Show 21 is a baseball video game developed by San Diego Studio">
<meta property="og:image" content="https://www.superpsx.com/wp-content/uploads/2024/09/MLB-PS4-PKG.webp">
</head><body><div class="entry-content">
  <div class="spsx-pt-pane"><p>MLB The Show 21 is a baseball video game developed by San Diego Studio and published by
     Sony Interactive Entertainment. The game was released for PlayStation 4 on April 20, 2021. This installment marks
     the 16th entry in the MLB The Show series.</p></div>
  <div class="spsx-pt-pane"><dl class="spsx-specs"><dt>Game Name</dt><dd>MLB The Show 21</dd><dt>Platform</dt>
     <dd>PlayStation 4</dd><dt>Genre</dt><dd>Sports</dd><dt>Mode</dt><dd>Single-player, multiplayer</dd>
     <dt>Release Date</dt><dd>April 20, 2021</dd><dt>Size</dt><dd>50 GB</dd>
     <dt>Version</dt><dd>CUSA25016 - USA</dd></dl></div>
</div></body></html>"""
URL_SPX = "https://www.superpsx.com/show21p4-437/"


def test_superpsx_detail():
    """Scheda con i dati in un elenco <dt>/<dd> invece che in righe «Chiave: valore»."""
    d = storemeta.parse_detail(HTML_SUPERPSX, URL_SPX)
    assert d["cover"].endswith("MLB-PS4-PKG.webp"), d["cover"]
    assert d["description"].startswith("MLB The Show 21 is a baseball video game"), d["description"]
    assert d["genres"] == ["Sports"], d.get("genres")
    assert d["release_date"] == "2021-04-20", d.get("release_date")
    assert d["size"] == 50 * 1024**3, d.get("size")
    print("ok  superpsx: copertina, descrizione, generi, data e peso dall'elenco <dt>/<dd>")


def test_fill_reads_archives(tmp_path):
    """Anche gli elenchi tipo archivio (type_c, es. superpsx) entrano nel giro copertine/descrizioni."""
    import asyncio
    import time as _time
    from types import SimpleNamespace
    from pozzo.cache import Cache
    cache = Cache(tmp_path)
    page = "https://www.superpsx.com/category/ps4/"
    cache.kv_set(f"catalog:type_c:{page}",
                 {"url": page, "index_url": page, "pid": "ps4", "time": _time.time(),
                  "entries": [{"name": "MLB The Show 21 PS4", "size": None, "url": URL_SPX, "magnet": None}]})
    cache.commit()
    cfg = SimpleNamespace(catalogs={"ps4": [page]}, limits={})
    seen = []

    async def fake_detail(cfg, cache, client, url, log=print, limiter=None):
        seen.append(url)
        return storemeta.put(cache, url, storemeta.parse_detail(HTML_SUPERPSX, url), "page")

    real = storemeta.fetch_detail
    storemeta.fetch_detail = fake_detail
    try:
        stats = asyncio.run(storemeta.fill(cfg, cache, None, log=lambda m: None, search_site=None))
    finally:
        storemeta.fetch_detail = real
    assert seen == [URL_SPX], seen
    assert stats["games"] == 1 and stats["complete"] == 1, stats
    assert storemeta.cached(cache, URL_SPX)["genres"] == ["Sports"]
    cache.close()
    print("ok  fill: anche gli elenchi type_c vengono completati (1 gioco, copertina e descrizione)")


def test_cache_roundtrip(tmp_path):
    from pozzo.cache import Cache
    cache = Cache(tmp_path)
    assert storemeta.cached(cache, URL_NX) == {}
    data = storemeta.parse_detail(HTML_NXBREW, URL_NX)
    storemeta.put(cache, URL_NX, data, "page")
    got = storemeta.cached(cache, URL_NX)
    assert got["cover"] == data["cover"] and got["source"] == "page", got
    # un secondo passaggio non cancella quello che c'era
    storemeta.put(cache, URL_NX, {"players": 2}, "gametdb")
    got = storemeta.cached(cache, URL_NX)
    assert got["cover"] == data["cover"] and got["players"] == 2 and got["source"] == "gametdb", got
    cache.close()
    print("ok  cache: dati salvati e completati senza perdere quelli gia' letti")


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    test_nxbrew_detail()
    test_nxbrew_placeholder_ignored()
    test_ziperto_detail()
    test_site_suffix_only_for_domains()
    test_clean_and_similarity()
    test_best_match()
    test_superpsx_detail()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_cache_roundtrip(Path(t))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        test_fill_reads_archives(Path(t))
    print("TUTTI I TEST OK")
