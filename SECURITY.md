# Sicurezza

## Versioni supportate

Le correzioni di sicurezza escono solo per l'**ultima versione** pubblicata nelle [Releases](https://github.com/erpaffo/Pozzo-Launcher/releases). L'app avvisa da sola quando ce n'è una nuova (Impostazioni › Info).

## Come segnalare una vulnerabilità

**Non aprire una segnalazione pubblica.** Usa la segnalazione privata di GitHub:
[Security › Report a vulnerability](https://github.com/erpaffo/Pozzo-Launcher/security/advisories/new).

Indica, se puoi: versione e sistema, cosa permette di fare la vulnerabilità, i passi per riprodurla. Riceverai una risposta appena possibile; a correzione pubblicata la segnalazione viene resa pubblica, con il tuo nome se lo desideri.

## Cosa conta di più

Pozzo Launcher gira sul tuo PC con i tuoi permessi. Le parti esposte, e quindi più importanti da segnalare:

- **Server per il telefono** (HTTPS sulla rete di casa): abbinamento con codice, accesso senza abbinamento, cosa può fare un telefono abbinato o un ospite.
- **Server dell'app** (solo `127.0.0.1`): richieste da altri siti aperti nel browser (CSRF, DNS rebinding).
- **Store delle estensioni**: un'estensione con `backend.py` esegue codice. Lo store installa solo file elencati nell'indice ufficiale e con lo sha256 corretto; un modo per aggirare questa verifica è una vulnerabilità.
- **Aggiornamento automatico**: download e sostituzione dell'eseguibile.
- **Dati personali**: chiavi delle fonti, account degli store e libreria non devono uscire dal PC (vedi [PRIVACY.md](PRIVACY.md)).

Le estensioni di terze parti installate a mano nella cartella `plugins/` girano con gli stessi permessi dell'app: un loro comportamento dannoso non è una vulnerabilità di Pozzo Launcher.
