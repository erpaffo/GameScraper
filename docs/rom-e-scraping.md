# ROM e scraping dei dati

Questo capitolo spiega come l'app trova le ROM sul disco, come le identifica e da dove scarica dati e immagini.
Tutto si gestisce da **Impostazioni › Nuovi giochi** e **Impostazioni › Fonti**, oppure con il pulsante
**⟳ Nuovi giochi** in alto.

## Organizzare le cartelle

La cartella delle ROM (**Impostazioni › Nuovi giochi › Cartella ROM: 📁 Cambia…**) contiene una cartella per
console. Le cartelle vengono cercate **anche annidate**, fino a 4 livelli:

```
D:/ROM/
  snes/                                                   *.sfc *.smc *.zip
  psx/                                                    *.chd *.cue *.m3u
  switch/<gioco>/                                         una cartella per gioco
  Minerva_Myrient/No-Intro/Sega - Mega Drive - Genesis/   *.zip
  Minerva_Myrient/Redump/Nintendo - Wii U - WUX/          *.zip
```

L'app riconosce:

- i **nomi brevi** e gli alias: `snes`, `psx`, `Mega Drive`, `genesis`…;
- i **nomi dei set No-Intro e Redump**, anche con suffissi:
  `Nintendo - GameCube - NKit RVZ [zstd-19-128k]` diventa GameCube.

Per alcune console (Switch, PS3, PS Vita, Xbox, Xbox 360) ogni sottocartella è un gioco. L'elenco completo delle
console riconosciute si ottiene con `python -m gamescraper platforms`.

### Cartelle non riconosciute

Nella finestra **⟳ Nuovi giochi** le cartelle non riconosciute compaiono con un menu per associarle a una console.
In alternativa si mappano in `config.toml`:

```toml
[platform_folders]
"Super Nintendo (PAL)" = "snes"
"Vari/Collezione SNES" = "snes"   # vale anche un percorso relativo alla radice
```

### Aggiungere una console nuova

**＋ Aggiungi console** crea una console che l'app non conosce. Si indicano:

- **Nome** e **Identificativo** (breve, senza spazi);
- **Estensioni dei file** dei giochi;
- **Nomi della cartella** che la identificano (separati da virgola);
- **Giochi su disco** (ISO/CHD): niente calcolo del checksum, troppo lento sui file grandi;
- **Ogni sottocartella è un gioco**, per le console con giochi a cartella;
- **ID piattaforma IGDB** (facoltativo): migliora la ricerca dei dati.

### Altre cartelle ROM

In **Impostazioni › Nuovi giochi › Altre cartelle ROM** aggiungi altri dischi. Ogni cartella può contenere cartelle
di console oppure essere essa stessa una console (per esempio `E:\Switch`). Un disco scollegato viene saltato e
segnalato, senza bloccare gli altri.

## Cercare i giochi nuovi

**⟳ Nuovi giochi** apre la finestra di scansione: per ogni console trovata sul disco mostra quanti giochi non sono
ancora in libreria e le colonne **Senza dati** e **Incompleti**. Si sceglie cosa fare:

| Modalità | Cosa interroga |
|---|---|
| **Solo giochi nuovi o mai cercati** | I giochi mai cercati prima: veloce anche su librerie grandi |
| **Completa i dati mancanti** | I giochi senza dati o incompleti: riprova solo le fonti che non li avevano trovati |
| **Riscarica tutto da capo** | Tutto, ignorando la cache (usa molta quota delle fonti online) |

Poi **⟳ Cerca nuovi giochi**. L'avanzamento si segue con una barra per fonte, **■ Interrompi** ferma tutto e alla
fine la libreria si aggiorna da sola. **↻ Ricontrolla cartelle** rilegge il disco.

L'esecuzione **riprende da dove si era fermata**: ogni risultato è salvato subito in `cache/cache.db`. Se interrompi,
finisce la quota o cade la rete, basta rilanciare.

I file `[BIOS]` non entrano mai in libreria (a meno di `show_bios`) e non vengono contati come nuovi.

### Stima prima di partire

**📊 Stima** mostra, per ogni fonte, quanti giochi interrogherà, quante richieste servono, quanto tempo e quanti
giorni ci vogliono entro i limiti di ciascuna fonte.

## Come vengono identificati i giochi

L'identificazione è **offline**:

1. **Prima il nome**: il nome del file viene confrontato con i DAT No-Intro e Redump di libretro. Se corrisponde
   esattamente, CRC, MD5 e SHA1 arrivano dal DAT e **il file non viene nemmeno letto**.
2. **Poi il contenuto**, solo per gli altri: il CRC degli ZIP si legge dall'indice dell'archivio, i file si leggono a
   blocchi da 8 MB con una strategia diversa per HDD e SSD (`disk`, `io_workers`), e i risultati restano in cache.
3. I file più grandi di `max_hash_mb` (512 MB di default) non vengono letti: si identificano per nome.

Su una raccolta reale di 14.782 giochi su HDD, i dati da leggere per i giochi DS passano così da 149 GB a 3,6 GB.

### Nomi in stile scene

I nomi delle cartelle PS4 come `Sekiro.Shadows.Die.Twice.PS4-CUSA12047` diventano «Sekiro Shadows Die Twice»;
anche i codici tra parentesi come `(PCSE00812)` vengono tolti dal titolo.

### Switch

I giochi Switch (`.nsp`, `.xci`, `.nsz`, `.xcz`) vengono letti direttamente dal file con il tuo `prod.keys`, come
fanno gli emulatori: titolo, editore, versione e icona 256×256 usata come copertina. Il file delle chiavi è cercato
in Ryujinx, Eden e yuzu, oppure indicato in `[paths] switch_keys`.

Nella scheda del gioco compaiono anche l'**aggiornamento installato** e i **DLC** presenti nella cartella. Il gioco
base è quello con il Title ID che finisce per `000`. Un pacchetto troncato (per esempio un NSP diviso al limite di
4 GiB del FAT32) viene segnalato con un messaggio chiaro: il file va riscaricato o ricomposto.

## Le fonti dei dati

Le fonti si ordinano e si regolano in **Impostazioni › Fonti**. Ogni fonte interroga **solo** i giochi a cui manca
ancora qualcosa che lei sa fornire.

| Fonte | Tipo | Cosa dà | Costo per i siti |
|---|---|---|---|
| **nsp** (Switch) | offline | Titolo, editore, versione, icona dal file | Nessuno |
| **libretro** | offline | Identificazione per CRC/nome, franchise, copertine, screenshot, title screen | Nessuno (DAT riscaricati ogni 30 giorni) |
| **LaunchBox** | offline | Valutazioni della community, descrizioni, generi, ESRB, video, loghi, box 3D, fanart | Un download a settimana (~110 MB) |
| **TheGamesDB (copia completa)** | offline | Descrizioni, giocatori, copertine | Un download al mese, nessuna quota |
| **ArcadeDB** | online | Giochi arcade | 25 giochi per richiesta |
| **IGDB** | online | Descrizioni, voto utenti e critica, franchise | 10 giochi per richiesta |
| **ScreenScraper** | online | Checksum, testi in italiano, wheel, marquee, video | Quota del tuo account |
| **RAWG** | online | Voto utenti e Metacritic | Chiave gratuita, 20.000 richieste al mese |
| **Wikipedia** | online | Descrizioni, sviluppatori, editori, generi, anno (prima in italiano, poi in inglese) | |
| **Recensioni Steam** | online | Voto per i giochi Steam | 1 richiesta al secondo, riusata per 14 giorni |

I **Database locali** (LaunchBox, TheGamesDB) si scaricano per intero una volta a settimana o al mese: dopo, tutte
le ricerche sono sul disco.

### La cascata

Le fonti lavorano **contemporaneamente**, ognuna con la propria coda: un gioco passa alla fonte successiva appena la
precedente ha finito con lui, immagini comprese. Mentre ScreenScraper consuma lentamente la sua quota, libretro e
IGDB lavorano già sugli altri giochi. L'ordine di priorità resta quello scelto: una fonte vede un gioco solo dopo
tutte quelle che la precedono.

Per un singolo campo si può cambiare la priorità con `[field_priority]` (per esempio le descrizioni in italiano, che
esistono quasi solo su ScreenScraper).

### Quali media scaricare

`media` in `config.toml` sceglie le immagini: `cover`, `screenshot`, `title`, `wheel`, `marquee`, `video`, `box3d`,
`fanart`. Wheel, marquee, video e box 3D esistono quasi solo su ScreenScraper: chiedili solo se ti servono, perché
allora ogni gioco passa da ScreenScraper e la sua quota giornaliera diventa il limite.

## Proteggere i siti (e non farsi bloccare)

- **Prima le fonti offline**: le fonti online ricevono solo i giochi a cui manca ancora qualcosa.
- **Limiti per fonte**: richieste al secondo, al giorno e al mese, con un margine di riserva. Si modificano nella
  tabella di **Impostazioni › Fonti** (colonne *Ritmo/s*, *Max/giorno*, *Max/mese*) o con `[limits.<fonte>]`. Il
  conteggio è salvato e vale anche tra un'esecuzione e l'altra.
- **Interruttore automatico**: dopo errori ripetuti (429, 5xx, timeout) la fonte va in pausa; dopo 3 pause di fila si
  ferma fino alla prossima esecuzione.
- **Cache delle risposte**: la stessa domanda non viene mai rifatta. Le chiavi API non vengono salvate nella cache.
- ScreenScraper: thread e quote letti dall'account, solo le versioni principali (`only_primary`), riserva di
  richieste (`quota_reserve`, `ko_reserve`).

## Scraping automatico

In **Impostazioni › Fonti › Scraping automatico** l'app, ogni giorno all'ora scelta, completa i dati mancanti entro i
limiti delle fonti. Se una quota finisce, riprende il giorno dopo.

## Dati mancanti di un solo gioco

Nel dettaglio, il riquadro **Mancano: …** elenca i campi e le immagini che non si sono trovati. **⟳ Cerca dati
mancanti** riprova le fonti per quel gioco e tutte le sue versioni. Da riga di comando:
`python -m gamescraper scrape --missing`.

## Durate (HowLongToBeat)

**Impostazioni › Fonti › Durate dei giochi** scarica da HowLongToBeat quanto dura ogni gioco. Le durate compaiono nel dettaglio, nell'ordinamento per durata, nei filtri Scopri *Brevi* e *Lunghi*
e nelle collezioni intelligenti.

## Credenziali delle fonti

| Fonte | Cosa serve |
|---|---|
| ScreenScraper | Utente e password del tuo account e, in più, `devid` e `devpassword` da sviluppatore (richiesti sul forum di screenscraper.fr) |
| IGDB | Un'applicazione su dev.twitch.tv: Client ID e Client Secret |
| RAWG | Chiave API gratuita da rawg.io/apidocs |
| TheGamesDB | Chiave API dal forum di TheGamesDB |

Le credenziali si inseriscono nella configurazione guidata, in **Impostazioni › Fonti** o in `config.toml`; in
alternativa con le variabili d'ambiente `SS_USER`, `SS_PASSWORD`, `SS_DEVID`, `SS_DEVPASSWORD`, `IGDB_CLIENT_ID`,
`IGDB_CLIENT_SECRET`, `TGDB_API_KEY`.

## Scraping da riga di comando

Lo scraping funziona anche senza interfaccia (vedi [Riferimento](riferimento.md#comandi-da-terminale)):

```powershell
python -m gamescraper run                # tutte le console trovate
python -m gamescraper run -p snes,psx    # solo alcune console
```

Il risultato va in `output/<console>/`: `games.json` (tutti i dati con la fonte di ogni campo), `games.csv`
(apribile in Excel, separatore `;`), `missing.txt` (ROM non trovate) e `media/<tipo>/`.
