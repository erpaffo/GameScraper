"""Client dell'API locale `/api/v1`: un solo posto in cui si sa come si chiama l'app.

Lo usano `pozzo-cli`, gli script e i plugin. Indirizzo e token si possono passare a mano, altrimenti
si prendono da `POZZO_URL` / `POZZO_API_TOKEN` e, per il token, dal file `<cache>/api-token`.

    from pozzo.api_client import Client
    c = Client(cfg=cfg)                      # http://127.0.0.1:8765 + token del PC
    for g in c.games(q="zelda", limit=5)["items"]:
        print(g["id"], g["title"])
    c.play(g["id"], idem="mia-chiave")       # ripetibile: non avvia due volte
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

TIMEOUT = 30.0


class ApiError(Exception):
    """Errore dell'API: `status` HTTP (0 se la rete non risponde), `detail` e `code` come li manda il server."""

    def __init__(self, status: int, detail: str, code: str = ""):
        super().__init__(f"{status}: {detail}" if status else detail)
        self.status, self.detail, self.code = int(status), str(detail), code


class Client:
    def __init__(self, base_url: str = "", token: str = "", cfg=None, timeout: float = TIMEOUT):
        port = int((getattr(cfg, "app", None) or {}).get("port") or 8765) if cfg is not None else 8765
        self.base_url = (base_url or os.environ.get("POZZO_URL") or f"http://127.0.0.1:{port}").rstrip("/")
        self.token = token or os.environ.get("POZZO_API_TOKEN") or self._token_file(cfg)
        self.timeout = timeout

    @staticmethod
    def _token_file(cfg) -> str:
        """Il token del PC: se il file non c'è ancora lo crea, così il client e il server usano lo stesso."""
        if not getattr(cfg, "cache", None):
            return ""
        from .app import apitoken
        try:
            return apitoken.get(cfg)
        except OSError:
            return ""

    def _headers(self, idem: str = "") -> dict:
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if idem:
            h["Idempotency-Key"] = idem
        return h

    def request(self, method: str, path: str, body=None, params=None, idem: str = ""):
        url = self.base_url + (path if path.startswith("/") else "/" + path)
        try:
            r = httpx.request(method, url, json=body, params=params, headers=self._headers(idem), timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ApiError(0, f"App non raggiungibile su {self.base_url} ({type(e).__name__})") from e
        if r.status_code == 204:
            return None
        try:
            data = r.json()
        except ValueError:
            data = {"detail": r.text[:300]}
        if r.status_code >= 400:
            raise ApiError(r.status_code, data.get("detail") or r.reason_phrase, data.get("code") or "")
        return data

    # ------------------------------------------------------------ endpoint
    def status(self) -> dict:
        return self.request("GET", "/api/v1/status")

    def games(self, **filters) -> dict:
        """Filtri: q, platform, store, status, key, favorite, versions, limit, offset, sort."""
        return self.request("GET", "/api/v1/games", params={k: v for k, v in filters.items() if v not in ("", None)})

    def game(self, gid: int) -> dict:
        return self.request("GET", f"/api/v1/games/{gid}")

    def find(self, key: str) -> dict | None:
        items = self.games(key=key).get("items") or []
        return items[0] if items else None

    def play(self, gid: int, idem: str = "") -> dict:
        return self.request("POST", f"/api/v1/games/{gid}/play", idem=idem)

    def favorite(self, gid: int, value: bool = True) -> dict:
        return self.request("POST", f"/api/v1/games/{gid}/favorite", {"value": bool(value)})

    def running(self) -> dict:
        return self.request("GET", "/api/v1/running")

    def stop(self) -> dict:
        return self.request("POST", "/api/v1/running/stop")

    def platforms(self) -> dict:
        return self.request("GET", "/api/v1/platforms")

    def collections(self) -> dict:
        return self.request("GET", "/api/v1/collections")

    def jobs(self) -> dict:
        return self.request("GET", "/api/v1/jobs")

    def job(self, action: str, idem: str = "") -> dict:
        """stores | scan | rebuild | stop-scan | stop-game."""
        return self.request("POST", f"/api/v1/jobs/{action}", idem=idem)

    def saves(self, gid: int) -> dict:
        return self.request("GET", f"/api/v1/saves/{gid}")

    def backup_saves(self, gid: int, idem: str = "") -> dict:
        return self.request("POST", f"/api/v1/saves/{gid}", idem=idem)

    def wishlist(self) -> dict:
        return self.request("GET", "/api/v1/wishlist")

    def wait_end(self, poll: float = 1.5, on_status=None) -> dict:
        """Aspetta che il gioco avviato finisca (per Sunshine, scorciatoie e script). Ritorna lo stato finale."""
        seen = False
        while True:
            st = self.running()
            if on_status:
                on_status(st)
            if st.get("status") in ("starting", "extracting", "running"):
                seen = True
            elif st.get("status") == "error":
                raise ApiError(0, str(st.get("message") or "Avvio non riuscito"), "launch_failed")
            elif seen or st.get("status") == "finished":
                return st
            time.sleep(poll)

    def events(self):
        """Generatore di eventi in tempo reale (SSE): `for kind, data in c.events(): ...`

        Il token va nell'URL perche' qui si legge un flusso continuo; `httpx` manda anche l'intestazione."""
        url = f"{self.base_url}/api/v1/events"
        params = {"token": self.token} if self.token else None
        with httpx.stream("GET", url, params=params, headers=self._headers(), timeout=None) as r:
            if r.status_code >= 400:
                raise ApiError(r.status_code, "Eventi non disponibili")
            kind = ""
            for line in r.iter_lines():
                if line.startswith("event: "):
                    kind = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        yield kind, json.loads(line[6:])
                    except ValueError:
                        yield kind, {}
                    kind = ""
