# Versioni, changelog e commit

Tutto passa da `tools/version.py`. Lo script non fa mai `git push`: il push lo fai tu.

## Come funziona

- La sezione in cima a `changelog.md` è la **versione in preparazione**: è «Non rilasciato», oppure una versione più
  alta di quella su GitHub (`origin/main`). Ogni modifica si scrive lì.
- Il numero si calcola da solo partendo dalla versione precedente:
  - c'è almeno una voce in «Aggiunto» → **+0.1.0** (funzioni nuove);
  - solo «Corretto», «Migliorato», «Cambiato», «Rimosso» → **+0.0.1** (correzioni, pulizia);
  - **+1.0.0** solo con `--major`, per la versione definitiva.
- Finché non fai il push, i commit successivi restano nella stessa versione: lo stesso «Version 0.3.0», con il changelog
  che cresce. Se nel frattempo arriva una funzione nuova, il numero sale da solo (per esempio da 0.2.3 a 0.3.0).
- Dopo il push, la modifica seguente apre una sezione nuova.

## Comandi

```bat
:: aggiungere una modifica al changelog (tipo: aggiunto, cambiato, migliorato, corretto, rimosso)
python tools/version.py add aggiunto "Controller Buzz! sul telefono"

:: inizio di un lavoro lungo (i file elencati restano fuori dai commit finché non è finito)
python tools/version.py wip "Port PC" gamescraper/ports.py gamescraper/stores/ports.py

:: lavoro finito: i suoi file tornano nei commit
python tools/version.py done "Port PC"

:: che versione sta per uscire e cosa è ancora in corso
python tools/version.py status

:: aggiorna changelog e gamescraper/__init__.py, poi git commit -m "Version x.y.z"
python tools/version.py commit
python tools/version.py commit --force    :: anche con lavori in corso: i loro file restano fuori
python tools/version.py commit --major    :: versione definitiva (+1.0.0)

:: pubblicare (a mano)
git push

:: compilare lo zip della versione scritta nell'init (non cambia la versione)
release.bat
```

## Agenti in parallelo

Regole per ogni agente (Claude, Gemini, altri), scritte anche in `CLAUDE.md` e `GEMINI.md`:

1. **All'inizio di un lavoro lungo** (più file, più passaggi): `wip "<nome>" <file nuovi o solo suoi>`.
2. **A lavoro finito e con i test verdi**: `add <tipo> "<cosa cambia per l'utente>"` per ogni novità, poi
   `done "<nome>"`, poi `commit`.
3. **Per una correzione piccola** (un solo passaggio): `add corretto "…"` e `commit`.
4. `commit` senza `--force` si rifiuta finché qualcuno ha un lavoro in corso. Così il commit non contiene codice a metà.
   Con `--force` lascia fuori solo i file elencati nel `wip`.
5. **Mai** `git push` e mai `release.bat` senza che l'utente lo chieda.

Limiti:

- Git committa file interi. Se due lavori toccano lo stesso file (per esempio `server.py` o `app.js`) e uno non è finito,
  quel file può contenere codice a metà: in quel caso aspetta il `done` dell'altro invece di usare `--force`.
- Il lock `.git/version.lock` fa scrivere il changelog e fare il commit a un agente alla volta.
- La versione su GitHub si legge da `origin/main` com'era all'ultimo `git push` o `git fetch`.
