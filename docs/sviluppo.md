# Sviluppo e dettagli tecnici

Per chi usa i sorgenti, vuole creare gli eseguibili o è curioso di come lavora Pozzo Launcher rispetto a [Skyscraper](https://github.com/Gemba/skyscraper).

## Avvio rapido dai sorgenti (Windows)

```powershell
python -m pip install -r requirements.txt
copy config.example.toml config.toml     # solo la prima volta, se non hai ancora usato l'exe: poi modifica roms = "..." e le credenziali

## Creare gli eseguibili

Su GitHub li crea il workflow [`.github/workflows/release.yml`](https://github.com/erpaffo/GameScraper/blob/main/.github/workflows/release.yml) a ogni tag di versione (`git tag v0.1.0 && git push origin v0.1.0`): build su Windows, macOS e Linux e pubblicazione nella release. In locale (solo per il sistema in uso):

```bash
pip install -r requirements.txt pyinstaller
pyinstaller packaging/pozzo.spec --noconfirm     # risultato in dist/PozzoLauncher/
```

## Struttura del codice

```
pozzo/
  cli.py          comandi e barre di avanzamento
  config.py       caricamento di config.toml
  platforms.py    piattaforme: estensioni, id ScreenScraper/IGDB/TGDB, sistemi libretro, alias
  scanner.py      scansione cartelle, hash in parallelo, modello Game
  cache.py        cache SQLite
  net.py          client HTTP/2, rate limiter, retry, download media con verifica
  matching.py     confronto titoli (rapidfuzz)
  pipeline.py     orchestrazione a cascata e priorità per campo
  export.py       JSON/CSV/media per console
  api_client.py   client dell'API locale /api/v1 (usato da pozzo-cli e dagli script)
  sources/        libretro, arcadedb, igdb, screenscraper, thegamesdb
  app/            server FastAPI: server.py (endpoint interni /api), apiv1.py (API stabile /api/v1),
                  apitoken.py (token dell'API), static/ (interfaccia, Big Picture, app del telefono)
tests/test_pipeline.py   test end-to-end con API simulate (python tests/test_pipeline.py)
```

L'API stabile per script e integrazioni è descritta in [Riferimento › API locale](riferimento.md#api-locale):
gli endpoint `/api/v1` non cambiano a sorpresa, quelli sotto `/api` sono interni all'interfaccia.

## Esportare la libreria come pagina web

`python -m pozzo export-frontend --format html --out C:\mialibreria` (oppure Impostazioni › ▦ Libreria ›
*Esporta per altri frontend* › **Pagina web**) produce:

```
index.html          la collezione, con ricerca, filtro per console, ordinamento e scheda del gioco
covers/             le copertine (file locali)
```

È **un solo file più le immagini**, senza server e senza risorse esterne: si apre con doppio clic (anche da `file://`),
da un NAS, da un disco di rete o da un sito statico. La pagina funziona anche con migliaia di giochi (le schede si
disegnano man mano che scorri).

## Perché è molto più veloce di Skyscraper

| | Skyscraper 3.7.7 | Pozzo Launcher |
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

## Cosa non è stato portato da Skyscraper

- La generazione di gamelist per EmulationStation, AttractMode e Pegasus: l'output è JSON/CSV neutro.
- Il compositing delle immagini (`artwork.xml`, effetti `fx*`): vengono salvate le immagini originali.
- I moduli MobyGames (API ora a pagamento), OpenRetro, WorldOfSpectrum, import ed esgamelist.

Se servono, si possono aggiungere come nuovi moduli in `pozzo/sources/` o in `export.py`.
