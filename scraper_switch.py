"""
Test scraper per NXBrew.net (Game Index).
Prova a capire la struttura della pagina indice e, se possibile,
estrae l'elenco dei giochi. Fallback su Playwright per Cloudflare.
Output: nxbrew_giochi.md
"""

import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://nxbrew.net"
INDEX_URL = f"{BASE_URL}/game-index/"
OUTPUT_FILE = Path("nxbrew_giochi.md")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# APPROCCIO 1: requests + BeautifulSoup (se Cloudflare non blocca)
# ---------------------------------------------------------------------------
def try_requests():
    print(f"[1] Provo requests su: {INDEX_URL}")
    try:
        r = requests.get(INDEX_URL, headers=HEADERS, timeout=20)
        print(f"    Status: {r.status_code}")
        print(f"    Content-Type: {r.headers.get('Content-Type')}")

        if r.status_code != 200:
            print("    ❌ Status diverso da 200")
            return None

        # Se la risposta è HTML di Cloudflare, non va bene
        if "Just a moment" in r.text or "cf-challenge" in r.text.lower():
            print("    ❌ Pagina di challenge Cloudflare rilevata")
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        return parse_index_page(soup, r.url)

    except Exception as e:
        print(f"    ❌ Errore: {e}")
        return None


# ---------------------------------------------------------------------------
# APPROCCIO 2: Playwright (per superare Cloudflare)
# ---------------------------------------------------------------------------
def try_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[2] ❌ Playwright non installato.")
        return None

    print("[2] Provo Playwright su: " + INDEX_URL)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,  # meglio visibile per Cloudflare
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1366, "height": 900},
                locale="en-US",
            )
            page = context.new_page()
            page.goto(INDEX_URL, wait_until="domcontentloaded", timeout=60000)

            # Attendi che Cloudflare finisca
            for _ in range(15):
                title = page.title()
                if "Just a moment" not in title and "Attention" not in title:
                    break
                page.wait_for_timeout(1000)

            print(f"    Titolo pagina: {page.title()}")
            html = page.content()
            browser.close()

            soup = BeautifulSoup(html, "html.parser")
            return parse_index_page(soup, INDEX_URL)

    except Exception as e:
        print(f"    ❌ Errore Playwright: {e}")
        return None


# ---------------------------------------------------------------------------
# PARSING DELLA PAGINA INDICE
# ---------------------------------------------------------------------------
def parse_index_page(soup: BeautifulSoup, base_url: str):
    """
    La pagina /game-index/ mostra solo le lettere A-Z.
    Dobbiamo capire se:
      - ci sono link diretti ai giochi
      - oppure link a sottopagine per lettera
    """
    games = []

    # Strategia A: cerca link che sembrano giochi (slug con /game/ o simili)
    all_links = soup.find_all("a", href=True)
    print(f"    Link totali trovati: {len(all_links)}")

    # Filtra link che sembrano puntare a giochi
    game_links = []
    letter_links = []

    for a in all_links:
        href = a["href"]
        text = a.get_text(strip=True)

        # Link a una lettera (A, B, C...)
        if len(text) == 1 and text.isalpha():
            letter_links.append((text, urljoin(base_url, href)))
            continue

        # Link che sembrano giochi (slug lungo, non navigazione)
        full = urljoin(base_url, href)
        if (
            full.startswith(BASE_URL)
            and "/game-index/" not in full
            and "/category/" not in full
            and "/tag/" not in full
            and text
            and len(text) > 2
        ):
            game_links.append((text, full))

    print(f"    Link-lettera trovati: {len(letter_links)}")
    print(f"    Possibili link-gioco trovati: {len(game_links)}")

    # Se non ci sono giochi direttamente, prova a seguire una lettera
    if not game_links and letter_links:
        print("    → Nessun gioco diretto. Provo a seguire la lettera 'A'...")
        letter_name, letter_url = letter_links[0]
        print(f"      URL lettera: {letter_url}")

        # Scarica la pagina della lettera con Playwright (o requests)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                page = browser.new_page(user_agent=HEADERS["User-Agent"])
                page.goto(letter_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                html = page.content()
                browser.close()

            sub_soup = BeautifulSoup(html, "html.parser")
            # Cerca link ai giochi nella pagina della lettera
            for a in sub_soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                full = urljoin(letter_url, href)
                if (
                    full.startswith(BASE_URL)
                    and text
                    and len(text) > 3
                    and "/game-index/" not in full
                ):
                    game_links.append((text, full))
            print(f"    → Dopo la lettera '{letter_name}': {len(game_links)} link-gioco")
        except Exception as e:
            print(f"    ⚠️ Errore nel seguire la lettera: {e}")

    # Costruisci la lista finale
    seen = set()
    for title, link in game_links:
        key = title.lower().strip()
        if key and key not in seen:
            seen.add(key)
            games.append(
                {
                    "title": title.strip(),
                    "link": link,
                }
            )

    return games


# ---------------------------------------------------------------------------
# GENERAZIONE MARKDOWN
# ---------------------------------------------------------------------------
def write_markdown(games: list, source: str):
    lines = [
        f"# Giochi NXBrew trovati ({len(games)})",
        "",
        f"*Fonte: {source}*",
        "",
    ]
    for i, g in enumerate(games, 1):
        title = g.get("title", "N/D")
        link = g.get("link", "")
        lines.append(f"## {i}. {title}")
        if link:
            lines.append(f"- **Link:** {link}")
        lines.append("")  # a capo separatore

    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ File scritto: {OUTPUT_FILE.resolve()}")
    print(f"   Totale giochi: {len(games)}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    games = None
    source = None

    games = try_requests()
    if games:
        source = "requests + BeautifulSoup"
    else:
        games = try_playwright()
        if games:
            source = "Playwright (HTML)"

    if not games:
        print("\n❌ Nessun gioco estratto.")
        print("   Possibili cause:")
        print("   - La pagina indice non contiene link diretti ai giochi.")
        print("   - Cloudflare blocca sia requests che Playwright.")
        print("   - La struttura del sito è cambiata.")
        sys.exit(1)

    write_markdown(games, source)


if __name__ == "__main__":
    main()