"""Manuale in PDF: i capitoli di docs/ nell'ordine del "nav" di mkdocs.yml, uniti e convertiti con Pandoc + XeLaTeX.

Uso:  python tools/docs_pdf.py [uscita.pdf] [--font "DejaVu Serif"] [--sans "DejaVu Sans"] [--mono "DejaVu Sans Mono"]
Serve pandoc nel PATH e una distribuzione LaTeX con xelatex (TeX Live, MiKTeX).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
# emoji e simboli colorati: i font di LaTeX non li hanno, nel PDF si tolgono (il testo accanto resta)
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")
# blocchi di codice che vanno a capo invece di uscire dal margine
HEADER = r"""
\usepackage{fvextra}
\DefineVerbatimEnvironment{Highlighting}{Verbatim}{breaklines,breakanywhere,commandchars=\\\{\}}
\usepackage{xurl}
"""


def pages(nav) -> list[str]:
    """File del nav di mkdocs, in ordine (le sezioni si appiattiscono)."""
    out = []
    for item in nav:
        for value in item.values():
            out += [value] if isinstance(value, str) else pages(value)
    return out


def chapter(name: str) -> str:
    """Un capitolo: titolo con id = nome del file, link fra pagine trasformati in link interni al PDF."""
    text = (DOCS / name).read_text(encoding="utf-8")
    stem = Path(name).stem.lower()
    text = re.sub(r"\A# (.+)", lambda m: f"# {m[1]} {{#{stem}}}", text, count=1)
    text = re.sub(r"\]\(([\w-]+)\.md#([\w-]+)\)", r"](#\2)", text)  # pagina.md#sezione -> #sezione
    text = re.sub(r"\]\(([\w-]+)\.md\)", lambda m: f"](#{m[1].lower()})", text)  # pagina.md -> #pagina
    text = re.sub(r"^> Il manuale è disponibile anche in PDF.*?(?:\n>.*)*\n", "", text, flags=re.M)  # ovvio nel PDF
    return EMOJI.sub("", text).strip() + "\n"


def version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "gamescraper" / "__init__.py").read_text(encoding="utf-8"))
    return m[1] if m else ""


def build(out: Path, font: str, sans: str, mono: str) -> Path:
    nav = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    md = "\n\n".join(chapter(p) for p in pages(nav))
    with tempfile.TemporaryDirectory() as t:
        src, head = Path(t) / "manuale.md", Path(t) / "header.tex"
        src.write_text(md, encoding="utf-8")
        head.write_text(HEADER, encoding="utf-8")
        subprocess.run([
            "pandoc", str(src), "-f", "markdown", "-o", str(out.resolve()), "--pdf-engine=xelatex",
            "--toc", "--toc-depth=2", "--number-sections", "--top-level-division=chapter", "-H", str(head),
            "-M", "title=GameScraper", "-M", f"subtitle=Manuale · versione {version()}", "-M", "lang=it",
            "-V", "documentclass=report", "-V", "papersize=a4", "-V", "geometry:margin=2.2cm",
            "-V", f"mainfont={font}", "-V", f"sansfont={sans}", "-V", f"monofont={mono}",
            "-V", "colorlinks=true", "-V", "linkcolor=blue", "-V", "urlcolor=blue", "-V", "toccolor=black",
        ], check=True, cwd=t)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", default="GameScraper-manuale.pdf")
    ap.add_argument("--font", default="DejaVu Serif")
    ap.add_argument("--sans", default="DejaVu Sans")
    ap.add_argument("--mono", default="DejaVu Sans Mono")
    a = ap.parse_args()
    print(build(Path(a.out), a.font, a.sans, a.mono))
