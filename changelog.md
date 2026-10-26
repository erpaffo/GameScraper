# Changelog

Formato: le novità più recenti in alto. Le versioni seguono `gamescraper/__version__` (ora 0.1.7);
la sezione «Non rilasciato» raccoglie le modifiche fatte dopo l'ultimo zip in `release\` (0.1.5) e non ancora impacchettate.

## Non rilasciato

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

## 0.1.5

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
