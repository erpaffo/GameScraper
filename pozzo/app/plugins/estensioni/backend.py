"""Store delle estensioni (incluso nell'app). La logica sta in pozzo/app/extstore.py.

GET  /plugin-api/estensioni/catalogo        -> estensioni di tutte le fonti con lo stato, e lo stato delle fonti
POST /plugin-api/estensioni/installa        {"id", "fonte"?} -> scarica, verifica gli sha256 e installa (o aggiorna)
POST /plugin-api/estensioni/rimuovi         {"id"}
POST /plugin-api/estensioni/fonti/aggiungi  {"link"} -> repository di un'estensione o altro store (GitHub o index.json)
POST /plugin-api/estensioni/fonti/rimuovi   {"link"}
"""

from fastapi import Body, HTTPException


def register(gs):
    from pozzo.app import extstore

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
        r = guard(extstore.install, str(body.get("id") or ""), body.get("fonte") or None)
        gs.log(f"Estensione installata: {r['id']} {r.get('version') or ''}")
        return r

    @gs.route("rimuovi", methods=("POST",))
    def rimuovi(body: dict = Body(...)):
        r = guard(extstore.remove, str(body.get("id") or ""))
        gs.log(f"Estensione rimossa: {r['id']}")
        return r

    @gs.route("fonti/aggiungi", methods=("POST",))
    def fonti_aggiungi(body: dict = Body(...)):
        link = str(body.get("link") or "")
        r = guard(extstore.add_source, link)
        gs.log(f"Store estensioni: fonte aggiunta {link}")
        return {"fonti": r}

    @gs.route("fonti/rimuovi", methods=("POST",))
    def fonti_rimuovi(body: dict = Body(...)):
        return {"fonti": guard(extstore.remove_source, str(body.get("link") or ""))}
