"""Manuale in docs/: le immagini citate esistono, i link interni puntano a sezioni vere, il nav di MkDocs esiste.

Esegui: python tests/test_docs.py
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
IMAGE = re.compile(r"!\[[^\]]*\]\((images/[^)\s]+)\)")
LINK = re.compile(r"\]\(([\w-]+\.md)(?:#([\w%\w-]+))?\)")


def slug(text: str, ascii_only: bool) -> str:
    text = unicodedata.normalize("NFKD", text)
    if ascii_only:
        text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s-]+", "-", text)


def headings(path: Path) -> set[str]:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    out = set()
    for title in re.findall(r"^#+ +(.+)$", text, flags=re.M):
        out |= {slug(title, True), slug(title, False)}
    return out


def nav_files(node) -> list[str]:
    out = []
    for item in node:
        for value in item.values():
            out += [value] if isinstance(value, str) else nav_files(value)
    return out


def main():
    pages = {p.name: headings(p) for p in DOCS.glob("*.md")}
    assert pages, "nessuna pagina in docs/"

    used = set()
    for page, anchors in pages.items():
        text = (DOCS / page).read_text(encoding="utf-8")
        for rel in IMAGE.findall(text):
            assert (DOCS / rel).exists(), f"{page}: immagine mancante {rel}"
            used.add(rel)
        for target, frag in LINK.findall(text):
            assert target in pages, f"{page}: pagina mancante {target}"
            if frag:
                assert frag in pages[target], f"{page}: sezione mancante {target}#{frag}"

    unused = {f"images/{p.name}" for p in (DOCS / "images").glob("*")} - used
    assert not unused, f"immagini non citate: {sorted(unused)}"

    nav = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    for name in nav_files(nav):
        assert name in pages, f"mkdocs.yml: pagina mancante {name}"

    print(f"ok  manuale: {len(used)} immagini citate, link interni validi, {len(pages)} pagine")


if __name__ == "__main__":
    main()
