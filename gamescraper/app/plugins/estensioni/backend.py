"""Store delle estensioni (incluso nell'app). La logica sta in gamescraper/app/extstore.py.

GET  /plugin-api/estensioni/catalogo  -> estensioni dell'indice con lo stato (disponibile, installata, aggiornamento)
POST /plugin-api/estensioni/installa  {"id"} -> scarica, verifica gli sha256 e installa (o aggiorna)
POST /plugin-api/estensioni/rimuovi   {"id"}
"""

from fastapi import Body, HTTPException


def register(gs):
    from gamescraper.app import extstore

    def guard(fn, *a):
        try:
            return fn(gs.cfg, *a)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # rete, file: l'interfaccia mostra il motivo invece di un 500 muto
            raise HTTPException(502, f"Store non raggiungibile: {e}")

    @gs.route("catalogo")
    def catalogo():
        return guard(extstore.catalog)

    @gs.route("installa", methods=("POST",))
    def installa(body: dict = Body(...)):
        r = guard(extstore.install, str(body.get("id") or ""))
        gs.log(f"Estensione installata: {r['id']} {r.get('version') or ''}")
        return r

    @gs.route("rimuovi", methods=("POST",))
    def rimuovi(body: dict = Body(...)):
        r = guard(extstore.remove, str(body.get("id") or ""))
        gs.log(f"Estensione rimossa: {r['id']}")
        return r
