"""Esportazione della libreria costruita (library.db) verso i frontend ES-DE e Pegasus.

Legge `<cache>/library.db` (solo giochi con file locali, niente store PC ne' cataloghi online,
solo versioni principali `is_primary = 1`) e scrive tutto dentro `out_dir` usando solo la
libreria standard.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from . import platforms as P

# Mappa dai tipi media di GameScraper alle sottocartelle di downloaded_media/<console>/ in ES-DE
ESDE_MEDIA_DIRS: dict[str, str] = {
    "cover": "covers",
    "screenshot": "screenshots",
    "title": "titlescreens",
    "wheel": "marquees",
    "marquee": "marquees",
    "video": "videos",
    "box3d": "3dboxes",
    "fanart": "fanart",
}

# Mappa dai tipi media di GameScraper alle chiavi assets.* di Pegasus
PEGASUS_ASSETS: dict[str, str] = {
    "cover": "assets.box_front",
    "screenshot": "assets.screenshot",
    "title": "assets.titlescreen",
    "wheel": "assets.wheel",
    "marquee": "assets.marquee",
    "video": "assets.video",
    "box3d": "assets.box_full",
    "fanart": "assets.background",
}


def _load_primary_games(cfg_cache: Path, platforms: list[str] | None = None) -> dict[str, list[dict]]:
    """Carica da `<cfg_cache>/library.db` i giochi principali con file locali, raggruppati per console."""
    db_path = Path(cfg_cache) / "library.db"
    if not db_path.is_file():
        return {}
    allowed = {p.lower() for p in platforms} if platforms else None
    by_plat: dict[str, list[dict]] = defaultdict(list)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT key, platform, title, sort_title, year, release_date, developer, publisher, "
            "genres, players, rating, description, files, media, is_primary, store "
            "FROM games WHERE is_primary = 1 ORDER BY platform, sort_title, title"
        )
        for row in cur:
            pid = (row["platform"] or "").strip()
            if not pid or pid == "windows":
                continue
            if allowed is not None and pid.lower() not in allowed:
                continue
            if row["store"]:  # salta store PC e voci di catalogo online
                continue
            try:
                files = json.loads(row["files"] or "[]")
            except (TypeError, ValueError):
                files = []
            if not files:
                continue
            try:
                genres = json.loads(row["genres"] or "[]")
                if not isinstance(genres, list):
                    genres = [str(genres)] if genres else []
            except (TypeError, ValueError):
                genres = [row["genres"]] if row["genres"] else []
            try:
                media = json.loads(row["media"] or "{}")
                if not isinstance(media, dict):
                    media = {}
            except (TypeError, ValueError):
                media = {}

            by_plat[pid].append({
                "key": row["key"],
                "platform": pid,
                "title": row["title"] or Path(files[0]).stem,
                "sort_title": row["sort_title"] or "",
                "year": row["year"],
                "release_date": row["release_date"] or "",
                "developer": row["developer"] or "",
                "publisher": row["publisher"] or "",
                "genres": [str(g).strip() for g in genres if str(g).strip()],
                "players": str(row["players"] or "").strip(),
                "rating": row["rating"],
                "description": row["description"] or "",
                "files": [str(f) for f in files if f],
                "media": media,
            })
    finally:
        con.close()
    return by_plat


def _resolve_media_src(cfg_cache: Path, rel: str) -> Path | None:
    if not rel:
        return None
    p = Path(rel[5:]) if rel.startswith("file:") else (Path(cfg_cache) / rel)
    return p if p.is_file() else None


def _copy_media(src: Path, dst: Path) -> None:
    """Copia (o collega via hardlink se possibile) un file media dentro `out_dir`."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            if dst.stat().st_size == src.stat().st_size:
                return
            dst.unlink()
        except OSError:
            pass
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _rom_stem(file_path: str) -> str:
    p = Path(file_path)
    # Per i giochi in cartella (es. Switch/PS3) o file senza estensione usa il nome completo
    return p.stem if p.suffix else p.name


def _esde_release_date(release_date: str, year: int | None) -> str:
    """Converte una data in formato ES-DE `YYYYMMDDT000000`."""
    s = (release_date or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}T000000"
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        return f"{m.group(1)}{m.group(2)}01T000000"
    m = re.match(r"^(\d{4})$", s)
    if m:
        return f"{m.group(1)}0101T000000"
    if year and 1970 <= int(year) <= 2100:
        return f"{int(year):04d}0101T000000"
    return ""


def _pegasus_release_date(release_date: str, year: int | None) -> str:
    """Converte una data in formato ISO `YYYY-MM-DD` (o `YYYY`) per Pegasus."""
    s = (release_date or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    m = re.match(r"^(\d{4}-\d{2})$", s)
    if m:
        return m.group(1)
    m = re.match(r"^(\d{4})", s)
    if m:
        return m.group(1)
    if year and 1970 <= int(year) <= 2100:
        return f"{int(year):04d}"
    return ""


def _common_rom_root(games: list[dict]) -> Path | None:
    """Individua la cartella base comune dei file ROM di una console per calcolare i percorsi relativi."""
    parents = []
    for g in games:
        if g["files"]:
            p = Path(g["files"][0])
            if p.parent and str(p.parent) not in ("", "."):
                parents.append(str(p.parent))
    if not parents:
        return None
    try:
        common = os.path.commonpath(parents)
        return Path(common) if common else None
    except ValueError:
        return None


def _esde_rom_path(file_path: str, rom_root: Path | None) -> str:
    p = Path(file_path)
    if rom_root is not None:
        try:
            rel = p.relative_to(rom_root).as_posix()
            return f"./{rel}"
        except ValueError:
            pass
    return f"./{p.name}"


def _xml_tag(indent: str, tag: str, value: str) -> str:
    return f"{indent}<{tag}>{xml_escape(str(value))}</{tag}>"


def export_esde(cfg_cache: Path, out_dir: Path, platforms: list[str] | None = None) -> dict[str, int]:
    """Esporta la libreria nel formato di ES-DE (EmulationStation Desktop Edition).

    Per ogni console scrive `out_dir/<console>/gamelist.xml` e copia i media disponibili in
    `out_dir/downloaded_media/<console>/<tipo>/<rom_stem>.<ext>`.
    Ritorna `{console: numero_di_giochi}`.
    """
    cfg_cache = Path(cfg_cache)
    out_dir = Path(out_dir)
    by_plat = _load_primary_games(cfg_cache, platforms)
    summary: dict[str, int] = {}

    for pid, games in sorted(by_plat.items()):
        if not games:
            continue
        rom_root = _common_rom_root(games)
        lines = ['<?xml version="1.0"?>', "<gameList>"]
        for g in games:
            first_file = g["files"][0]
            stem = _rom_stem(first_file)
            lines.append("\t<game>")
            lines.append(_xml_tag("\t\t", "path", _esde_rom_path(first_file, rom_root)))
            lines.append(_xml_tag("\t\t", "name", g["title"]))
            if g["description"]:
                lines.append(_xml_tag("\t\t", "desc", g["description"]))
            rdate = _esde_release_date(g["release_date"], g["year"])
            if rdate:
                lines.append(_xml_tag("\t\t", "releasedate", rdate))
            if g["developer"]:
                lines.append(_xml_tag("\t\t", "developer", g["developer"]))
            if g["publisher"]:
                lines.append(_xml_tag("\t\t", "publisher", g["publisher"]))
            if g["genres"]:
                lines.append(_xml_tag("\t\t", "genre", ", ".join(g["genres"])))
            if g["players"]:
                lines.append(_xml_tag("\t\t", "players", g["players"]))
            if g["rating"] is not None:
                try:
                    rval = max(0.0, min(1.0, float(g["rating"])))
                    lines.append(_xml_tag("\t\t", "rating", f"{rval:.2f}"))
                except (TypeError, ValueError):
                    pass
            lines.append("\t</game>")

            # Copia dei media nelle sottocartelle downloaded_media/<console>/<tipo>/
            copied_folders: set[str] = set()
            for mtype, rel in g["media"].items():
                esde_folder = ESDE_MEDIA_DIRS.get(mtype)
                if not esde_folder or esde_folder in copied_folders:
                    continue
                src = _resolve_media_src(cfg_cache, rel)
                if src is None:
                    continue
                dst = out_dir / "downloaded_media" / pid / esde_folder / f"{stem}{src.suffix}"
                _copy_media(src, dst)
                copied_folders.add(esde_folder)

        lines.append("</gameList>")
        xml_path = out_dir / pid / "gamelist.xml"
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        summary[pid] = len(games)

    return summary


def _clean_single_line(val: str) -> str:
    return re.sub(r"[\r\n]+", " ", str(val)).strip()


def export_pegasus(cfg_cache: Path, out_dir: Path, platforms: list[str] | None = None) -> dict[str, int]:
    """Esporta la libreria nel formato di Pegasus (`out_dir/<console>/metadata.pegasus.txt`).
    Copia i media in `out_dir/<console>/media/<tipo>/<rom_stem>.<ext>` e li collega con
    percorsi relativi nei campi `assets.*`. Ritorna `{console: numero_di_giochi}`.
    """
    cfg_cache = Path(cfg_cache)
    out_dir = Path(out_dir)
    by_plat = _load_primary_games(cfg_cache, platforms)
    summary: dict[str, int] = {}

    for pid, games in sorted(by_plat.items()):
        if not games:
            continue
        plat = P.PLATFORMS.get(pid)
        plat_name = plat.name if plat else pid
        plat_dir = out_dir / pid
        plat_dir.mkdir(parents=True, exist_ok=True)
        rom_root = _common_rom_root(games)

        blocks: list[str] = []
        header = [f"collection: {_clean_single_line(plat_name)}", f"shortname: {pid}"]
        if plat and plat.all_exts:
            header.append("extensions: " + ", ".join(plat.all_exts))
        blocks.append("\n".join(header))

        for g in games:
            first_file = g["files"][0]
            stem = _rom_stem(first_file)
            fp = Path(first_file)
            if rom_root is not None:
                try:
                    file_val = fp.relative_to(rom_root).as_posix()
                except ValueError:
                    file_val = fp.as_posix()
            else:
                file_val = fp.as_posix()

            entry = [
                f"game: {_clean_single_line(g['title'])}",
                f"file: {file_val}",
            ]
            if g["sort_title"]:
                entry.append(f"sort-by: {_clean_single_line(g['sort_title'])}")
            if g["developer"]:
                entry.append(f"developer: {_clean_single_line(g['developer'])}")
            if g["publisher"]:
                entry.append(f"publisher: {_clean_single_line(g['publisher'])}")
            for genre in g["genres"]:
                entry.append(f"genre: {_clean_single_line(genre)}")
            if g["players"]:
                entry.append(f"players: {_clean_single_line(g['players'])}")
            rdate = _pegasus_release_date(g["release_date"], g["year"])
            if rdate:
                entry.append(f"release: {rdate}")
            if g["rating"] is not None:
                try:
                    rval = max(0.0, min(1.0, float(g["rating"])))
                    entry.append(f"rating: {round(rval * 100)}%")
                except (TypeError, ValueError):
                    pass
            if g["description"]:
                desc_lines = g["description"].replace("\r\n", "\n").replace("\r", "\n").split("\n")
                entry.append("description:")
                for dl in desc_lines:
                    stripped = dl.strip()
                    entry.append(f"  {stripped}" if stripped else "  .")

            for mtype, rel in g["media"].items():
                asset_key = PEGASUS_ASSETS.get(mtype)
                if not asset_key:
                    continue
                src = _resolve_media_src(cfg_cache, rel)
                if src is None:
                    continue
                rel_dst = Path("media") / mtype / f"{stem}{src.suffix}"
                _copy_media(src, plat_dir / rel_dst)
                entry.append(f"{asset_key}: {rel_dst.as_posix()}")

            blocks.append("\n".join(entry))

        meta_path = plat_dir / "metadata.pegasus.txt"
        meta_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        summary[pid] = len(games)

    return summary

# --------------------------------------------------------------------------- pagina web statica

# CSS e JavaScript della pagina esportata: stringhe grezze (nessuna sequenza di escape interpretata)
_HTML_CSS = r"""
:root{--bg:#0f1117;--card:#171a23;--line:#252a38;--txt:#e8eaf0;--muted:#98a0b3;--accent:#5b8cff;--ok:#67d391}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;padding:14px 18px;background:rgba(15,17,23,.92);border-bottom:1px solid var(--line);
  backdrop-filter:blur(8px);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
h1{font-size:17px;margin:0 12px 0 0;white-space:nowrap}
input,select{background:#12151d;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit}
input#q{flex:1 1 240px;min-width:180px}
.chips{display:flex;gap:6px;flex-wrap:wrap;padding:10px 18px}
.chip{border:1px solid var(--line);background:#141821;color:var(--muted);border-radius:99px;padding:5px 12px;cursor:pointer;font-size:13px}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
main{padding:6px 18px 60px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;cursor:pointer;
  transition:transform .12s ease,border-color .12s ease}
.card:hover{transform:translateY(-2px);border-color:var(--accent)}
.cv{aspect-ratio:3/4;background:hsl(var(--h,220) 24% 18%);display:flex;align-items:center;justify-content:center;position:relative}
.cv img{width:100%;height:100%;object-fit:cover;display:block}
.cv .ph{padding:10px;font-size:12px;color:var(--muted);text-align:center;word-break:break-word}
.cv .tag{position:absolute;left:6px;top:6px;background:rgba(0,0,0,.66);border-radius:5px;padding:2px 6px;font-size:10px;
  letter-spacing:.5px;text-transform:uppercase}
.cv .score{position:absolute;right:6px;top:6px;background:rgba(0,0,0,.66);border-radius:5px;padding:2px 6px;font-size:11px;color:var(--ok)}
.lbl{padding:7px 9px;font-size:13px;line-height:1.25;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
#count{padding:0 18px 8px;color:var(--muted);font-size:13px}
#sheet{position:fixed;inset:0;background:rgba(6,8,12,.78);display:none;z-index:20;padding:24px;overflow:auto}
#sheet.on{display:block}
.box{max-width:820px;margin:0 auto;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
.box h2{margin:0 0 4px;font-size:21px}
.meta{color:var(--muted);font-size:13px;margin-bottom:12px}
.box img.cover{float:right;width:150px;border-radius:8px;margin:0 0 10px 14px}
.box p.desc{white-space:pre-wrap}
.box dl{display:grid;grid-template-columns:130px 1fr;gap:4px 10px;font-size:14px;margin:14px 0 0}
.box dt{color:var(--muted)}
#sheet .close{float:right;background:#222838;border:1px solid var(--line);color:var(--txt);border-radius:8px;
  padding:6px 12px;cursor:pointer;font:inherit}
footer{padding:18px;color:var(--muted);font-size:12px;text-align:center}
"""

_HTML_JS = r"""
const G = window.__GAMES__ || [];
const el = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fold = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const st = { q: "", plat: "", sort: "title", shown: 0, items: [] };

function pass(g) {
  if (st.plat && g.p !== st.plat) return false;
  const toks = fold(st.q).split(/\s+/).filter(Boolean);
  if (!toks.length) return true;
  const hay = fold([g.t, g.desc, g.developer, g.publisher, (g.genres || []).join(" ")].join(" "));
  return toks.every((w) => hay.includes(w));
}
function cmp(a, b) {
  if (st.sort === "-year") return (b.year || 0) - (a.year || 0);
  if (st.sort === "year") return (a.year || 9999) - (b.year || 9999);
  if (st.sort === "-rating") return (b.rating ?? -1) - (a.rating ?? -1);
  return a.sort.localeCompare(b.sort);
}
function card(g, i) {
  return `<div class="card" data-i="${i}"><div class="cv" style="--h:${g.hue}">`
    + (g.cover ? `<img loading="lazy" src="${esc(g.cover)}" alt="">` : `<div class="ph">${esc(g.t)}</div>`)
    + `<span class="tag">${esc(g.platName)}</span>`
    + (g.rating != null ? `<span class="score">${Math.round(g.rating * 100)}</span>` : "")
    + `</div><div class="lbl">${esc(g.t)}</div></div>`;
}
function more() {
  const next = st.items.slice(st.shown, st.shown + 120);
  st.shown += next.length;
  el("#grid").insertAdjacentHTML("beforeend", next.map(card).join(""));
}
function render() {
  st.items = G.filter(pass).sort(cmp);
  st.shown = 0;
  el("#grid").innerHTML = "";
  if (!st.items.length) el("#grid").innerHTML = `<p style="color:var(--muted)">Nessun gioco trovato.</p>`;
  else more();
  el("#count").textContent = `${st.items.length.toLocaleString("it")} giochi`;
}
function openGame(i) {
  const g = st.items[i];
  if (!g) return;
  el("#sheet .box").innerHTML = `<button class="close">Chiudi</button>`
    + (g.cover ? `<img class="cover" src="${esc(g.cover)}" alt="">` : "")
    + `<h2>${esc(g.t)}</h2><div class="meta">${esc([g.platName, g.year].filter(Boolean).join(" · "))}</div>`
    + (g.desc ? `<p class="desc">${esc(g.desc)}</p>` : "")
    + `<dl>`
    + (g.developer ? `<dt>Sviluppatore</dt><dd>${esc(g.developer)}</dd>` : "")
    + (g.publisher ? `<dt>Editore</dt><dd>${esc(g.publisher)}</dd>` : "")
    + ((g.genres || []).length ? `<dt>Generi</dt><dd>${esc(g.genres.join(", "))}</dd>` : "")
    + (g.players ? `<dt>Giocatori</dt><dd>${esc(g.players)}</dd>` : "")
    + ((g.files || []).length ? `<dt>File</dt><dd>${esc(g.files.join(", "))}</dd>` : "")
    + `</dl>`;
  el("#sheet").classList.add("on");
}
el("#q").oninput = (e) => { st.q = e.target.value; render(); };
el("#sort").onchange = (e) => { st.sort = e.target.value; render(); };
el(".chips").onclick = (e) => {
  const b = e.target.closest("[data-plat]");
  if (!b) return;
  st.plat = b.dataset.plat;
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
  render();
};
el("#grid").onclick = (e) => { const c = e.target.closest(".card"); if (c) openGame(+c.dataset.i); };
el("#sheet").onclick = (e) => { if (e.target.id === "sheet" || e.target.classList.contains("close")) el("#sheet").classList.remove("on"); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") el("#sheet").classList.remove("on"); });
// altre schede solo quando servono: la pagina resta leggera anche con migliaia di giochi
new IntersectionObserver((e) => { if (e[0].isIntersecting && st.shown < st.items.length) more(); },
  { rootMargin: "700px" }).observe(el("#more"));
render();
"""


def _html_text(s: str) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _html_attr(s: str) -> str:
    return _html_text(s).replace('"', "&quot;")


def _hue(text: str) -> int:
    """Tonalità stabile dal titolo: colore del segnaposto quando la copertina manca."""
    h = 0
    for ch in str(text or ""):
        h = (h * 31 + ord(ch)) % 360
    return h


def export_html(cfg_cache: Path, out_dir: Path, platforms: list[str] | None = None,
                title: str = "La mia libreria") -> dict[str, int]:
    """Esporta la libreria come **pagina web statica**: `out_dir/index.html` + `out_dir/covers/`.

    Si apre nel browser senza server (funziona anche con `file://`) e si può mettere su un NAS,
    un disco di rete o un sito qualsiasi. Ricerca, filtro per console, ordinamento e scheda del
    gioco sono dentro la pagina (JavaScript incluso): nessuna dipendenza esterna, nessuna richiesta
    di rete, copertine come file locali.
    """
    cfg_cache = Path(cfg_cache)
    out_dir = Path(out_dir)
    by_plat = _load_primary_games(cfg_cache, platforms)
    covers_dir = out_dir / "covers"
    games: list[dict] = []
    summary: dict[str, int] = {}
    used: set[str] = set()

    for pid, items in sorted(by_plat.items()):
        if not items:
            continue
        plat_name = getattr(P.PLATFORMS.get(pid), "name", pid)
        for g in items:
            cover_rel = ""
            rel = g["media"].get("cover") or g["media"].get("boxart") or ""
            src = _resolve_media_src(cfg_cache, rel) if rel else None
            if src is not None:
                base = f"{pid}-{_rom_stem(g['files'][0])}"
                base = re.sub(r"[^\w.\-]+", "_", base)[:110] or "cover"
                name, n = f"{base}{src.suffix.lower()}", 2
                while name in used:
                    name, n = f"{base}-{n}{src.suffix.lower()}", n + 1
                used.add(name)
                try:
                    _copy_media(src, covers_dir / name)
                    cover_rel = f"covers/{name}"
                except OSError:
                    cover_rel = ""
            rating = None
            if g["rating"] is not None:
                try:
                    rating = round(max(0.0, min(1.0, float(g["rating"]))), 3)
                except (TypeError, ValueError):
                    rating = None
            games.append({
                "t": g["title"], "sort": (g["sort_title"] or g["title"]).lower(), "p": pid,
                "platName": plat_name, "year": g["year"], "developer": g["developer"],
                "publisher": g["publisher"], "genres": g["genres"], "players": g["players"],
                "rating": rating, "desc": (g["description"] or "").strip()[:4000],
                "cover": cover_rel, "files": [_rom_stem(f) for f in g["files"][:4]],
                "hue": _hue(g["title"]),
            })
        summary[pid] = len(items)

    plats = sorted({(g["p"], g["platName"]) for g in games}, key=lambda x: x[1].lower())
    chips = ('<button class="chip on" data-plat="">Tutte</button>'
             + "".join(f'<button class="chip" data-plat="{_html_attr(pid)}">{_html_text(name)}</button>'
                       for pid, name in plats))
    data = json.dumps(games, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html_text(title)}</title>
<style>{_HTML_CSS}</style>
</head>
<body>
<header><h1>◆ {_html_text(title)}</h1>
  <input id="q" type="search" placeholder="Cerca titolo, genere, sviluppatore…" autocomplete="off">
  <select id="sort" title="Ordinamento">
    <option value="title">Titolo A–Z</option>
    <option value="year">Anno ↑</option>
    <option value="-year">Anno ↓</option>
    <option value="-rating">Valutazione</option>
  </select>
</header>
<div class="chips">{chips}</div>
<div id="count"></div>
<main><div class="grid" id="grid"></div><div id="more"></div></main>
<footer>{len(games):,} giochi · esportato da GameScraper</footer>
<div id="sheet"><div class="box"></div></div>
<script>window.__GAMES__ = {data};</script>
<script>{_HTML_JS}</script>
</body>
</html>
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    return summary
