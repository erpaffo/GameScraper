# Dati, copie di sicurezza e salute

## Copie di sicurezza dei tuoi dati

In **Impostazioni › Libreria › Copie di sicurezza dei tuoi dati** si salvano in uno zip preferiti, tempi di gioco,
stati, note, collezioni personali e `settings.json`. Le copie stanno in `<cache>/backups`.

- **Copia automatica settimanale** all'avvio; si tengono le ultime 10.
- **Ripristino a caldo**: funziona con l'app aperta. Prima di ripristinare viene salvata una copia dei dati attuali,
  così il ripristino si può annullare.

La libreria (`library.db`) non serve copiarla: si ricostruisce da sola. Per i salvataggi dei giochi vedi
[Giocare](giocare.md#salvataggi).

## La pagina Salute

**Impostazioni › 🩺 Salute** controlla in un solo posto quello che va sistemato:

- cartelle ROM non raggiungibili (per esempio un disco scollegato);
- console senza un emulatore pronto;
- `prod.keys` mancante per i giochi Switch;
- giochi senza dati o senza copertina;
- giochi con i file non trovati;
- fonti online che non rispondono;
- Sunshine fermo o in avvio manuale;
- età dell'ultima copia dei tuoi dati.

Ogni problema ha il pulsante che lo risolve o porta all'impostazione giusta. Il controllo non fa richieste in rete.

## Cartelle rinominate o spostate

Se rinomini o sposti le cartelle dei giochi, la libreria vede i giochi vecchi come «file non trovati» e quelli nuovi
come giochi mai visti. In **Impostazioni › Nuovi giochi** l'app se ne accorge da sola e mostra
**⟲ N giochi sembrano rinominati o spostati**: abbina i giochi con file mancanti alle cartelle nuove (stessa
console e stessa dimensione, oppure nome simile) e **Riassocia i selezionati** li sposta con un clic. Dati
scaricati, tempo di gioco, preferiti e collezioni passano alla nuova posizione.

Prima della riassociazione viene salvata una copia dei database (`*.bak`). Funziona finché la libreria contiene ancora
i giochi con i vecchi percorsi, cioè **prima** di una ricostruzione. Da terminale:

```powershell
python -m gamescraper relink            # anteprima
python -m gamescraper relink --apply    # esegue
```

## Esportare per altri frontend

**Impostazioni › Libreria › Esporta per altri frontend** esporta metadati e immagini per:

| Frontend | Risultato |
|---|---|
| **ES-DE** | `gamelist.xml` più la cartella `downloaded_media` |
| **Pegasus** | `metadata.pegasus.txt` più le immagini |

Da terminale: `gamescraper export-frontend --format esde` oppure `--format pegasus`.

## Dati scaricati

**Impostazioni › Info › Dati scaricati › ⟳ Calcola** mostra dove stanno i dati e quanto pesano: per cartella, per
console (immagini e video, metadati nel database, elenchi degli store online), per fonte dei metadati, per gli altri
dati e per singolo database.

## Ricostruire la libreria

La libreria si ricostruisce da sola dopo ogni scansione, aggiornamento degli store o importazione. A mano:
`python -m gamescraper library`. La ricostruzione non tocca i tuoi dati (`userdata.db`, `settings.json`).

## Log

- Il log normale dell'eseguibile, senza terminale, è in `<cartella dati>/logs/gamescraper.log`.
- Il log diagnostico dettagliato (store, pianificatore, errori) si attiva con `--log` o `GAMESCRAPER_LOG` (vedi
  [Log su file](store-online.md#log-su-file)).
