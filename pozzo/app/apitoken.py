"""Token dell'API locale: un segreto per PC, in `<cache>/api-token`.

Serve a `/api/v1` (CLI, Stream Deck, Home Assistant, webhook, script) e impedisce che un programma qualsiasi
o una pagina web raggiungano l'API senza permesso. Non difende dagli altri processi dello stesso utente, che
possono leggere il file: il modello resta «il PC e' fidato, la rete no». Per questo il token si mostra in
Impostazioni › Info e non e' una password.

Il file e' creato al primo avvio con permessi 0600 (su Windows la cartella dati e' gia' dell'utente).
"""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

NAME = "api-token"
_LOCK = threading.Lock()


def path(cfg) -> Path:
    return Path(cfg.cache) / NAME


def get(cfg) -> str:
    """Il token del PC, creato se manca. Lo stesso finche' non si cancella il file."""
    p = path(cfg)
    with _LOCK:
        try:
            t = p.read_text(encoding="utf-8").strip()
            if len(t) >= 32:
                return t
        except OSError:
            pass
        t = secrets.token_urlsafe(32)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        tmp.write_text(t, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows: i permessi POSIX non ci sono, la cartella dati e' gia' privata
        tmp.replace(p)
        return t


def rotate(cfg) -> str:
    """Nuovo token: quello vecchio smette subito di funzionare (gli script vanno riconfigurati)."""
    with _LOCK:
        try:
            path(cfg).unlink()
        except OSError:
            pass
    return get(cfg)


def check(cfg, presented: str | None) -> bool:
    """True se il token presentato e' quello del PC (confronto a tempo costante)."""
    if not presented:
        return False
    return secrets.compare_digest(str(presented), get(cfg))


def presented(headers, query: bytes = b"") -> str | None:
    """Token da `Authorization: Bearer`, `X-Api-Token` o `?token=` (EventSource non manda intestazioni)."""
    h = {k.lower(): v for k, v in headers}
    auth = (h.get(b"authorization") or b"").decode("latin-1").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    x = (h.get(b"x-api-token") or b"").decode("latin-1").strip()
    if x:
        return x
    for part in (query or b"").decode("latin-1").split("&"):
        k, _, v = part.partition("=")
        if k == "token" and v:
            from urllib.parse import unquote_plus
            return unquote_plus(v)
    return None
