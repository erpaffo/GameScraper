# GameScraper

Scraper di metadati e media per collezioni di ROM, divise per console, con una libreria in stile Playnite (desktop e Big Picture per il controller). È il successore moderno e molto più veloce di [Skyscraper](https://github.com/Gemba/skyscraper) (C++/Qt5).

## Download

Gli eseguibili sono nella pagina **Releases** del repository:

| Sistema | File | Avvio |
|---|---|---|
| Windows 10/11 (64 bit) | `GameScraper-<versione>-windows-x64.zip` | estrai e apri `GameScraper.exe` |
| macOS 11+ (Apple Silicon) | `GameScraper-<versione>-macos-arm64.zip` | estrai e apri `GameScraper.app` |
| Linux (64 bit) | `GameScraper-<versione>-linux-x64.tar.gz` | estrai e avvia `GameScraper/GameScraper` |

Al primo avvio scegli la cartella delle ROM in **Impostazioni › Nuovi giochi › 📁 Cambia…**. Configurazione, cache e libreria stanno nella cartella dell'utente (`%LOCALAPPDATA%\GameScraper` su Windows, `~/Library/Application Support/GameScraper` su macOS, `~/.local/share/GameScraper` su Linux); per una versione **portatile** metti un `config.toml` accanto all'eseguibile. Nella stessa cartella c'è anche `gamescraper-cli` per i comandi da terminale (vedi [Comandi](#comandi)).

Gli eseguibili non sono firmati: su Windows SmartScreen chiede conferma ("Ulteriori informazioni › Esegui comunque"), su macOS la prima volta apri l'app con clic destro › Apri (oppure `xattr -cr GameScraper.app`).

### Creare gli eseguibili

Su GitHub li crea il workflow [`.github/workflows/release.yml`](.github/workflows/release.yml) a ogni tag di versione (`git tag v0.1.0 && git push origin v0.1.0`): build su Windows, macOS e Linux e pubblicazione nella release. In locale (solo per il sistema in uso):

```bash
pip install -r requirements.txt pyinstaller
pyinstaller packaging/gamescraper.spec --noconfirm     # risultato in dist/GameScraper/
```

## Avvio rapido dai sorgenti (Windows)

```powershell
python -m pip install -r requirements.txt
copy config.example.toml config.toml     # poi modifica roms = "..." e le credenziali
python -m gamescraper run                # tutte le console trovate
python -m gamescraper run -p snes,psx    # solo alcune console
```

Le cartelle console vengono cercate sotto la radice ROM **anche annidate**, fino a 4 livelli. Il programma riconosce sia i nomi brevi (`snes`, `psx`, `Mega Drive`, `genesis`…) sia i nomi dei set No-Intro/Redump, anche con suffissi (`Nintendo - GameCube - NKit RVZ [zstd-19-128k]` → gc). Le cartelle con nomi diversi si mappano in `[platform_folders]`. Elenco completo con `python -m gamescraper platforms`.

```
D:/ROM/
  snes/                                     *.sfc *.smc *.zip
  psx/                                      *.chd *.cue *.m3u
  switch/<gioco>/                           una cartella per gioco (anche ps3, psvita, xbox, xbox360)
  Minerva_Myrient/No-Intro/Sega - Mega Drive - Genesis/   *.zip
  Minerva_Myrient/Redump/Nintendo - Wii U - WUX/          *.zip
```

## Telefono e streaming

**App per il telefono** (Impostazioni › 📱 Telefono e streaming › *Attiva l'accesso dal telefono*): una web app servita dal PC in HTTPS sulla rete di casa, da aggiungere alla schermata Home. Si abbina inquadrando il QR (codice di 6 cifre, valido 10 minuti, usabile una volta); senza abbinamento non si raggiunge nulla e anche da abbinati il telefono può solo sfogliare la libreria, avviare/chiudere i giochi, fare schermate e fare da controller.

- **Controller sullo schermo**: diventa un controller Xbox virtuale sul PC (serve il driver [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases)), fino a 4 telefoni = 4 giocatori.
- **Giroscopio**: modalità *Movimento* = protocollo DSU/cemuhook su `127.0.0.1:26760` per Cemu, Ryujinx, Dolphin, Azahar/Citra (nell'emulatore scegli la sorgente di movimento DSU); modalità *Mira* = l'inclinazione muove la levetta destra, per i giochi senza giroscopio.
- La prima apertura mostra l'avviso "connessione non privata" (certificato del tuo PC): *Avanzate › Procedi*. Windows può chiedere di consentire l'app sulle reti private.

**Streaming da remoto** con [Sunshine](https://app.lizardbyte.dev/Sunshine) (sul PC) e [Moonlight](https://moonlight-stream.org) (telefono, tablet, TV, altro PC): *Pubblica in Moonlight* aggiunge a Sunshine "GameScraper Big Picture" e i tuoi giochi principali con le copertine (le altre app di Sunshine restano). Moonlight avvia `gamescraper-cli play --key …`, che resta aperto finché giochi: lo streaming si chiude da solo a fine partita.

## Output

```
output/
  summary.json                 riepilogo per console
  snes/
    games.json                 tutti i dati, con la sorgente di ogni campo
    games.csv                  stesso contenuto, apribile in Excel (separatore ;)
    missing.txt                ROM non trovate
    media/cover/<rom>.png
    media/screenshot/<rom>.png
    media/wheel/<rom>.png
```

## Comandi

| Comando | Cosa fa |
|---|---|
| `run` | scansione, scraping ed export (default) |
| `scrape` | solo scraping (salva in cache) |
| `export` | rigenera l'output dalla cache, senza rete |
| `stats` | statistiche della cache per console e sorgente |
| `purge --source X -p Y` | cancella dalla cache i dati di una sorgente o console |
| `platforms` | elenco delle piattaforme supportate |

Opzioni: `-p snes,psx` (console), `-s libretro,igdb` (sorgenti e ordine), `-m cover,wheel` (media; `none` = nessuno), `--refresh` (re-interroga anche i giochi già cercati), `--limit 50` (per i test).

**L'esecuzione si riprende da dove si era fermata.** Ogni risultato viene salvato subito in `cache/cache.db`. Se interrompi con Ctrl+C, finisce la quota o cade la rete, basta rilanciare lo stesso comando.

## Perché è molto più veloce di Skyscraper

| | Skyscraper 3.7.7 | GameScraper |
|---|---|---|
| Identificazione | Una chiamata API per gioco | **Offline**: CRC e nome confrontati con i DAT No-Intro/Redump di libretro |
| Hash delle ROM | MD5, SHA1 e CRC a blocchi da 1 KB, per thread | **Prima si prova il nome esatto sui DAT**: se corrisponde, CRC/MD5/SHA1 arrivano dal DAT e il file non viene letto. Gli altri: CRC degli ZIP letto dall'indice, blocchi da 8 MB, strategia HDD/SSD, risultati in cache |
| Rete | Sincrona, 1 richiesta alla volta per thread | Asincrona con HTTP/2, rate limit solo dove serve |
| ScreenScraper | Attesa di 1,2 s anche prima di ogni immagine | Media scaricati in parallelo, e solo quelli mancanti |
| IGDB | 2 richieste per gioco, 1 al secondo | **10 giochi per richiesta**, 4 richieste/s |
| ArcadeDB | 1 gioco per richiesta | **25 giochi per richiesta** |
| Cache | `db.xml` in RAM, scritto solo alla fine | SQLite con salvataggio continuo e ripresa automatica |
| Console | Una per esecuzione (`-p`) | Tutte insieme |
| Fonti | Una dopo l'altra | **A catena**: tutte lavorano contemporaneamente su giochi diversi |

Misurato su 10.000 ROM in 5 console, con cartelle annidate: 9.500 identificate per nome senza leggere i file; scansione, hash dei restanti 500, identificazione ed export in **meno di 10 secondi** (senza media). Sulla collezione reale di prova (14.782 giochi su HDD), gli NDS da leggere passano da 149 GB a 3,6 GB. Le immagini da libretro arrivano a circa 16 al secondo (misurate su 300 copertine SNES, l'84% trovate): circa 30 minuti per cover, screenshot e title di 10.000 giochi.

### Come funziona la cascata delle sorgenti

Ogni fonte è uno stadio con la propria coda. Un gioco passa alla fonte successiva appena la precedente ha finito con lui, media compresi. Così, mentre ScreenScraper consuma lentamente la sua quota, libretro e IGDB stanno già lavorando sugli altri giochi. L'ordine di priorità non cambia: una fonte vede un gioco solo dopo tutte quelle che la precedono.

1. **libretro**: identifica ogni ROM offline e ricava titolo, sviluppatore, editore, genere, anno e giocatori. Scarica cover, screenshot e title screen da thumbnails.libretro.com, senza quota.
2. **arcadedb**: solo per le console arcade.
3. **igdb**: descrizioni, rating, cover e artwork per quello che manca ancora. Usa il nome canonico trovato da libretro. Prima cerca 10 giochi per richiesta con il nome esatto, poi fa la ricerca singola solo per quelli non trovati (la multiquery di IGDB non supporta `search`).
4. **screenscraper**: wheel, marquee, video, descrizioni in italiano e giochi non ancora trovati.
5. **thegamesdb**: ultima risorsa (ha una quota mensile).

Ogni sorgente riceve **solo** i giochi a cui manca qualcosa che lei sa fornire. Per sfruttare al massimo la velocità, chiedi i media `wheel`/`marquee`/`video` solo se ti servono: esistono quasi solo su ScreenScraper, e quindi la quota giornaliera del tuo account (es. 20.000 richieste) diventa il limite. Per 10.000 giochi, con quei media, servono circa 10-12.000 richieste ScreenScraper, cioè 1 giorno con un account base. Se la quota finisce, l'esecuzione si ferma pulita e riprende il giorno dopo.

## Credenziali

- **ScreenScraper**: oltre a utente e password servono `devid` e `devpassword` da sviluppatore, da richiedere sul forum di screenscraper.fr. GameScraper **non** riusa le credenziali offuscate nel codice di Skyscraper.
- **IGDB**: crea un'applicazione su dev.twitch.tv e copia Client ID e Client Secret (https://api-docs.igdb.com/#account-creation).
- **TheGamesDB**: chiave API da forums.thegamesdb.net.

## Cosa non è stato portato da Skyscraper

- La generazione di gamelist per EmulationStation, AttractMode e Pegasus: l'output è JSON/CSV neutro.
- Il compositing delle immagini (`artwork.xml`, effetti `fx*`): vengono salvate le immagini originali.
- I moduli MobyGames (API ora a pagamento), OpenRetro, WorldOfSpectrum, import ed esgamelist.

Se servono, si possono aggiungere come nuovi moduli in `gamescraper/sources/` o in `export.py`.

## Struttura del codice

```
gamescraper/
  cli.py          comandi e barre di avanzamento
  config.py       caricamento di config.toml
  platforms.py    piattaforme: estensioni, id ScreenScraper/IGDB/TGDB, sistemi libretro, alias
  scanner.py      scansione cartelle, hash in parallelo, modello Game
  cache.py        cache SQLite
  net.py          client HTTP/2, rate limiter, retry, download media con verifica
  matching.py     confronto titoli (rapidfuzz)
  pipeline.py     orchestrazione a cascata e priorità per campo
  export.py       JSON/CSV/media per console
  sources/        libretro, arcadedb, igdb, screenscraper, thegamesdb
tests/test_pipeline.py   test end-to-end con API simulate (python tests/test_pipeline.py)
```

## Frontend (libreria stile Playnite)

```powershell
python -m gamescraper app              # finestra desktop
python -m gamescraper app --browser    # oppure nel browser (http://127.0.0.1:8765)
python -m gamescraper library          # ricostruisce la libreria dopo uno scraping
```

- **Avvio rapido**: l'app legge `cache/library.db`, un'istantanea già pronta di giochi, dati e collezioni. Tutta la libreria arriva al browser in un'unica risposta (circa 370 KB compressi per 14.000 giochi). Ricerca, filtri e ordinamenti avvengono in memoria, quindi sono istantanei. Nella griglia esistono solo le copertine visibili, e le copertine sono servite come miniature WebP (circa 20 KB invece di 1-2 MB).
- **Console e collezioni**: la barra laterale elenca le console e le collezioni. Le collezioni automatiche (Pokémon, Kingdom Hearts, Tomb Raider…) raccolgono i giochi della stessa saga su tutte le console. Sono generate dai franchise di libretro e IGDB, più un'euristica sul titolo. Puoi rinominarle, nasconderle, togliere o aggiungere giochi e crearne di tue: le modifiche restano anche dopo ogni ricostruzione.
- **Una versione per gioco**: le versioni regionali e le revisioni dello stesso gioco sono raggruppate (Rubino, Ruby, Rubin, Rubis… sono un unico gioco). Viene mostrata la versione nella tua lingua, secondo `languages` e `regions` nel config. Le altre restano nel dettaglio e puoi scegliere quale usare come principale. Il raggruppamento usa il codice prodotto Nintendo (es. AGB-**AXV**J), l'identità IGDB e ScreenScraper.
- **Gioca**: configura gli emulatori in `[emulators]`. Ogni console usa il primo emulatore disponibile tra quelli previsti (elenco in Impostazioni). Gli ZIP di dischi (es. GameCube di Myrient) vengono estratti automaticamente; per i giochi "a cartella" viene scelto il file giusto (PS3 → EBOOT.BIN, Switch → il file base). Tempo di gioco, ultimo avvio e numero di avvii sono registrati.
- **Nuovi giochi e nuove console** (pulsante «⟳ Nuovi giochi»): mostra le console trovate sul disco con il numero di giochi non ancora in libreria. Le cartelle non riconosciute si associano a una console da un menu. Lo scraping parte, si segue con le barre di avanzamento e si può interrompere; alla fine la libreria si aggiorna da sola. Vengono interrogati solo i giochi mai cercati; «Riscarica i dati» forza di nuovo tutti.
- **Dati mancanti**: nella scheda «Nuovi giochi» scegli *Completa i dati mancanti*. Le colonne «Senza dati» e «Incompleti» dicono quanti giochi ne hanno bisogno per console. Le fonti che in passato non avevano trovato un gioco vengono riprovate; quelle che l'avevano trovato no, per non sprecare quota. Nel dettaglio di ogni gioco, il riquadro «Mancano: …» ha il pulsante **⟳ Cerca dati mancanti** per quel solo gioco e tutte le sue versioni. Da riga di comando: `python -m gamescraper scrape --missing`.
- **Emulatori** (Impostazioni › Emulatori): «Trova emulatori» cerca in una cartella gli eseguibili conosciuti (RetroArch, Ryujinx, Eden, melonDS, Dolphin, PCSX2, DuckStation, PPSSPP, RPCS3, Vita3K, Azahar, Cemu, Xenia, xemu, Flycast, shadPS4). Per ogni console scegli l'emulatore («Automatico» = il primo consigliato installato), il core di RetroArch e gli argomenti; con «Altro…» puoi usare qualsiasi emulatore esterno. La colonna Stato dice cosa manca. Le impostazioni vanno in `settings.json`, accanto al config, che non viene mai riscritto.
- **PS4 e nomi in stile scene**: `Sekiro.Shadows.Die.Twice.PS4-CUSA12047` diventa «Sekiro Shadows Die Twice»; anche i codici tra parentesi come `(PCSE00812)` vengono rimossi dal titolo.
- **Scorciatoie**: `/` cerca, `Esc` chiude, `Invio` avvia il gioco aperto, doppio click su una copertina avvia il gioco.

I dati personali (preferiti, tempo di gioco, collezioni tue e modifiche) stanno in `cache/userdata.db`, separati dalla libreria.

## Giochi PC dagli store

La sezione **PC** della barra laterale raccoglie i giochi di Steam, Epic Games, GOG, Amazon Games, Battle.net, Windows/Xbox e Minecraft. Ci sono sia quelli **installati** sia quelli **posseduti ma non installati**. Questi ultimi compaiono in grigio con il pulsante **⬇ Installa**, che apre lo store direttamente sulla pagina del gioco.

| Store | Installati | Non installati | Come |
|---|---|---|---|
| Steam | file locali | ✅ | Chiave Web API gratuita (Impostazioni › Account) |
| Epic Games | file locali | ✅ | «Collega account»: login nella pagina ufficiale Epic |
| GOG.com | registro di Windows | ✅ | «Collega account»: login nella pagina ufficiale GOG |
| Amazon Games / Prime Gaming | ✅ | ✅ | Automatico, letto dall'app Amazon Games |
| Battle.net | ✅ | giochi gratuiti e già usati | Blizzard non offre l'elenco dei giochi posseduti |
| Windows / Xbox | ✅ | — | Servirebbe l'accesso a Xbox Live |
| Minecraft | ✅ Java e Bedrock | ✅ (Microsoft Store) | Automatico |

- **Aggiungere un gioco qualsiasi**: col «＋» accanto a PC scegli un `.exe`. Copertina e immagini vengono cercate su Steam e IGDB.
- **Lo stesso gioco su più store**, per esempio Steam ed Epic, è una scheda sola con più versioni; la principale è quella installata.
- **Tempo di gioco**: viene registrato anche per i giochi avviati tramite un launcher, riconoscendo il processo del gioco nella sua cartella. Steam fornisce anche il suo tempo totale.

## Big Picture (controller)

Pulsante **🎮 Big Picture** in alto, oppure `python -m gamescraper app --bigpicture` per avviare direttamente a schermo intero, ad esempio su un PC collegato alla TV.

- Home a scaffali: *Continua a giocare*, *Preferiti*, *Giochi PC installati*, le collezioni più grandi e le console. Lo sfondo e il logo cambiano con il gioco selezionato.
- Libreria, PC, Console, Collezioni e Preferiti come griglie navigabili; ricerca con tastiera a schermo; pagina del gioco con Gioca/Installa, Preferito e Versioni.
- **Controller**: Xbox, PlayStation, Switch Pro e altri. Croce o stick per muoversi, A conferma, B indietro, Y cerca, X preferito, LB/RB scorrono velocemente. **Select+Start tenuti premuti per 2 secondi chiudono il gioco** in esecuzione: funziona anche mentre il gioco è in primo piano con i controller XInput (Xbox).
- Funziona anche con la tastiera: frecce, Invio, Esc, `/` per cercare, `F` per i preferiti.

## Temi e plugin

Da **Impostazioni › Aspetto** scegli il tema (inclusi: OLED, Chiaro, Retrò, Notte blu) e abiliti i plugin. Temi e plugin si creano nelle cartelle `themes/` e `plugins/` accanto al config:
- un tema è solo CSS;
- un plugin può aggiungere pulsanti, sezioni, pagine, store, fonti di metadati e reagire agli eventi.

Guida completa con esempi: [docs/ESTENSIONI.md](docs/ESTENSIONI.md). È incluso il plugin di esempio **Statistiche**.

## Fonti dei dati e gestione dello scraping

L'ordine e le regole delle fonti si gestiscono da **Impostazioni › 🔌 Fonti**.

| Fonte | Tipo | Cosa dà | Costo per i siti |
|---|---|---|---|
| **nsp** (Switch) | offline | **Titolo, publisher, versione e icona letti dal file NSP/XCI** con il tuo `prod.keys` (cercato in Ryujinx/Eden/yuzu, o `switch_keys` nel config). L'icona è sempre disponibile come copertina | Nessuno |
| libretro | offline | Identificazione per CRC/nome, franchise, immagini | Nessuno (DAT scaricati ogni 30 giorni) |
| **LaunchBox** | offline | **Valutazioni della community** (127.000 giochi, con numero di voti), descrizioni, generi, ESRB, video, **loghi/wheel**, box 3D, fanart | Un download a settimana (~110 MB) |
| **TheGamesDB (copia completa)** | offline | Descrizioni, giocatori, copertine di 121.000 giochi | Un download al mese, **nessuna quota** |
| ArcadeDB | online | Arcade | 25 giochi per richiesta |
| IGDB | online | Descrizioni, **voto utenti e critica**, franchise | 10 giochi per richiesta |
| ScreenScraper | online | Checksum, testi in italiano, video | Quota del tuo account |
| **RAWG** | online | **Voto utenti e Metacritic** | Chiave gratuita, 20.000/mese |
| Recensioni Steam | online | Voto dalle recensioni per i giochi Steam | 1 richiesta/s, riusata per 14 giorni |

**Come si evita di sovraccaricare i siti (e di farsi bloccare)**
- **Prima le fonti offline**: interrogano solo il disco. Le fonti online ricevono solo i giochi a cui manca ancora qualcosa.
- **Limiti per fonte**: richieste al secondo, al giorno e al mese, con un margine di riserva. Il conteggio è salvato e vale anche tra un'esecuzione e l'altra.
- **Interruttore automatico**: dopo errori ripetuti (429, 5xx, timeout) la fonte va in pausa; dopo 3 pause di fila si ferma fino alla prossima esecuzione.
- **Cache delle risposte**: la stessa domanda non viene mai rifatta, e le chiavi API non vengono salvate.
- Valgono anche le protezioni già presenti: thread e quote di ScreenScraper letti dall'account, solo le versioni principali, BIOS esclusi, un solo scraping alla volta.

**Stima e pianificazione**
- **📊 Stima**, nella scheda «Nuovi giochi»: per ogni fonte mostra giochi da interrogare, richieste, tempo e giorni necessari entro i limiti, prima di partire.
- **Scraping automatico**, in Impostazioni › Fonti: ogni giorno all'ora scelta l'app completa i dati mancanti entro i limiti. Se una quota finisce, riprende il giorno dopo.

**Valutazioni**: il voto mostrato combina tutte le fonti pesandole per **numero di voti** (media bayesiana), così 4,7 con 1.800 voti conta più di 5,0 con 2 voti. Il voto della **critica** (Metacritic o IGDB) è mostrato a parte.
