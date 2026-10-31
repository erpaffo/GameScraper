from __future__ import annotations

import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "docs" / "img"
PLATFORMS = ("gba", "snes", "psx", "nes", "megadrive")
TITLES = [
    "Stella Errante", "Cavaliere di Cenere", "Giardino delle Lune", "Il Faro Sommerso", "Vento di Rame",
    "Cronache di Brumavia", "La Torre di Vetro", "Sentieri di Ambra", "Il Seme del Drago", "Ombre sul Lago",
    "Orizzonte di Carta", "La Città Silenziosa", "Custodi del Crepuscolo", "Rotta per Miralba", "Il Patto delle Nevi",
    "Pietra di Mezzanotte", "Atlante dei Sogni", "La Nave di Cobalto", "Canto delle Maree", "Lanterne nel Bosco",
    "Il Regno Capovolto", "Fiamma d'Inverno", "Le Isole Perdute", "Eco di Valdombra", "La Mappa Segreta",
    "Cuore di Quarzo", "Oltre le Nuvole", "Il Viandante Verde", "La Valle dei Giganti", "Fortezza di Cenere",
    "L'Alchimista Blu", "La Corsa delle Comete", "Lupo di Monteluce", "Il Giudice delle Stelle", "Specchio di Smeraldo",
    "La Caverna Cantante", "Nebbia sul Passo", "Il Treno di Zaffiro", "Maschere di Sale", "La Chiave di Aster",
    "Sussurri di Granito", "Il Mercato Volante", "Danza dei Fulmini", "La Regina di Rugiada", "Il Mulino Rosso",
    "Figli del Vulcano", "Il Porto delle Ombre", "Viaggio a Solaria", "La Biblioteca Errante", "Cacciatori di Pioggia",
    "Il Labirinto d'Avorio", "Fuga da Oricalco", "Il Gioco dei Gabbiani", "Racconti di Luminara", "La Sfera Nascosta",
    "Duchi di Nebbia", "Il Tesoro di Corallo", "Campane di Vetro", "La Luna di Ferro", "I Custodi del Sale",
    "La Foresta Mobile", "Ultima Aurora", "La Strada di Giada", "Il Segreto di Auralis", "Piume di Tempesta",
    "Il Ponte Infinito", "Le Miniere di Luce", "La Rosa di Basalto", "Il Viaggio di Nivaria", "Stelle di Cartone",
]
GENRES = ("Avventura", "Azione", "Puzzle", "Ruolo", "Strategia", "Corse", "Platform", "Esplorazione")
PALETTES = ((29, 90, 133), (112, 62, 128), (185, 91, 56), (25, 122, 115), (181, 120, 45), (67, 83, 150))


def _font(size: int, bold: bool = False):
    candidates = (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf") if bold else (
        r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf")
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    im = Image.new("RGB", size)
    px = im.load()
    for y in range(h):
        t = y / max(1, h - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(w):
            px[x, y] = color
    return im


def _cover(path: Path, title: str, index: int):
    base = PALETTES[index % len(PALETTES)]
    dark = tuple(max(5, int(c * 0.30)) for c in base)
    im = _gradient((480, 640), base, dark)
    glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.ellipse((185, 65, 500, 380), fill=(*tuple(min(255, c + 55) for c in base), 95))
    d.polygon([(0, 495), (210, 300), (480, 525), (480, 640), (0, 640)], fill=(12, 16, 29, 140))
    im = Image.alpha_composite(im.convert("RGBA"), glow).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((24, 24, 456, 616), radius=18, outline=(255, 255, 255), width=2)
    d.text((48, 52), "POZZO  /  RACCOLTA IMMAGINARIA", font=_font(16, True), fill=(238, 241, 252))
    d.line((48, 90, 432, 90), fill=(255, 255, 255), width=1)
    font_size = 52 if len(title) < 20 else 43
    font = _font(font_size, True)
    words = title.split()
    lines, line = [], ""
    for word in words:
        candidate = (line + " " + word).strip()
        if d.textbbox((0, 0), candidate, font=font)[2] > 350 and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    y = 230 - len(lines) * (font_size + 8) // 2
    for text in lines:
        d.text((48, y), text, font=font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(11, 16, 28))
        y += font_size + 9
    d.text((48, 570), f"EDIZIONE DEMO  ·  {index + 1:02d}", font=_font(15, True), fill=(230, 231, 244))
    im.save(path, "JPEG", quality=84, optimize=True)


def _hero(path: Path, title: str, index: int):
    base = PALETTES[index % len(PALETTES)]
    im = _gradient((960, 520), tuple(min(255, c + 35) for c in base), tuple(max(5, int(c * .24)) for c in base))
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.ellipse((560, -100, 1030, 420), fill=(*tuple(min(255, c + 70) for c in base), 105))
    d.polygon([(440, 520), (710, 160), (960, 410), (960, 520)], fill=(8, 15, 30, 115))
    im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(im)
    d.text((52, 48), "STORIE INFINITE", font=_font(18, True), fill=(245, 245, 255))
    font = _font(55 if len(title) < 21 else 46, True)
    d.text((52, 300), title, font=font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(10, 15, 28))
    d.text((55, 390), "Un'avventura tutta da immaginare", font=_font(22), fill=(237, 241, 250))
    im.save(path, "JPEG", quality=82, optimize=True)


def make_demo_data(folder: Path):
    cache = folder / "cache"
    media = cache / "media" / "demo"
    media.mkdir(parents=True)
    (folder / "roms").mkdir()
    (folder / "config.toml").write_text(
        '[paths]\nroms = "roms"\ncache = "cache"\noutput = "output"\n\n'
        '[app]\nsetup_done = true\nexperience = "catalog"\ntheme = "default"\n', encoding="utf-8")
    from pozzo.library.build import SCHEMA
    db = sqlite3.connect(cache / "library.db")
    db.executescript(SCHEMA)
    now = time.time()
    rows = []
    keys = []
    for i, title in enumerate(TITLES):
        plat = PLATFORMS[i % len(PLATFORMS)]
        key = f"{plat}/demo-{i + 1:03d}"
        cover_rel = f"media/demo/cover-{i + 1:03d}.jpg"
        hero_rel = f"media/demo/hero-{i + 1:03d}.jpg"
        _cover(cache / cover_rel, title, i)
        _hero(cache / hero_rel, title, i)
        genres = [GENRES[i % len(GENRES)], GENRES[(i * 3 + 2) % len(GENRES)]]
        rows.append((key, plat, title, title.lower(), 1988 + (i * 7) % 37, f"{1988 + (i * 7) % 37}-06-01",
                     "Studio Immaginario", "Pozzo Lab", json.dumps(genres, ensure_ascii=False), "1", 4.1 + (i % 9) / 10,
                     "Una piccola avventura nata dalla fantasia.", "", 1, json.dumps([f"demo/{key}.rom"]),
                     json.dumps({"cover": cover_rel, "hero": hero_rel, "wheel": hero_rel}), "", "", "", "{}", key, 1, "", 1,
                     "", "", "", "", "", 0, 0, "{}", 0, "{}", "{}", "game"))
        keys.append(key)
    columns = ("key, platform, title, sort_title, year, release_date, developer, publisher, genres, players, rating, "
               "description, region, found, files, media, video_url, canonical, crc, origin, grp, is_primary, store, "
               "installed, launch, install_uri, page, store_playtime, install_dir, rating_count, rating_critics, "
               "ratings, size, hltb, store_pages, content")
    marks = ",".join("?" for _ in range(36))
    db.executemany(f"INSERT INTO games({columns}) VALUES({marks})", rows)
    db.executemany("INSERT INTO collections VALUES(?, ?)", [("rotte-lontane", "Rotte lontane"), ("piccole-meraviglie", "Piccole meraviglie")])
    db.executemany("INSERT INTO collection_games VALUES(?, ?)",
                   [("rotte-lontane", key) for key in keys[::3]] + [("piccole-meraviglie", key) for key in keys[1::4]])
    db.execute("INSERT INTO meta VALUES('built_at', ?)", (str(now),))
    db.execute("INSERT INTO meta VALUES('games', ?)", (str(len(rows)),))
    db.commit()
    db.close()

    from pozzo.cache import Cache
    cached = Cache(cache)
    cached.kv_set("update_check", {"time": time.time(), "latest": None})
    cached.close()
    from pozzo.library.userdata import UserData
    user = UserData(cache / "userdata.db")
    for i in (0, 4, 9, 17, 26, 34, 41, 52):
        user.db.execute("INSERT INTO user_games(key, favorite, last_played, play_count, playtime, status, my_rating) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?)",
                        (keys[i], int(i % 2 == 0), now - (i + 1) * 86400, 2 + i % 8, 720 + i * 95,
                         "playing" if i == 0 else "done" if i in (9, 34) else "backlog", 4 + i % 2))
    user.db.execute("INSERT INTO user_collections(slug, name, custom, hidden, created) VALUES(?, ?, 1, 0, ?)",
                    ("serate-quiete", "Serate tranquille", now))
    user.db.execute("INSERT INTO user_collections(slug, name, custom, hidden, created) VALUES(?, ?, 1, 0, ?)",
                    ("mondi-da-esplorare", "Mondi da esplorare", now))
    user.db.executemany("INSERT INTO collection_edits(slug, key, action) VALUES(?, ?, 'add')",
                        [("serate-quiete", keys[i]) for i in (2, 8, 15, 22, 38, 49)] +
                        [("mondi-da-esplorare", keys[i]) for i in (5, 12, 19, 28, 44, 60)])
    user.db.commit()
    user.close()
    return folder / "config.toml", cache


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_server(proc: subprocess.Popen, url: str, log_path: Path):
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"Il server è uscito ({proc.returncode}):\n{log_path.read_text(encoding='utf-8', errors='replace')}")
        try:
            with urlopen(url + "/api/setup", timeout=1) as response:
                state = json.load(response)
            if state.get("done") and state.get("has_library"):
                return
        except (URLError, TimeoutError, ValueError):
            time.sleep(.2)
    raise RuntimeError("Il server demo non risponde entro 30 secondi")


def save_jpeg(page, path: Path):
    raw = page.screenshot(type="jpeg", quality=82, full_page=False)
    with Image.open(io.BytesIO(raw)) as im:
        im.convert("RGB").save(path, "JPEG", quality=82, optimize=True, progressive=True)
    if path.stat().st_size > 250 * 1024:
        with Image.open(path) as im:
            for quality in (78, 74, 70, 66, 62, 58):
                im.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
                if path.stat().st_size <= 250 * 1024:
                    break
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        if im.size != (1440, 900):
            raise RuntimeError(f"Viewport inatteso per {path.name}: {im.size}")
    if path.stat().st_size > 250 * 1024:
        raise RuntimeError(f"{path.name} supera 250 KB ({path.stat().st_size} byte)")


def main():
    from tempfile import TemporaryDirectory
    from playwright.sync_api import sync_playwright

    OUTPUT.mkdir(parents=True, exist_ok=True)
    created = []
    problems = []
    with TemporaryDirectory(prefix=".demo-screenshots-", dir=ROOT) as temp:
        data_dir = Path(temp)
        config_path, cache = make_demo_data(data_dir)
        port = available_port()
        base_url = f"http://127.0.0.1:{port}"
        log_path = data_dir / "server.log"
        env = os.environ.copy()
        env["POZZO_CONFIG"] = str(config_path)
        env["PYTHONUTF8"] = "1"
        code = ("import sys, uvicorn; from pozzo import config; from pozzo.app.server import create_app; "
                "cfg=config.load(sys.argv[1]); uvicorn.run(create_app(cfg), host='127.0.0.1', "
                "port=int(sys.argv[2]), log_level='error', access_log=False)")
        with log_path.open("w", encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable, "-c", code, str(config_path), str(port)], cwd=ROOT,
                                      env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_server(server, base_url, log_path)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1,
                                              locale="it-IT")
                page = context.new_page()
                external = []
                errors = []
                bad_responses = []
                page.on("response", lambda response: bad_responses.append((response.status, response.url)) if response.status >= 400 else None)
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                def local_only(route):
                    if route.request.url.startswith(base_url):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.fulfill(status=200, content_type="text/css", body="")
                page.route("**/*", local_only)
                page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                page.evaluate("localStorage.clear(); sessionStorage.clear()")
                page.reload(wait_until="domcontentloaded", timeout=30000)
                page.locator("#main").wait_for(state="visible", timeout=20000)
                page.wait_for_function("() => document.querySelectorAll('#grid-layer img').length > 0", timeout=25000)
                if not page.request.get(base_url + "/api/setup").json().get("done"):
                    raise RuntimeError("La configurazione guidata è apparsa nonostante setup_done")
                lib = page.request.get(base_url + "/api/library?part=owned").json()
                if len(lib.get("games", {}).get("rows", [])) != len(TITLES):
                    raise RuntimeError("La libreria demo non contiene i 70 giochi previsti")
                cover_errors = []
                for row in lib["games"]["rows"]:
                    gid = row[0]
                    for kind in ("cover", "hero"):
                        response = page.request.get(f"{base_url}/img/{gid}/{kind}")
                        try:
                            if response.status != 200:
                                cover_errors.append(f"{gid}/{kind}: HTTP {response.status}")
                            else:
                                with Image.open(io.BytesIO(response.body())) as art:
                                    art.verify()
                        except Exception as exc:
                            cover_errors.append(f"{gid}/{kind}: {exc}")
                if cover_errors:
                    problems.append("Media demo non validi: " + ", ".join(cover_errors[:8]))
                for name, experience in (("esp-catalogo.jpg", "catalog"), ("esp-scaffali.jpg", "shelves"),
                                         ("esp-console.jpg", "console"), ("esp-dashboard.jpg", "dashboard"),
                                         ("esp-cinema.jpg", "cinema"), ("esp-vetrina.jpg", "storefront")):
                    response = page.request.post(base_url + "/api/app/experience", data={"experience": experience})
                    if response.status != 200:
                        problems.append(f"{experience}: API cambio grafica {response.status} {response.text()}")
                    page.evaluate("(view) => localStorage.setItem('gs.view', JSON.stringify({ type: view }))",
                                 "home" if experience not in ("catalog", "original") else "all")
                    page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_function("(id) => window.GS && GS.experience && GS.experience.id === id",
                                               arg=experience, timeout=15000)
                    except Exception as exc:
                        problems.append(f"{experience}: grafica non confermata nel frontend ({exc})")
                    page.wait_for_timeout(1200)
                    try:
                        page.wait_for_function("() => { const a=[...document.querySelectorAll('#grid-layer img')].filter(i => { const r=i.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; }); return a.length > 0 && a.every(i => i.complete && i.naturalWidth > 0); }", timeout=12000)
                    except Exception:
                        pass
                    if experience == "console" and not page.locator("#drawer-toggle").is_visible():
                        problems.append("console: pulsante ☰ della barra laterale non visibile")
                    if name == "esp-catalogo.jpg":
                        try:
                            page.locator('#nav [data-view="all"]').click(timeout=4000)
                            page.wait_for_function("() => document.querySelectorAll('#grid-layer img').length > 0", timeout=10000)
                            page.wait_for_timeout(400)
                            page.wait_for_function("() => { const a=[...document.querySelectorAll('#grid-layer img')].filter(i => { const r=i.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; }); return a.length > 0 && a.every(i => i.complete && i.naturalWidth > 0 && i.classList.contains('ok')); }", timeout=20000)
                        except Exception:
                            pass
                    output = OUTPUT / name
                    save_jpeg(page, output)
                    created.append(output)
                for theme in ("notte-blu", "chiaro", "oled", "retro"):
                    response = page.request.post(base_url + "/api/app/experience", data={"experience": "catalog"})
                    if response.status != 200:
                        problems.append(f"Catalogo prima del tema {theme}: API {response.status}")
                    response = page.request.post(base_url + "/api/app/theme", data={"theme": theme})
                    if response.status != 200:
                        problems.append(f"Tema {theme}: API {response.status} {response.text()}")
                    page.evaluate("localStorage.setItem('gs.view', JSON.stringify({ type: 'all' }))")
                    page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1200)
                    try:
                        page.locator('#nav [data-view="all"]').click(timeout=3500)
                    except Exception:
                        pass
                    page.wait_for_function("() => { const a=[...document.querySelectorAll('#grid-layer img')].filter(i => { const r=i.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; }); return a.length > 0 && a.every(i => i.complete && i.naturalWidth > 0 && i.classList.contains('ok')); }", timeout=20000)
                    page.wait_for_timeout(400)
                    output = OUTPUT / f"tema-{theme}.jpg"
                    save_jpeg(page, output)
                    created.append(output)
                page.request.post(base_url + "/api/app/theme", data={"theme": "default"})
                page.request.post(base_url + "/api/app/experience", data={"experience": "catalog"})
                page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1000)
                page.evaluate("openSettings('look')")
                page.locator("#experience-list").wait_for(state="visible", timeout=10000)
                page.locator("#theme-list .theme-card").first.wait_for(state="visible", timeout=10000)
                page.wait_for_timeout(1000)
                output = OUTPUT / "aspetto.jpg"
                save_jpeg(page, output)
                created.append(output)
                if errors:
                    problems.append("Errori frontend: " + " | ".join(errors[:12]))
                if bad_responses:
                    problems.append("Risposte HTTP di errore: " + " | ".join(f"{status} {url}" for status, url in bad_responses[:12]))
                if external:
                    print("Richieste remote intercettate localmente (mai inviate): " + ", ".join(sorted(set(external))))
                browser.close()
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
    print("Creati:")
    for path in created:
        print(f"  {path.relative_to(ROOT)} — {path.stat().st_size:,} byte")
    if problems:
        print("Problemi:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("Problemi: nessuno; avvio schermate e risorse senza richieste esterne.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
