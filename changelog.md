# Changelog

## 0.5.4 (2026-10-27)
### Migliorato

- Manuale completo in docs/ (13 capitoli: ogni funzione, come aggiungere store online e store PC, configurazione e comandi): sito su GitHub Pages e PDF con indice tra i file di ogni release

### Corretto

- Messaggi d'errore degli store che spiegano il motivo: verifica anti-bot (Cloudflare) non superabile con richieste semplici, accesso negato, pagina non trovata, sito che limita le richieste
### In corso

- Prezzi delle key da allkeyshop (lista desideri) — `gamescraper/keyprices.py` `tests/test_keyprices.py` `tests/fixtures/aks_product.html` `tests/fixtures/aks_search.json` `tests/fixtures/aks_history.json`

## 0.5.3 (2026-10-27)
### Migliorato

- Barra del titolo e bordo della finestra seguono il tema: colore esatto su Windows 11, barra scura o chiara su Windows 10

## 0.5.2 (2026-10-27)
### Aggiunto

- Installer per ogni sistema accanto alle versioni portatili: Windows setup.exe (senza permessi di amministratore), macOS .dmg, Linux .deb
- Aggiornamento dall'app: in Impostazioni › Info «⬇ Installa e riavvia» scarica la nuova versione adatta (installer o portatile), ne verifica il checksum, si chiude e si riapre aggiornata; dati e config.toml accanto all'exe restano

## 0.5.1 (2026-10-27)
### Corretto

- L'app non partiva con la versione 0.5.0 (mancava un modulo dei port PC e un import nell'avvio con --log)
- settings.json non si svuota piu' se il file e' bloccato (OneDrive, antivirus) o rotto; scritture da piu' thread senza perdite
- Eseguibili: incluso beautifulsoup4, senza il quale gli store type_a/type_b e «Link → Store» non funzionavano

### Rimosso

- nxbrew_giochi.md (elenco di prova non usato dal programma)

## 0.5.0 (2026-10-27)
### Migliorato

- Anteprima di un indirizzo store con «Prova»: formato, giochi e pagine totali prima di aggiungerlo
- Avvio molto più rapido: i tuoi giochi compaiono in circa un secondo (prima 10-15) con il tema scelto e una barra di caricamento; i giochi del catalogo degli store arrivano subito dopo in sottofondo. Anche l'apertura della scheda di un gioco è più veloce
- **Aggiornamento degli store a coda**: clicchi ⟳ su più store e ognuno entra in coda; restano attivi al massimo
  N elenchi (impostazione «in parallelo», 1–8) e mai due dello stesso sito insieme, gli altri partono appena si
  libera un posto. A coda vuota la libreria si ricostruisce una volta sola.
- **Elenco di tutti gli store sempre visibile** (Impostazioni › Store › Aggiornamento degli store): per ogni riga
  stato, posizione in coda, pagina corrente, secondi per pagina, tempo rimanente e ultima riga di log; **📄** apre il
  log completo di quello store, **⟳** lo accoda da solo.
- **«■ Interrompi»** ferma la lettura a fine pagina (anche a metà di un elenco da centinaia di pagine), svuota la coda
  e **sparisce** appena non c'è più nulla da fermare (per esempio durante la ricostruzione della libreria).
- Gli elenchi letti restano salvati: all'avvio i giochi degli store sono già in libreria e l'aggiornamento può girare
  in sottofondo senza bloccare l'interfaccia.
- I file dell'interfaccia hanno la versione nell'indirizzo (`?v=…`): dopo un aggiornamento il browser non riusa più una
  copia vecchia di `app.js` (era la causa di pulsanti con comportamenti strani).

### Aggiunto

- **🏆 Migliori giochi**: Top 100 di ogni console (pulsante «🏆 Top 100» nella pagina della console) e classifica globale Top 100 / Top 1000 nella barra laterale, fra giochi posseduti e scaricabili dagli store. Entrano solo i giochi con voti affidabili (almeno 2 fonti, voto della critica o 50 voti); il punteggio unisce critica (Metacritic/IGDB) e giocatori. «↻ Aggiorna voti online» chiede a IGDB e RAWG i voti dei candidati (i migliori della console su IGDB + i già ben votati), ora usati anche nelle schede dei giochi da scaricare. Nella globale lo stesso gioco su più console conta una volta (prima la versione posseduta).
- Estensione «Link -> Store»: legge una pagina che raccoglie altri siti, raggruppa i link per intestazione (h2/h3) e li aggiunge come store di una console o come store PC (repack / download diretto)
- **Aggiornamento automatico degli store** (console e PC): all'avvio dell'app, ogni giorno all'ora scelta oppure solo
  manualmente, con il pulsante «⟳ Aggiorna tutti» e più store in parallelo.
- **Log su file** con `--log` (o `GAMESCRAPER_LOG`): ora, decisioni del pianificatore, apertura di ogni elenco, **ogni
  pagina letta** ed errori completi — serve a capire perché un sito è lento o non risponde. Il percorso del file è
  indicato nelle impostazioni degli store.

### Corretto

- Sicurezza: il server dell'app risponde solo a se' stesso (blocca richieste e WebSocket da altri siti e il DNS rebinding) e le icone scaricate dai siti sono servite in sandbox, cosi' uno script in un SVG non puo' usare l'app
- Due clic rapidi (o PC e telefono insieme) non avviano piu' due volte la stessa ricerca o installazione
- Scheda del gioco: la riga «Fonti» compare di nuovo (era sovrascritta dai link di download degli store online)
- **Aggiungere un altro store mentre una lettura è in corso lo accoda** invece di rispondere «già in corso»: usano la
  stessa coda il pulsante ⟳, «Leggi gli elenchi» degli store PC, la pagina store delle console e il plugin Link → Store.
- L'interruzione e l'accodamento non si bloccano più a vicenda: dopo «Interrompi» il job si chiude davvero (prima poteva
  restare «in corso»), e più letture in parallelo non si scontrano sulle stesse connessioni al database.

## 0.4.0 (2026-10-27)
### Aggiunto
- **Store online: tipo di pagina per ogni console** (campo `type` in `settings.json`): `filelist` (elenco di file, invariato),
  `type_a` (elenco paginato con `?lcp_page0=N`), `type_b` (elenco A-Z senza paginazione, con la lettera di ogni gioco) e
  `type_c` (archivio paginato `/page/N/`, es. Ziperto); se il tipo non è indicato viene riconosciuto dalla pagina stessa.
- **Store online: copertine e descrizioni** (pulsante 🖼 nella finestra dello store, con due barre di avanzamento — copertine
  e descrizioni — e pulsante di stop): database offline **GameTDB** per i giochi Switch e lettura delle pagine dei siti
  (nxbrew, ziperto) per copertina, descrizione, Title ID, data, editore, generi e peso. I dati già letti restano in cache:
  ogni esecuzione riprende da dove era.
- **Store online: Ziperto** (elenco paginato letto da solo, `https://www.ziperto.com/nintendo-switch-nsp/`) e **stesso gioco
  da più fonti**: se dopo la pulizia del titolo NXBrew e Ziperto hanno la stessa partita, la libreria mostra una sola scheda
  con un pulsante di scaricamento per ogni sito.
- **Altri store PC** (Impostazioni › Store, separati dagli store ufficiali): si incolla l'indirizzo di una pagina elenco e
  i giochi compaiono tra quelli PC, divisi in **📦 Repack** (download corto, installazione lunga) e **⬇ Download diretto**,
  con filtro, cambio categoria e stesso gioco da più fonti su una sola scheda.
### Migliorato
- Titoli degli store online ripuliti da «Switch NSP (eShop)» e dalla versione finale; le copertine mancanti in una regione
  vengono cercate nelle altre (GameTDB US/EN/JA).
- La tabella degli store mostra anche i **Port PC**, con il numero di port e il pulsante per gestirli.

## 0.3.1 (2026-10-27)
### Aggiunto
- **Port PC** (nuova voce nella barra laterale): i giochi console ricompilati o ricostruiti per Windows (Zelda64Recomp,
  Ship of Harkinian, Banjo Recompiled…) dal catalogo di Quiver Launcher (~580 port, in cache 24 ore).
  - Filtri per console, ricerca, «🎮 Ho la ROM», «Installati», «Da aggiornare».
  - **Installa** dall'ultima release GitHub/GitLab/Codeberg (asset Windows scelto da solo) in `Ports\<cartella>`,
    con `portable.txt` dove il port lo prevede; il port diventa un gioco PC (tempo di gioco, Big Picture, telefono).
  - Più eseguibili: si sceglie quale avviare e resta ricordato. **Disinstalla** sposta la cartella nel Cestino.
  - **Aggiornamenti** (controllo al massimo ogni 12 ore): copia di sicurezza in `Ports\.backup`, salvataggi,
    configurazione e mod conservati, «Salta questa versione». Token GitHub facoltativo (⚙ nella pagina).
  - **ROM della libreria**: la scheda di una ROM mostra «Disponibile come port PC»; per i port che indicano l'hash
    richiesto, «Copia dalla libreria» mette la ROM giusta nella cartella del port dopo il controllo.
  - **Salvataggi** del port nel backup per gioco (i file della cartella che non vengono dalla release).
  - **Mod** da Thunderstore (installa/rimuovi); GameBanana solo come link.
  - **Aggiungi a mano**: da un repository GitHub (`utente/progetto`) o da una cartella già scaricata.
- **Nuovi controller sul telefono** (⚙ › Aspetto del controller):
  - **Buzz!**: pulsante rosso grande e 4 colori, telefono in verticale; un telefono per giocatore (fino a 4).
  - **Wii Remote** in verticale o in orizzontale (come un NES) e **Wii Remote + Nunchuk**: tasti, levetta, inclinazione,
    scuotimento e puntatore arrivano a Dolphin dal server DSU.
  - **Volante**: si sterza inclinando il telefono, pedali a pressione, «Centra».
  - **Chitarra** (Guitar Hero / Rock Band): 5 tasti colorati, plettro, leva del vibrato, Star Power.
  - **Arcade** a 6 tasti per i picchiaduro e **GameCube** (A grande, C-stick giallo, tasti per lettera).
  - **Pistola ottica / touchpad**: il telefono muove il mouse del PC (GunCon, Zapper, pennino del DS). Va permesso sul PC.
  - **Microfono (SingStar)**: la voce va in un cavo audio virtuale del PC (es. VB-Cable) che PCSX2 usa come microfono.
  - Aspetto scelto da solo anche per GameCube, Wii e per i giochi Buzz!, Guitar Hero/Rock Band e SingStar.
- **Sala giocatori**: numero e colore del giocatore sul telefono e sul PC; i telefoni si scambiano il posto senza ricollegarsi.
  Il numero è quello che vedono giochi ed emulatori (indice XInput).
- **Configurazione automatica degli emulatori** (Impostazioni › Telefono e streaming): PCSX2 per Buzz! o per la chitarra,
  Dolphin per il Wii Remote (server DSU, profili «GameScraper Telefono N», Wii Remote 1..N), con copia dei file e «Annulla».
- **Lista desideri: storico dei prezzi** (un punto al giorno, store e negozi di key) con grafichino e avviso quando il prezzo
  è il più basso da quando segui il gioco.
- **Layout del controller sul telefono**: oltre a Xbox, **PlayStation** (croce in alto a sinistra, levette in basso e
  simmetriche come sul DualSense, tasti △ ○ ✕ □ con i loro colori, L1/L2/R1/R2, Create/PS/Options) e **Nintendo Switch**
  (Pro Controller: X in alto, A a destra, B in basso, Y a sinistra, ZL/L/R/ZR, − ⌂ +). Si sceglie in ⚙ del controller.
  Per la Switch i tasti mandano al PC la stessa *posizione* di un controller Xbox (valgono le impostazioni che l'emulatore
  ha già per i controller veri); un'opzione fa mandare invece la *lettera* (A Nintendo = A).
- **Aspetto automatico del controller**: con un gioco PS1/PS2/PS3/PSP/Vita in corso il telefono passa da solo al layout
  PlayStation, con un gioco Switch al layout Switch (disattivabile in ⚙).
- **Vibrazione dal gioco al telefono**: quando il gioco fa vibrare il controller virtuale, vibra il telefono (impulsi
  più lunghi per vibrazioni più forti). Funziona su Android; Safari su iPhone/iPad non permette la vibrazione alle pagine web.
- **Tasti a schermo personalizzabili**: ⚙ › «Sposta e ridimensiona i tasti»: si trascina ogni gruppo (levette, croce,
  tasti azione, dorsali, tasti centrali) e se ne cambia la dimensione. La disposizione si salva per ogni aspetto
  (Xbox, PlayStation, Switch) in percentuale dello schermo; «Ripristina» torna a quella originale.
- **«📱 Gioca su questo telefono»** nella scheda di un gioco sul telefono: avvia il gioco sul PC e spiega come entrarci
  da Moonlight con un tocco tramite la nuova app **«▶ Gioco in corso»** pubblicata in Sunshine (mostra lo schermo del PC).
  Aprire Moonlight direttamente su un gioco da una pagina web non è possibile: Moonlight per iOS non registra schemi
  URL e su Android il suo collegamento ai giochi non è raggiungibile dal browser (verificato sui sorgenti ufficiali).
- **«⚡ Ottimizza lo streaming»** (Impostazioni › Telefono e streaming): controlla Sunshine (avviato, con Windows),
  il preset dell'encoder NVIDIA, il two-pass, la correzione degli errori e se il PC è via cavo o Wi-Fi, con le istruzioni
  per ogni modifica; consigli per TV, box e telefono (Modalità Gioco, cavo, frame pacing, bitrate, statistiche di Moonlight).

### Corretto
- **Giochi avviati dal telefono che non comparivano in «In corso» e «Giocati di recente».** Due cause: la partita veniva
  registrata solo alla fine, e l'app sul PC controllava lo stato solo dei giochi avviati da lei, quindi non ricaricava
  i dati. Ora una partita, da qualunque dispositivo (PC, telefono, Moonlight), entra subito tra i recenti e «In corso»
  quando parte; l'app sul PC si accorge entro pochi secondi delle modifiche fatte altrove (partite, preferiti, stati)
  e mostra anche la barra «In esecuzione» dei giochi avviati dal telefono.
- **Store Minerva: non per tutte le console venivano aggiunti i link.** L'importazione si fermava se una console aveva già gli store nella forma `{"urls": [...], "type": ...}` (le console seguenti restavano senza link); ora il tipo e le opzioni restano e gli URL si aggiungono. Riconosciute anche 3DO, PC Engine CD, Neo Geo CD, Jaguar CD e MS-DOS, prima scartate.

### Cambiato

- Sviluppo: versioni e changelog con `tools/version.py` (numero calcolato dal changelog, commit «Version x.y.z», lavori in corso fuori dal commit); istruzioni in `VERSIONING.md`.

## 0.2.2 (2026-10-26)

### Cambiato
- **Una sola cartella dati per exe e sorgenti.** Prima l'exe usava `%LOCALAPPDATA%\GameScraper\` e i sorgenti il
  `config.toml` del progetto: libreria, cache, preferiti e impostazioni si dividevano in due copie. Ora `config.toml`
  si cerca in quest'ordine: `GAMESCRAPER_CONFIG`, accanto all'exe (versione portatile), cartella dati dell'utente;
  solo se questa non esiste ancora, da sorgenti, quello del progetto o della cartella corrente. Una copia nel progetto
  non viene più letta e il programma lo segnala sulla console; per usarla: `GAMESCRAPER_CONFIG=<percorso>`.
  La copia vecchia nel progetto (`cache\`, `settings.json`) non è stata toccata né cancellata.

### Aggiunto
- **Riassociazione delle cartelle rinominate** in Impostazioni › Nuovi giochi: i giochi con file mancanti vengono
  abbinati alle cartelle nuove (stessa console e stessa dimensione, oppure nome simile) e si possono riassociare
  con un clic: metadati, tempo di gioco, preferiti e collezioni passano alla nuova cartella. Prima si salva una
  copia dei database (`*.bak`, ora con l'API di backup di sqlite, coerente anche a database aperto).
  Nuovi endpoint `GET /api/relink/plan` e `POST /api/relink/apply` (l'abbinamento si ricalcola sul server).
  Limite: funziona finché la libreria contiene ancora i giochi con i vecchi percorsi, cioè prima di una ricostruzione.
- **Copie di sicurezza dei dati utente** (Impostazioni › Libreria): preferiti, tempi di gioco, stati, note, collezioni
  personali e `settings.json` in uno zip in `<cache>/backups`. Copia automatica settimanale all'avvio, si tengono le
  ultime 10. Il ripristino funziona con l'app aperta e prima salva una copia dei dati attuali, così si può annullare.
- **Pagina «Salute»** (Impostazioni › 🩺 Salute): cartelle ROM non raggiungibili, console senza emulatore pronto,
  `prod.keys` mancante, giochi senza dati o copertina, giochi con i file non trovati, fonti online che non rispondono,
  età dell'ultima copia dei dati. Ogni problema ha il pulsante che lo risolve. Nessuna richiesta in rete.
- **Più cartelle ROM** (Impostazioni › Nuovi giochi › Altre cartelle ROM, oppure `[paths] extra_roms`): altri dischi,
  anche una cartella che è essa stessa una console (es. `E:\Switch`). Un disco scollegato viene saltato e segnalato.
- **Nota personale e voto (1-5 stelle)** nella scheda del gioco, validi per tutte le versioni. Per i tag si usano le
  collezioni personali.
- **🎲 Gioco a caso** tra quelli mostrati (filtri e ricerca compresi), preferendo quelli avviabili subito.
- **Giochi gratis**: etichetta «Nuovo» e numero 🆕 nella barra laterale per le offerte comparse dall'ultima visita;
  pulsante «✓ Preso» per segnare quelle già riscattate; ordine per scadenza più vicina, prima quelle ancora da
  prendere; ♡ per quelle nella lista desideri.
- **Schermate di Eden** (e yuzu, suyu, citron) importate dopo ogni partita come già per Ryujinx e gli altri emulatori:
  cartella predefinita `%APPDATA%\eden\screenshots`, versione portatile `user\screenshots` accanto all'exe e percorso
  personalizzato letto da `qt-config.ini`.
- **Copie dei salvataggi dei giochi** (scheda del gioco › 💾 Salvataggi): una copia automatica a ogni fine partita
  (se i salvataggi non superano 1 GB), «Copia adesso» e «Ripristina» (che prima copia i salvataggi attuali). Si tengono
  le ultime 10 per gioco in `<cache>/saves`. Emulatori supportati, perché lì i salvataggi si riconoscono per singolo
  gioco: Eden/yuzu (Title ID nel percorso), Ryujinx (Title ID in `ExtraData0`), RetroArch (file `.srm`/`.state` con
  il nome della ROM). Sui tuoi dati: salvataggi riconosciuti per 6 giochi con Eden e 44 con Ryujinx. Il ripristino
  riscrive i file della copia ma non cancella quelli nuovi creati dopo. Disattivabile con `[app] save_backups = false`.
- **Telefono e Big Picture**: stato del gioco e «Togli da Giocati di recente» anche lì.
- **Lista desideri: prezzi dei negozi di key** (giochi PC con codice Steam) tramite l'API ufficiale di
  **GG.deals** (chiave gratuita, da inserire in ⚙ della lista): prezzo più basso dei negozi di key, sconto rispetto al
  prezzo pieno ufficiale (es. 59,99 € su Steam → 18,50 € = -69%), minimo storico e pulsante 🔑 per il confronto.
  La soglia «≤ €» di ogni gioco vale ora anche per i negozi di key: se uno qualsiasi scende a quel prezzo o sotto
  arriva l'avviso. Allkeyshop non è usabile per questo: blocca l'IP dopo 4-5 richieste e un prezzo per gioco
  richiederebbe una richiesta per ciascuno.
- **Giochi gratis: Prime Gaming anche senza allkeyshop**, dal feed Atom pubblico di LootScraper (link diretto al
  riscatto su Luna, copertina, scadenza e valore). Cache di 12 ore, 3 ore di attesa dopo un errore; se un gioco c'è
  già da allkeyshop si aggiungono solo scadenza e valore. *(Gemini)*
- **Metadati Switch dai file `.nsz` e `.xcz`** (compressi): il Control NCA resta non compresso, quindi si leggono senza
  dipendenze nuove (es. Crawl). Riconosciuti anche come file sciolti fuori dalle cartelle. *(Gemini)*
- **Prova di avvio dell'exe nella release** (`packaging/smoke_test.py`): dopo la build il workflow avvia l'eseguibile
  con una cartella dati temporanea, aspetta che `/api/status` risponda e lo chiude, su Windows, macOS e Linux
  (con `xvfb-run`). Il workflow esegue ora **tutti** i test di `tests/`. *(Gemini)*
- **Streaming su TV, telefono e iPad (Sunshine + Moonlight), sistemato.** Il motivo per cui non funzionava: il servizio
  di Sunshine era fermo e impostato su avvio manuale (dopo un «Esci» dall'icona di Sunshine o un riavvio non ripartiva,
  quindi Moonlight non trovava il PC). «▶ Avvia Sunshine» ora lo avvia **e** lo imposta per partire con Windows, con
  una sola richiesta dei permessi di amministratore; la scheda avvisa se è in avvio manuale e la pagina «Salute»
  segnala quando è fermo. Nuova guida in Impostazioni › Telefono e streaming: TV con il controller collegato in
  Bluetooth alla TV (gli input arrivano al PC come controller Xbox virtuale tramite Moonlight → Sunshine → ViGEmBus),
  telefono/iPad con i tasti a schermo di Moonlight o un controller Bluetooth, telefono come controller.
- **App del telefono: controller vero o tasti a schermo.** Se al telefono/iPad è collegato un controller Bluetooth
  (PS5, PS4, Xbox, Switch Pro, MFi) l'app lo usa come controller del PC al posto dei tasti a schermo (Gamepad API);
  pulsante 🎮 per scegliere *Auto* / *A schermo* / *Il mio*. I tasti a schermo restano disponibili come prima.
- **Switch: aggiornamento e DLC** nella scheda del gioco («Aggiornamento 1.2.0», «3 DLC»), letti dalle intestazioni dei
  pacchetti nella cartella. Sulla tua libreria: 178 giochi con aggiornamento e 81 con DLC (670 in tutto). *(lettura: Gemini)*
- **Esportazione per ES-DE e Pegasus** (Impostazioni › Libreria, oppure `gamescraper export-frontend --format esde|pegasus`):
  `gamelist.xml` + `downloaded_media`, oppure `metadata.pegasus.txt` + media. Sulla tua libreria: Saturn e Switch
  (476 giochi) in circa un secondo. *(formati: Gemini)*
- **RetroAchievements** (Impostazioni › Account: utente e Web API key): nella scheda dei giochi retro, obiettivi
  sbloccati, percentuale e icone. Abbinamento per hash (MD5 già noto dal DAT No-Intro, quindi funziona anche con le ROM
  in `.zip` e con le versioni italiane, che su RetroAchievements hanno il titolo inglese), altrimenti per titolo,
  nome canonico o nome del file. Cache: catalogo 7 giorni, abbinamento 30 giorni, progresso 30 minuti. *(client: Gemini)*
- **GG.deals**: formato della risposta verificato su implementazioni pubbliche dell'API (era scritto a memoria ed era
  giusto); regioni e limiti (100 giochi per richiesta, 1000 l'ora) rispettati. *(Gemini)*
- **Impostazioni › ℹ Info**: versione installata, controllo aggiornamenti sull'ultima release di GitHub (al massimo
  una richiesta ogni 12 ore, avviso una sola volta per versione) e questo changelog, ora incluso nell'eseguibile.

### Migliorato
- **Ricostruzione della libreria quasi due volte più veloce** (da 21,5 a 11,9 s su 20.187 voci): mancava l'indice
  su `games.grp`, quindi ogni aggiornamento delle durate HowLongToBeat scorreva tutta la tabella.

### Corretto
- **Eden**: argomenti di avvio verificati sui sorgenti ufficiali (`-g <gioco>`, `-f` schermo intero); gli argomenti
  extra di un profilo (es. `-f`) ora vanno *prima* di `-g`, che deve essere seguito subito dal gioco. Riconosciuto anche
  l'AppImage di Linux. *(Gemini)*
- **Switch, cartelle con aggiornamenti e DLC**: il gioco base è quello con il Title ID che finisce per `000`; prima un
  file `[v0]` poteva essere scartato e veniva avviato un aggiornamento. *(Gemini)*
- **Pacchetti Switch troncati riconosciuti**: OCTOPATH TRAVELER II è lungo esattamente 4 GiB (limite FAT32, prima parte
  di un NSP diviso) mentre il suo contenuto ne dichiara 5,2: il Control NCA sta oltre la fine del file. Ora l'errore
  lo dice chiaramente invece di «NCA Control non trovato». Il file va riscaricato o ricomposto. *(Gemini)*
- **`release.bat`**: niente più +0.0.1 automatico (vedi nota in alto); `*.bak` in `.gitignore`. *(Gemini)*
- **`release.bat`: zip non più interrotto da «file in uso da un altro processo»** (OneDrive o antivirus che
  aprono i file appena compilati): la compressione ora la fa Python, che legge i file in modo condiviso, invece di
  `Compress-Archive`.
- Un test scriveva lo stato dei giochi gratis nel `settings.json` della cartella corrente; ora usa una cartella temporanea.
- **GitHub Actions**: gli artifact scaricati dal workflow erano uno zip contenente un altro zip (`upload-artifact`
  impacchetta sempre quello che carica). Ora si usa `upload-artifact@v7` con `archive: false`: si scarica direttamente
  `GameScraper-<versione>-<sistema>.zip` (o `.tar.gz` su Linux). Il job di release usa `download-artifact@v8`.

## 0.2.0 (2026-10-25)

### Aggiunto
- **Menu del tasto destro su un gioco** (schermata principale): Gioca, Dettagli, Mostra nella cartella,
  preferiti, aggiungi a una collezione, stato (Da giocare / In corso / Finito / Abbandonato), nascondi.
- **«Rimuovi da…»** nel menu: dalla vista corrente (es. «Giocati di recente», Preferiti, uno stato, una collezione)
  con la voce in evidenza, e dalle altre categorie a cui il gioco appartiene. Togliere da «Giocati di recente»
  azzera solo l'ultima partita; tempo di gioco e avvii restano.
- **Giochi gratis da Steam**: giochi normalmente a pagamento con sconto del 100% (esclusi free to play, DLC e
  colonne sonore), letti dalla ricerca ufficiale di Steam.
- `/api/status` ora include `built_at` (data di costruzione della libreria).

### Corretto
- **Copertine e metadati che sparivano dalla schermata**: dopo una ricostruzione della libreria fatta altrove
  (scansione, CLI, altra finestra) la pagina usava i numeri di gioco vecchi e riceveva 404. Ora confronta la data
  di costruzione ogni 20 s, al ritorno sulla finestra e dopo un errore su copertina/dettaglio, e ricarica da sola.
- Una copertina che dà errore viene riprovata 2 volte prima di essere tolta (prima veniva rimossa al primo errore).
- **Giochi gratis / allkeyshop**: una sola richiesta al widget (niente più secondo tentativo sulla pagina del blog, che
  non contiene l'elenco), interrogato al massimo ogni 12 h, 3 h di attesa dopo un errore di rete, timeout di
  connessione 8 s. Se il sito non risponde (blocca l'IP dopo poche richieste) si tiene l'ultimo elenco buono, con i
  giochi Prime, invece di perderlo.
- **Pagina «Giochi gratis» che bloccava il resto dell'app**: con `cdn.allkeyshop.com` irraggiungibile (timeout 20 s)
  le copertine remote restavano appese e occupavano le poche connessioni del browser verso l'app, così le copertine
  e i dati dei propri giochi, anche cambiando pagina, restavano in coda. Ora le immagini remote hanno timeout brevi
  (connessione 4 s, totale 10 s) e un host che non risponde viene saltato per 5 minuti.
- **«Nuovi giochi» che restavano nuovi dopo lo scraping**: i file `[BIOS]` (64 nella tua raccolta) non entrano mai
  in libreria, quindi venivano contati come nuovi per sempre. Ora «nuovi» sono solo i giochi presenti nelle cartelle
  delle ROM e non ancora nella libreria; i BIOS sono esclusi (a meno di `show_bios`). I giochi aggiunti ma senza
  risultati compaiono tra i «Senza dati».
- Host delle copertine Steam aggiunto a quelli consentiti per le immagini remote.

### Test
- Widget allkeyshop reale come fixture (16 giochi, 9 Prime) e test del comportamento in caso di blocco.
- Test per la rimozione da «Giocati di recente» e per `built_at`.

### Note
- Prime Gaming si legge solo tramite allkeyshop: GamerPower non lo elenca più e la pagina di Amazon richiede login.
- L'exe non è stato provato con un gioco e un emulatore reali; il menu del tasto destro non è stato provato nell'interfaccia.

## 0.1.5 (2026-10-23)

### Aggiunto
- **Emulatore Eden** per Switch (accanto a Ryujinx): argomenti `-g {rom}`, ricerca di `eden.exe`.
  L'argomento `-g` non è verificato sulla documentazione di Eden.
- **Metadati Switch letti dal file** (`.nsp` / `.xci`) con il `prod.keys` dell'utente, come fanno gli emulatori:
  titolo, publisher, versione e icona 256×256 usata come copertina. Fonte `nsp`, offline, in testa alla catena;
  le altre fonti scaricano solo ciò che manca. Configurabile con `[paths] switch_keys`.
  Su 438 giochi Switch, 437 hanno copertina e publisher. Restano fuori `.nsz` e i titoli senza Control NCA
  (es. OCTOPATH TRAVELER II).
- **Comando `relink`** (`--apply`): riassocia i giochi con tutti i file mancanti ai nuovi percorsi dopo uno
  spostamento o la pulizia dei nomi delle cartelle, conservando metadati, tempi di gioco e collezioni
  (con backup dei database).
- **Istanza unica**: una seconda copia dell'app riporta in primo piano la finestra già aperta (`/api/focus`)
  invece di aprire una pagina nel browser.
- `release.bat`: aumenta la versione di 0.0.1, compila con PyInstaller e crea
  `release\GameScraper-<versione>-windows-x64.zip`. Nessuna operazione git.
- Note di progetto per Obsidian in `docs/obsidian/GameScraper/` (18 note: architettura, pipeline, fonti, cache,
  frontend, avvio giochi, store, Big Picture, telefono, plugin, packaging, test, cronologia).

### Corretto
- Build che non apriva i giochi, apriva una pagina web al posto della finestra e mostrava
  `ERR_CONNECTION_REFUSED` su `:8765`: restava in esecuzione una copia vecchia sulla stessa porta.
- Lo zip veniva dichiarato creato anche quando la compressione falliva (file in uso).
- Lettura dei pacchetti XCI: la partizione radice HFS0 è a 0x130, non 0x138; cartelle annidate cercate
  fino a un livello.

### Note
- Esistono due copie dei dati: `cache\` nel progetto e `%LOCALAPPDATA%\GameScraper\cache` per l'exe.
  Vanno tenute allineate a mano.

## 0.1.1 e precedenti

- 0.1.0 – 0.1.1: prima versione della riscrittura in Python di Skyscraper con interfaccia in stile Playnite
  (FastAPI + pywebview): scansione ROM, cascata di fonti (ScreenScraper, IGDB, RAWG, TheGamesDB, Libretro,
  LaunchBox, Wikipedia…), cache, libreria, collezioni, avvio con emulatori, store, Big Picture, versione per telefono.
  Il dettaglio è nello storico git (`git log`).
