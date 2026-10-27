"""describe_page(): anteprima di un indirizzo prima di aggiungerlo come store."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gamescraper import catalog  # noqa: E402

TYPE_A = """
<div class="entry-content">
  <ul class="lcp_catlist" id="lcp_instance_0">
    <li><a href="https://example-a.test/game-one/">Game One – Build 123</a></li>
    <li><a href="https://example-a.test/game-two/">Game Two – v1.4</a></li>
    <li><a href="https://example-a.test/game-three/">Game Three</a></li>
  </ul>
  <ul class="lcp_paginator">
    <li class="lcp_currentpage">1</li>
    <li><a href="https://example-a.test/all/?lcp_page0=2#lcp_instance_0" title="2">2</a></li>
    <li><a href="https://example-a.test/all/?lcp_page0=147#lcp_instance_0" title="147">147</a></li>
  </ul>
</div>
"""

TYPE_B = """
<div class="PLACEHOLDER_CSS_B-az-list">
  <h2 id="letter-#">#</h2>
  <div class="PLACEHOLDER_CSS_B-game"><a href="https://example-b.test/game-hash/">Game Hash</a></div>
  <h2 id="letter-A">A</h2>
  <div class="PLACEHOLDER_CSS_B-game"><a href="https://example-b.test/game-a/">Game A</a></div>
  <div class="PLACEHOLDER_CSS_B-game"><a href="https://example-b.test/game-a2/">Game A2</a></div>
</div>
"""

TYPE_D = """
<div class="az-listing">
  <div class="letter-section">
    <h2 class="letter-title">A</h2>
    <ul>
      <li><a href="https://example-d.test/game-a/">Game Alpha</a></li>
      <li><a href="https://example-d.test/game-b/">Game Beta</a></li>
    </ul>
  </div>
</div>
"""

FILELIST = """
<pre>
<a href="Gioco.Uno.rar">Gioco.Uno.rar</a>  12/03/2024 10:00  1.2G
<a href="Gioco.Due.rar">Gioco.Due.rar</a>  12/03/2024 10:00  800M
</pre>
"""


def test_type_a_riporta_le_pagine():
    info = catalog.describe_page(TYPE_A, "https://example-a.test/all/")
    assert info["kind"] == "type_a"
    assert info["games"] == 3
    assert info["pages"] == 147
    assert info["samples"][0] == "Game One – Build 123"


def test_type_b_una_sola_pagina():
    info = catalog.describe_page(TYPE_B, "https://example-b.test/")
    assert info["kind"] == "type_b"
    assert info["games"] == 3
    assert info["pages"] == 1


def test_type_d_elenco_unico():
    info = catalog.describe_page(TYPE_D, "https://example-d.test/list/")
    assert info["kind"] == "type_d"
    assert info["games"] == 2
    assert info["pages"] == 1


def test_filelist_senza_paginazione():
    info = catalog.describe_page(FILELIST, "https://example-f.test/files/")
    assert info["kind"] == "filelist"
    assert info["games"] == 2
    assert info["pages"] == 1


def test_pagina_vuota_resta_filelist():
    info = catalog.describe_page("<html><body><p>niente</p></body></html>", "https://example.test/")
    assert info["kind"] == "filelist"
    assert info["games"] == 0
    assert info["samples"] == []


def test_errori_chiari():
    """403 anti-bot, 404 e 429 devono spiegare il motivo, non solo il codice."""

    class R:
        def __init__(self, code, text="", headers=None):
            self.status_code = code
            self.text = text
            self.headers = headers or {}

    cf = R(403, "<html><script src='/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1'></script>",
           {"server": "cloudflare", "cf-mitigated": "challenge"})
    msg = catalog._http_error(cf, "https://esempio.test/elenco/")
    assert "anti-bot" in msg and "Cloudflare" in msg and "403" in msg, msg
    assert "esempio.test" in msg

    # 403 senza Cloudflare: messaggio diverso (non è una verifica JavaScript)
    msg2 = catalog._http_error(R(403), "https://esempio.test/x/")
    assert "accesso negato" in msg2 and "anti-bot" not in msg2, msg2

    assert "non trovata" in catalog._http_error(R(404), "https://esempio.test/x/")
    assert "limitando" in catalog._http_error(R(429), "https://esempio.test/x/")
    assert "nessuna risposta" in catalog._http_error(None, "https://esempio.test/x/")
    # 503 di Cloudflare = verifica, non «sito giù»
    assert "anti-bot" in catalog._http_error(R(503, "", {"server": "cloudflare"}), "https://e.test/")
