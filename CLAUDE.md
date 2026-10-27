# Istruzioni per gli agenti

- Rispondi all'utente in italiano.
- Versioni e changelog: segui [VERSIONING.md](VERSIONING.md). In breve: `python tools/version.py wip "<lavoro>" <file>`
  all'inizio di un lavoro lungo; a lavoro finito e test verdi `add <tipo> "<testo>"`, `done "<lavoro>"`, `commit`.
  Mai `git push` né `release.bat` senza richiesta esplicita.
- Test: `PYTHONUTF8=1 python tests/<file>.py` (script con assert); JS: `node --check`.
