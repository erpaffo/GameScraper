# Giocare

## Configurare gli emulatori

Gli emulatori si configurano in **Impostazioni › Emulatori**, in due tabelle.

**Programmi**: un percorso per emulatore, valido per tutte le console che lo usano. **🔍 Trova emulatori** cerca in
una cartella gli eseguibili conosciuti: RetroArch, Ryujinx, Eden, melonDS, Dolphin, PCSX2, DuckStation, PPSSPP,
RPCS3, Vita3K, Azahar, Cemu, Xenia, xemu, Flycast, shadPS4. **＋ Aggiungi un altro emulatore** aggiunge un programma
qualsiasi.

**Emulatore per console**: per ogni console scegli

- l'**emulatore**: *Automatico* usa il primo installato tra quelli consigliati per quella console; *Altro…* permette
  qualsiasi emulatore esterno;
- il **core** di RetroArch, se la console usa RetroArch;
- gli **argomenti**: `{rom}` è il file del gioco, `{core}` il core di RetroArch.

La colonna **Stato** dice cosa manca (emulatore non trovato, core assente…). Le scelte vanno in `settings.json`; il
`config.toml` non viene mai riscritto. In `config.toml` si possono indicare gli stessi percorsi in `[emulators]` e
sovrascrivere il comando di una console in `[launch.<console>]` (vedi [Riferimento](riferimento.md)).

### Profilo di avvio di un singolo gioco

Nel dettaglio di un gioco, **Profilo di avvio** cambia il modo di avviare **solo quel gioco** (tutte le sue
versioni). I campi vuoti usano le impostazioni della console:

- emulatore e core RetroArch;
- eseguibile diverso (per esempio un'altra versione dell'emulatore);
- argomenti al posto di quelli predefiniti;
- argomenti aggiuntivi, messi prima del file del gioco (per esempio `-f` per lo schermo intero).

## Avviare un gioco

**▶ Gioca**, `Invio` o un doppio clic sulla copertina. L'app sceglie da sola il file giusto:

- gli **ZIP di dischi** (per esempio i GameCube di Myrient) vengono estratti automaticamente prima dell'avvio;
- per i **giochi a cartella** si avvia il file corretto (PS3 → `EBOOT.BIN`, Switch → il file del gioco base, non un
  aggiornamento o un DLC);
- con un emulatore mancante il dettaglio lo dice chiaramente, con il collegamento alle impostazioni.

Durante la partita la barra **In esecuzione** mostra il gioco in corso e permette di chiuderlo.

### Chiudere il gioco con il controller

**Select + Start (View + Menu) tenuti premuti per 2 secondi** chiudono il gioco in esecuzione, anche mentre il gioco
è in primo piano, con i controller XInput (Xbox). Si disattiva con `[app] exit_combo = false`.

## Tempo di gioco e partite

Per ogni gioco l'app registra **tempo di gioco**, **ultima partita** e **numero di avvii**. Una partita entra subito
tra i *Giocati di recente* e nello stato *In corso* appena parte, da qualunque dispositivo (PC, telefono, Moonlight);
l'app sul PC se ne accorge entro pochi secondi.

Per i giochi PC avviati tramite un launcher (Steam, Epic…) il tempo viene registrato riconoscendo il processo del
gioco nella sua cartella. Steam fornisce anche il suo tempo totale, mostrato a parte.

## Salvataggi

Nel dettaglio, **💾 Salvataggi** tiene copie dei salvataggi del gioco:

- una **copia automatica a ogni fine partita** (se i salvataggi non superano 1 GB);
- **Copia adesso** per farne una a mano;
- **Ripristina**, che prima copia i salvataggi attuali (così si può tornare indietro). Il ripristino riscrive i file
  della copia ma non cancella quelli creati dopo.

Si tengono le ultime 10 copie per gioco in `<cache>/saves`. Gli emulatori supportati sono quelli in cui i salvataggi
si riconoscono per singolo gioco:

| Emulatore | Come si riconoscono i salvataggi |
|---|---|
| Eden, yuzu | Title ID nel percorso |
| Ryujinx | Title ID in `ExtraData0` |
| RetroArch | File `.srm` e `.state` con il nome della ROM |

Per i port PC vengono copiati i file della cartella del port che non vengono dalla release. Le copie si disattivano
con `[app] save_backups = false`.

## Schermate

Dopo ogni partita l'app importa le schermate fatte dall'emulatore (Ryujinx, Eden e derivati, e gli altri
supportati). Per Eden cerca nella cartella predefinita `%APPDATA%\eden\screenshots`, nella versione portatile
`user\screenshots` accanto all'exe e nel percorso personalizzato letto da `qt-config.ini`. Le schermate si sfogliano
nella vista **📷 Schermate** e si possono fare anche dal telefono.

## RetroAchievements

In **Impostazioni › Store › RetroAchievements** inserisci il tuo utente e la Web API key. Nel dettaglio dei giochi
retro compaiono gli **obiettivi sbloccati**, la percentuale e le icone.

Il gioco viene abbinato:

1. per **hash**: l'MD5 è già noto dal DAT No-Intro, quindi funziona anche con le ROM in `.zip` e con le versioni
   italiane (che su RetroAchievements hanno il titolo inglese);
2. altrimenti per titolo, nome canonico o nome del file.

Cache: catalogo 7 giorni, abbinamento 30 giorni, progresso 30 minuti.
