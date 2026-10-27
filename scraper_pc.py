"""
Test scraper per FitGirl Repacks.
Prova prima l'API REST di WordPress (più pulita),
poi fallback su HTML con Playwright (per superare Cloudflare).
Output: giochi_trovati.md
"""

import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://fitgirl-repacks.site"
OUTPUT_FILE = Path("giochi_trovati.md")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# APPROCCIO 1: API REST di WordPress
# ---------------------------------------------------------------------------
def try_wordpress_api(per_page: int = 20):
    """Tenta di leggere i post tramite l'API REST di WordPress."""
    url = f"{BASE_URL}/wp-json/wp/v2/posts"
    params = {"per_page": per_page, "_fields": "id,title,link,date,excerpt"}

    print(f"[1] Provo API WordPress: {url}")
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        print(f"    Status: {r.status_code}")
        print(f"    Content-Type: {r.headers.get('Content-Type')}")

        # Se Cloudflare blocca, la risposta non è JSON
        if r.status_code != 200:
            print("    ❌ API non accessibile (status != 200)")
            return None

        if "application/json" not in r.headers.get("Content-Type", ""):
            print("    ❌ La risposta non è JSON (probabile blocco Cloudflare)")
            return None

        posts = r.json()
        if not isinstance(posts, list) or not posts:
            print("    ❌ Nessun post ricevuto")
            return None

        print(f"    ✅ Ricevuti {len(posts)} post dall'API")
        games = []
        for p in posts:
            title = BeautifulSoup(p["title"]["rendered"], "html.parser").get_text()
            excerpt = BeautifulSoup(
                p.get("excerpt", {}).get("rendered", ""), "html.parser"
            ).get_text().strip()
            games.append(
                {
                    "title": title,
                    "link": p["link"],
                    "date": p.get("date", ""),
                    "description": excerpt[:300],
                    "source": "wordpress-api",
                }
            )
        return games

    except Exception as e:
        print(f"    ❌ Errore: {e}")
        return None


# ---------------------------------------------------------------------------
# APPROCCIO 2: HTML con Playwright (per superare Cloudflare)
# ---------------------------------------------------------------------------
def try_playwright_html(max_pages: int = 1):
    """Usa un browser headless per superare il controllo Cloudflare."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[2] ❌ Playwright non installato. Salto.")
        return None

    print("[2] Provo HTML con Playwright...")
    games = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1366, "height": 900},
                locale="en-US",
            )
            page = context.new_page()

            for page_num in range(1, max_pages + 1):
                url = BASE_URL if page_num == 1 else f"{BASE_URL}/page/{page_num}/"
                print(f"    → Apro {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # Attendi che Cloudflare finisca (max 15s)
                for _ in range(15):
                    if "Just a moment" not in page.title():
                        break
                    page.wait_for_timeout(1000)

                print(f"      Titolo pagina: {page.title()}")

                # Trova i post
                articles = page.query_selector_all("article")
                if not articles:
                    # fallback: selettore più generico
                    articles = page.query_selector_all("h2.entry-title, h1.entry-title")

                print(f"      Articoli trovati: {len(articles)}")

                for art in articles:
                    try:
                        # Titolo
                        title_el = art.query_selector("h1.entry-title, h2.entry-title, h1, h2")
                        title = title_el.inner_text().strip() if title_el else "N/D"

                        # Link
                        link_el = art.query_selector("h1.entry-title a, h2.entry-title a, a")
                        link = link_el.get_attribute("href") if link_el else ""

                        # Descrizione (primi paragrafi)
                        desc_el = art.query_selector(".entry-content, .entry-summary, p")
                        description = desc_el.inner_text().strip()[:300] if desc_el else ""

                        if title and title != "N/D":
                            games.append(
                                {
                                    "title": title,
                                    "link": link or url,
                                    "date": "",
                                    "description": description,
                                    "source": "playwright-html",
                                }
                            )
                    except Exception as e:
                        print(f"      ⚠️ Errore su un articolo: {e}")
                        continue

            browser.close()

        if not games:
            print("    ❌ Nessun gioco estratto")
            return None

        print(f"    ✅ Estratti {len(games)} giochi")
        return games

    except Exception as e:
        print(f"    ❌ Errore Playwright: {e}")
        return None


# ---------------------------------------------------------------------------
# GENERAZIONE FILE MARKDOWN
# ---------------------------------------------------------------------------
def clean_title(title: str) -> str:
    """Rimuove il prefisso '#NNNN ' tipico dei titoli FitGirl."""
    return re.sub(r"^#\d+\s+", "", title).strip()


def write_markdown(games: list, source: str) -> None:
    lines = [
        f"# Giochi trovati ({len(games)})",
        "",
        f"*Fonte: {source}*",
        "",
    ]
    for i, g in enumerate(games, 1):
        title = clean_title(g["title"])
        lines.append(f"## {i}. {title}")
        if g.get("link"):
            lines.append(f"- **Link:** {g['link']}")
        if g.get("date"):
            lines.append(f"- **Data:** {g['date']}")
        if g.get("description"):
            desc = g["description"].replace("\n", " ").strip()
            lines.append(f"- **Descrizione:** {desc}")
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

    # Tentativo 1: API WordPress
    games = try_wordpress_api(per_page=20)
    if games:
        source = "WordPress REST API"

    # Tentativo 2: fallback HTML con Playwright
    if not games:
        games = try_playwright_html(max_pages=1)
        if games:
            source = "HTML via Playwright"

    if not games:
        print("\n❌ Nessun metodo ha funzionato.")
        print("   Possibili cause: Cloudflare blocca tutto, o il sito è cambiato.")
        sys.exit(1)

    # Deduplica per titolo
    seen = set()
    unique = []
    for g in games:
        key = clean_title(g["title"]).lower()
        if key not in seen:
            seen.add(key)
            unique.append(g)

    write_markdown(unique, source)


if __name__ == "__main__":
    main()