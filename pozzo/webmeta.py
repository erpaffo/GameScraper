"""Giochi browser: validazione dell'URL (input non attendibile) e metadati statici della pagina.

Solo http/https, mai host locali o IP privati (anche dopo i redirect). Timeout, dimensione dell'HTML e numero
di redirect sono limitati. Dai tag <head> si leggono titolo, descrizione e immagine Open Graph; niente JavaScript.
Anti DNS-rebinding: i client di questo modulo risolvono il nome UNA volta, verificano che tutti gli indirizzi
siano pubblici e si collegano proprio a quell'IP (TLS/Host restano quelli del sito); vale anche per redirect e
immagini. Con proxy di sistema non si passa (trust_env=False).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from .catalog import BeautifulSoup, _require_soup
from .net import UA

MAX_HTML = 1_000_000  # byte letti dalla pagina: i meta stanno nell'<head>
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(8, connect=5)
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


class WebMetaError(ValueError):
    """URL non valido o pagina non raggiungibile: il messaggio e' pronto per l'utente."""


def _public_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_global
    except ValueError:
        return False


def _pick_ip(host: str, port: int, infos: list) -> str:
    addrs = [i[4][0] for i in infos]
    if not addrs or not all(_public_ip(a) for a in addrs):
        raise httpcore.ConnectError("Indirizzo locale o privato non ammesso")
    return addrs[0]


class _PinnedSync(httpcore.SyncBackend):
    def connect_tcp(self, host, port, *a, **kw):
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as e:
            raise httpcore.ConnectError(str(e))
        return super().connect_tcp(_pick_ip(host, port, infos), port, *a, **kw)


class _PinnedAsync(httpcore.AnyIOBackend):
    async def connect_tcp(self, host, port, *a, **kw):
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        except OSError as e:
            raise httpcore.ConnectError(str(e))
        return await super().connect_tcp(_pick_ip(host, port, infos), port, *a, **kw)


def safe_client(**kw) -> httpx.Client:
    """Client sincrono che si collega solo a indirizzi pubblici (controllo sull'IP realmente usato)."""
    t = httpx.HTTPTransport()
    t._pool._network_backend = _PinnedSync()  # shortcut: attributo privato di httpcore, vedi test_pinned_backend
    return httpx.Client(transport=t, trust_env=False, **kw)


def safe_async_client(**kw) -> httpx.AsyncClient:
    """Come safe_client, asincrono: per scaricare le immagini dei giochi browser (redirect compresi)."""
    t = httpx.AsyncHTTPTransport()
    t._pool._network_backend = _PinnedAsync()
    return httpx.AsyncClient(transport=t, trust_env=False, **kw)


def validate_url(url: str, resolve: bool = True) -> str:
    """Ritorna l'URL normalizzato oppure solleva WebMetaError. resolve=False: niente DNS (solo test sintattico)."""
    url = (url or "").strip()
    if not url or len(url) > 2048 or re.search(r"[\s\x00-\x1f]", url):
        raise WebMetaError("URL non valido")
    if "://" not in url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(/|$))", url):
        url = "https://" + url  # "chess.com" senza schema
    try:
        parts = urlsplit(url)
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise WebMetaError("URL non valido")
    if parts.scheme not in ("http", "https"):
        raise WebMetaError("Sono ammessi solo URL http o https")
    if not host:
        raise WebMetaError("URL senza nome host")
    if parts.username or parts.password:
        raise WebMetaError("URL con credenziali non ammesso")
    host = host.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        if not _public_ip(host):
            raise WebMetaError("Indirizzo locale o privato non ammesso")
    else:
        if host == "localhost" or "." not in host or host.endswith(BLOCKED_SUFFIXES):
            raise WebMetaError("Host locale non ammesso")
        if resolve:
            try:
                addrs = {a[4][0] for a in socket.getaddrinfo(host, None)}
            except OSError:
                raise WebMetaError(f"Impossibile risolvere l'host {host}")
            if not addrs or not all(_public_ip(a) for a in addrs):
                raise WebMetaError("L'host punta a un indirizzo locale o privato: non ammesso")
    return parts.geturl()


def _meta(soup, *names: str) -> str:
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        v = re.sub(r"\s+", " ", (tag.get("content") or "")).strip() if tag else ""
        if v:
            return v
    return ""


def parse_meta(html: str, base_url: str) -> dict:
    """{title, description, image}: Open Graph, poi Twitter card, poi <title>/meta description. Vuoti se assenti."""
    _require_soup()
    soup = BeautifulSoup(html, "html.parser")
    title = _meta(soup, "og:title", "twitter:title") or (
        re.sub(r"\s+", " ", soup.title.get_text()).strip() if soup.title else "")
    image = ""
    raw = _meta(soup, "og:image", "og:image:secure_url", "twitter:image")
    if raw:
        try:
            image = validate_url(urljoin(base_url, raw), resolve=False)
        except WebMetaError:
            image = ""  # immagine con schema o host non ammesso: si ignora
    return {"title": title[:200], "image": image,
            "description": _meta(soup, "og:description", "description", "twitter:description")[:2000]}


def fetch_meta(url: str) -> dict:
    """Scarica la pagina (limiti e redirect controllati) e ne estrae i metadati. Solleva WebMetaError."""
    url = validate_url(url)
    with safe_client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", url) as r:
                    if r.is_redirect:
                        loc = r.headers.get("location")
                        if not loc:
                            raise WebMetaError("Redirect senza destinazione")
                        url = validate_url(urljoin(url, loc))  # ogni tappa e' controllata come l'originale
                        continue
                    if r.status_code >= 400:
                        raise WebMetaError(f"La pagina ha risposto con errore {r.status_code}")
                    if "html" not in r.headers.get("content-type", "html").lower():
                        raise WebMetaError("L'URL non e' una pagina web (HTML)")
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) >= MAX_HTML:
                            break  # basta l'inizio: i meta stanno nell'<head>
                    return parse_meta(body[:MAX_HTML].decode(r.encoding or "utf-8", "replace"), str(r.url))
            except httpx.TimeoutException:
                raise WebMetaError("Tempo scaduto nel contattare il sito")
            except httpx.HTTPError as e:
                if "non ammesso" in str(e):
                    raise WebMetaError(f"{e}")
                raise WebMetaError(f"Sito non raggiungibile ({type(e).__name__})")
    raise WebMetaError(f"Troppi redirect (oltre {MAX_REDIRECTS})")
