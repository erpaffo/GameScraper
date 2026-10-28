"""Wikipedia: descrizione, sviluppatore, editore, generi, anno di uscita per i giochi a cui mancano.

Gratuita e senza chiave (API MediaWiki). Si cerca prima su Wikipedia in italiano, poi in inglese
(secondo `languages` della configurazione); se LaunchBox (offline) conosce gia' la pagina Wikipedia del
gioco, si usa direttamente quella. Per ogni gioco servono 1-2 richieste per lingua: una ricerca e
una lettura che restituisce insieme l'introduzione e l'infobox. Le risposte restano in cache 90 giorni.

Interviene solo per i campi ancora mancanti dopo le altre fonti (la pipeline la interroga solo per
quei giochi), quindi conviene metterla per ultima.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from .. import matching
from ..net import RateLimiter, request
from ..scanner import Game
from .base import Result, Skipped, Source

UA = "PozzoLauncher/1.0 (catalogo personale di videogiochi; https://github.com/)"
# parole usate nella ricerca e nomi dei parametri dell'infobox, per lingua
LANG = {
    "it": {"query": "videogioco", "box": re.compile(r"\{\{\s*videogioco\b", re.I),
           "developer": ("sviluppo",), "publisher": ("pubblicazione",), "genres": ("genere",),
           "year": ("anno", "data"), "modes": ("modi gioco", "modigioco", "modalità")},
    "en": {"query": "video game", "box": re.compile(r"\{\{\s*infobox video ?game\b", re.I),
           "developer": ("developer", "developers"), "publisher": ("publisher", "publishers"), "genres": ("genre", "genres"),
           "year": ("released", "release"), "modes": ("modes",)},
}
WANTED = ("description", "developer", "publisher", "genres", "release_date")


def _strip_wiki(s: str) -> str:
    """Wikitesto -> testo semplice (link, template comuni, riferimenti, html)."""
    s = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    for _ in range(4):  # template annidati: elenchi -> voci, il resto via
        s = re.sub(r"\{\{\s*(?:ubl|unbulleted list|plainlist|flatlist|hlist|collapsible list)\s*\|([^{}]*)\}\}",
                   lambda m: "\n".join(x for x in m.group(1).split("|") if "=" not in x), s, flags=re.I)
        s = re.sub(r"\{\{\s*(?:nowrap|vgrelease|video game release)\s*\|([^{}]*)\}\}", lambda m: m.group(1).replace("|", " "), s, flags=re.I)
        s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s*([^\]]*)\]", r"\1", s)
    return s.replace("'''", "").replace("''", "").strip()


def _infobox(wikitext: str, lang: str) -> dict[str, str]:
    m = LANG[lang]["box"].search(wikitext)
    if not m:
        return {}
    # testo del template fino alla graffa di chiusura corrispondente
    depth, i, start = 0, m.start(), m.start()
    while i < len(wikitext) - 1:
        two = wikitext[i:i + 2]
        if two == "{{":
            depth += 1
            i += 2
            continue
        if two == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                break
            continue
        i += 1
    body = wikitext[start + 2:i - 2]
    # parametri di primo livello: si divide sui "|" che non sono dentro [[ ]] o {{ }}
    params, cur, d = {}, "", 0
    for ch_i, ch in enumerate(body):
        pair = body[ch_i:ch_i + 2]
        if pair in ("[[", "{{"):
            d += 1
        elif pair in ("]]", "}}"):
            d -= 1
        if ch == "|" and d <= 0:
            if "=" in cur:
                k, v = cur.split("=", 1)
                params[k.strip().lower()] = v.strip()
            cur = ""
        else:
            cur += ch
    if "=" in cur:
        k, v = cur.split("=", 1)
        params[k.strip().lower()] = v.strip()
    return params


def _values(raw: str) -> list[str]:
    out = []
    for x in re.split(r"[\n,•·]|\s/\s", _strip_wiki(raw)):
        x = x.strip(" *;:.").strip()
        if x and len(x) < 60 and x.lower() not in out and not x.lower().startswith(("na:", "eu:", "jp:")):
            out.append(x)
    return out


class Wikipedia(Source):
    name = "wikipedia"
    provides = set(WANTED)
    concurrency = 2

    def __init__(self, ctx):
        super().__init__(ctx)
        self.limiter = RateLimiter(3, 3)  # cortesia verso Wikimedia: poche richieste al secondo
        langs = [x for x in (getattr(self.cfg, "languages", None) or ["it", "en"]) if x in LANG]
        self.langs = langs + [x for x in ("en",) if x not in langs]
        self.lb = None

    async def setup(self, platforms) -> bool:
        try:  # pagina Wikipedia gia' nota da LaunchBox (offline): niente ricerca
            from ..bulk import LaunchBoxDB
            lb = LaunchBoxDB(self.cfg.cache)
            self.lb = lb if lb.ready() else None
        except Exception:
            self.lb = None
        return True

    async def close(self):
        if self.lb:
            self.lb.close()

    async def _api(self, lang: str, params: dict):
        r = await request(self.client, "GET", f"https://{lang}.wikipedia.org/w/api.php", limiter=self.limiter,
                          cache_ttl=90 * 86400, headers={"User-Agent": UA},
                          params={"format": "json", "formatversion": "2", **params})
        if r is None or r.status_code != 200:
            return None
        return r.json()

    async def _search(self, lang: str, game: Game) -> str | None:
        q = f'{game.search_name} {LANG[lang]["query"]} {game.platform.name}'
        js = await self._api(lang, {"action": "query", "list": "search", "srsearch": q, "srlimit": 6, "srprop": ""})
        hits = [h["title"] for h in (js or {}).get("query", {}).get("search", [])]
        # "Sonic (videogioco 1991)" -> "Sonic"
        hit, _ = matching.best(game.search_name, hits, key=lambda t: re.sub(r"\s*\(.*?\)\s*$", "", t),
                               min_score=max(self.cfg.min_match, 85))
        return hit

    async def _page(self, lang: str, title: str) -> dict | None:
        js = await self._api(lang, {"action": "query", "prop": "extracts|revisions", "exintro": 1, "explaintext": 1,
                                    "rvprop": "content", "rvslots": "main", "rvsection": 0, "redirects": 1,
                                    "titles": title})
        pages = (js or {}).get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            return None
        p = pages[0]
        text = ((p.get("revisions") or [{}])[0].get("slots", {}).get("main", {}).get("content")) or ""
        box = _infobox(text, lang)
        if not box:
            return None  # non e' la pagina di un videogioco
        # "Super Metroid (スーパーメトロイド?, Sūpā Metoroido) è..." -> "Super Metroid è..."
        extract = re.sub(r"\s*\([^()]*[぀-ヿ一-鿿][^()]*\)", "", p.get("extract") or "").strip()
        return {"extract": extract, "box": box, "url": f"https://{lang}.wikipedia.org/wiki/"
                + p["title"].replace(" ", "_")}

    async def lookup(self, game: Game) -> Result | None:
        missing = game.missing(self.cfg.wanted) & self.provides
        if not missing:
            return Skipped()
        known = {}
        if self.lb:
            r = self.lb.find(game.platform.id, [game.search_name], fuzzy=0)
            if r and r.get("wiki") and "wikipedia.org/wiki/" in r["wiki"]:
                u = urlparse(r["wiki"])
                known[u.netloc.split(".")[0]] = unquote(u.path.split("/wiki/", 1)[1]).replace("_", " ")
        f: dict = {}
        for lang in self.langs:
            title = known.get(lang) or await self._search(lang, game)
            if not title:
                continue
            page = await self._page(lang, title)
            if not page:
                continue
            L, box = LANG[lang], page["box"]
            get = lambda keys: next((box[k] for k in keys if box.get(k)), "")  # noqa: E731
            cand = {
                "description": page["extract"] if len(page["extract"]) > 80 else None,
                "developer": ", ".join(_values(get(L["developer"]))[:3]) or None,
                "publisher": ", ".join(_values(get(L["publisher"]))[:3]) or None,
                "genres": _values(get(L["genres"]))[:4] or None,
            }
            y = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", _strip_wiki(get(L["year"])))
            cand["release_date"] = y.group(1) if y else None
            modes = _strip_wiki(get(L["modes"])).lower()
            if modes and re.search(r"single|giocatore singolo", modes) and not re.search(r"multi|cooperativ|co-op|versus", modes):
                cand["players"] = "1"
            for k, v in cand.items():
                if v and k not in f:
                    f[k] = v
            f.setdefault("wikipedia_url", page["url"])
            if not (missing - set(f)):
                break  # tutto quello che mancava e' stato trovato
        f = {k: v for k, v in f.items() if k in missing or k == "wikipedia_url"
             or (k == "players" and not game.data.get("players"))}
        return Result(fields=f) if set(f) - {"wikipedia_url"} else None
