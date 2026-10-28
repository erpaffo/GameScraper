# Riferimento: configurazione e comandi

## File e cartelle

| Percorso | Contenuto | Chi lo scrive |
|---|---|---|
| `config.toml` | Configurazione di base (cartelle, fonti, credenziali) | Tu; l'app non lo riscrive mai |
| `settings.json` | Scelte fatte dall'app: emulatori, store, cataloghi, tema, plugin, pianificazioni | L'app (accanto a `config.toml`) |
| `cache/cache.db` | Risposte delle fonti, stato dello scraping, elenchi degli store online | L'app |
| `cache/library.db` | La libreria pronta per l'interfaccia | L'app, a ogni ricostruzione |
| `cache/userdata.db` | Preferiti, tempo di gioco, stati, note, collezioni personali del profilo Principale; profili di avvio e giochi aggiunti a mano (comuni) | L'app; mai toccato dalle ricostruzioni |
| `cache/profiles/<profilo>.db` | Gli stessi dati personali degli altri profili utente | L'app |
| `cache/backups/` | Copie di sicurezza dei tuoi dati | L'app |
| `cache/saves/` | Copie dei salvataggi dei giochi | L'app |
| `cache/logs/diagnostica.log` | Log diagnostico (con `--log`) | L'app |
| `output/` | Risultato dello scraping da terminale (JSON, CSV, immagini) | `gamescraper run` / `export` |
| `themes/`, `plugins/` | Temi e plugin tuoi | Tu |

La cartella che contiene `config.toml` si trova come descritto in
[Installazione](installazione.md#dove-stanno-i-dati).

## config.toml

Un esempio completo e commentato è `config.example.toml` nel repository.

### [paths]

| Chiave | Significato |
|---|---|
| `roms` | Cartella radice delle ROM (le cartelle console si cercano fino a 4 livelli sotto) |
| `extra_roms` | Altre cartelle ROM, per esempio `["E:/Switch", "F:/Retro"]`. Se le imposti dall'app, l'elenco dell'app sostituisce questo |
| `cache` | Cartella dei dati e dei media scaricati (default `cache`) |
| `output` | Cartella dell'output da terminale (default `output`) |
| `switch_keys` | Percorso di `prod.keys`; vuoto = cercato in Ryujinx, Eden, yuzu |

### [platform_folders]

Associa una cartella non riconosciuta a una console: `"Super Nintendo (PAL)" = "snes"`. Vale anche un percorso
relativo alla radice delle ROM.

### [scrape]

| Chiave | Default | Significato |
|---|---|---|
| `sources` | tutte | Ordine delle fonti |
| `fields` | titolo, descrizione, sviluppatore, editore, generi, data, giocatori, voto | Campi testuali desiderati |
| `media` | `cover`, `screenshot`, `wheel` | Immagini: `cover`, `screenshot`, `title`, `wheel`, `marquee`, `video`, `box3d`, `fanart` |
| `regions` | `eu`, `us`, `wor`, `jp` | Preferenza delle regioni per titoli, immagini, date |
| `languages` | `it`, `en` | Preferenza delle lingue per descrizioni e generi |
| `min_match` | 70 | Punteggio minimo (0–100) per accettare un abbinamento per nome |
| `max_hash_mb` | 512 | I file più grandi non vengono letti (identificati per nome) |
| `full_hash` | false | Calcola anche MD5 e SHA1 (più lento; utile solo per ScreenScraper) |
| `hash_workers` | 0 | Processi di calcolo (0 = tutti i core) |
| `disk` | `auto` | `hdd` o `ssd`: su HDD i file grandi si leggono uno alla volta |
| `io_workers` | 0 | File grandi letti in parallelo (0 = 1 su HDD, tutti i core su SSD) |
| `link_media` | true | Hardlink dalla cache all'output: istantaneo, nessuno spazio in più |

### [field_priority]

Ordine delle fonti per un singolo campo, per esempio `description = ["screenscraper", "igdb", "thegamesdb"]`.

### Fonti online

| Sezione | Chiavi |
|---|---|
| `[screenscraper]` | `user`, `password`, `devid`, `devpassword`, `threads` (0 = massimo dell'account), `quota_reserve`, `ko_reserve`, `search_fallback` (`missing`, `true`, `false`), `only_primary`, `media_parallel` |
| `[igdb]` | `client_id`, `client_secret`, `rate` (max 4), `search_fallback` |
| `[rawg]` | `api_key` |
| `[thegamesdb]` | `api_key`, `rate`, `quota_reserve` |
| `[libretro]` | `refresh_days` (30), `download_threads` (24), `thumbs_by_filename` |
| `[limits.<fonte>]` | `rate` (richieste/s), `daily`, `monthly`, `reserve` |

### [collections]

| Chiave | Significato |
|---|---|
| `min_games` | Una collezione automatica nasce con almeno N giochi (default 3) |
| `exclude` | Franchise da non mostrare, per esempio `["Disney", "NBA"]` |
| `aliases` | Unisce franchise con nomi diversi: `{ "Lara Croft" = "Tomb Raider" }` |
| `show_bios` | Mostra anche i file `[BIOS]` in libreria |

### [emulators] e [launch]

`[emulators]` contiene il percorso di ogni emulatore (`retroarch = "C:/Emulatori/RetroArch/retroarch.exe"`, …).
`[launch.<console>]` sovrascrive il comando di una console:

```toml
[launch.psx]
emulator = "retroarch"
core = "swanstation"
args = ["-L", "{core}", "{rom}", "-f"]
```

`{rom}` è il file del gioco, `{core}` il core di RetroArch. Le stesse scelte si fanno da **Impostazioni › Emulatori**.

### [stores]

| Chiave | Significato |
|---|---|
| `[stores.steam] api_key` | Chiave Web API di Steam, per i giochi non installati |
| `[stores.steam] steamid` | Account Steam; vuoto = quello usato più di recente |
| `[stores.<store>] enabled` | `false` per disattivare uno store |

### [app]

| Chiave | Significato |
|---|---|
| `theme` | Tema (si sceglie anche da **Impostazioni › Aspetto**) |
| `exit_combo` | Select + Start tenuti 2 secondi chiudono il gioco (default `true`) |
| `save_backups` | Copie automatiche dei salvataggi (default `true`) |
| `skip_duplicates` | Mostra una sola versione per gioco (default `true`) |
| `port` | Porta del server locale (default 8765) |
| `notify` | Notifiche di Windows per prezzi, giochi gratis, port aggiornati e lavori finiti (default `true` su Windows) |
| `after_jobs` | Al termine delle operazioni lunghe: `none`, `sleep` o `shutdown` (default `none`) |

### Variabili d'ambiente

| Variabile | Significato |
|---|---|
| `GAMESCRAPER_CONFIG` | Percorso del `config.toml` da usare |
| `GAMESCRAPER_LOG` | Attiva il log diagnostico (`1` o un percorso) |
| `SS_USER`, `SS_PASSWORD`, `SS_DEVID`, `SS_DEVPASSWORD` | Credenziali ScreenScraper |
| `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET` | Credenziali IGDB |
| `TGDB_API_KEY` | Chiave TheGamesDB |

## settings.json

Lo scrive l'app; si può modificare a mano con l'app chiusa. Le sezioni più utili da conoscere:

| Sezione | Contenuto |
|---|---|
| `catalogs` | Store online per console (vedi [Indicare il formato a mano](store-online.md#indicare-il-formato-a-mano)) |
| `pc_stores` | Altri store PC: `[{"url": …, "kind": "repack" o "direct"}]` |
| `emulators`, `launch` | Scelte di **Impostazioni › Emulatori** |
| `stores` | Store attivi e account collegati |
| `paths`, `platform_folders` | Cartelle ROM e associazioni di cartelle fatte dall'app |
| `custom_platforms` | Console aggiunte con **＋ Aggiungi console** |
| `screenscraper`, `igdb`, `rawg`, `thegamesdb`, `limits` | Account e limiti delle fonti impostati dall'app |
| `app` | Tema, plugin, collezioni intelligenti (`smart_collections`), pianificazioni e altre scelte dell'app |
| `app.profiles`, `app.profile` | Profili utente e profilo attivo |
| `app.sync` | Sincronizzazione: cartella condivisa (`folder`), cosa sincronizzare (`what`: `personal` o `all`), PC abbinati, identità di questo PC |
| `app.phone` | Telefono: `touchscreen` (secondo schermo nei giochi DS e 3DS), `screen_idle` (secondi senza tocchi prima che lo schermo inferiore torni sul PC), `mouse`, `mic_device`, `public_url` (indirizzo del tunnel per l'ospite), `video_addr` (indirizzo per Moonlight), `pad` (aspetto, sensibilità e posizioni dei tasti, comuni a tutti i telefoni) |
| `app.discord` | Presenza su Discord: `enabled`, `client_id`, `show_time` |
| `app.notify` | Notifiche di Windows (`false` per spegnerle; di base attive su Windows) |
| `app.after_jobs` | Al termine delle operazioni lunghe: `none`, `sleep` o `shutdown` |
| `app.save_dir` | Cartella delle versioni dei salvataggi (vuota = `<cache>/saves`); mettendoci quella di Ludusavi le copie sono condivise |

## Comandi da terminale

Dai sorgenti si usa `python -m gamescraper <comando>`. Con l'eseguibile si usa `gamescraper-cli <comando>`, che sta
nella stessa cartella dell'app; con il pacchetto `.deb` c'è il comando `gamescraper <comando>`.

| Comando | Cosa fa |
|---|---|
| `app` | Avvia l'app. Opzioni: `--browser`, `--bigpicture`, `--port N`, `--log [percorso]` |
| `bigpicture` | Big Picture a schermo intero (usa l'app già aperta se c'è) |
| `run` | Scansione, scraping ed esportazione (comando predefinito) |
| `scrape` | Solo scraping (salva in cache); `--missing` completa i dati mancanti |
| `export` | Rigenera l'output dalla cache, senza rete |
| `library` | Ricostruisce la libreria dell'app |
| `export-frontend` | Esporta per ES-DE o Pegasus: `--format esde|pegasus`, `--out cartella`, `-p console` |
| `relink` | Riassocia i giochi rinominati o spostati: anteprima, `--apply` per eseguire |
| `play --key <chiave>` | Avvia un gioco e attende che finisca (usato da Sunshine/Moonlight) |
| `stats` | Statistiche della cache per console e fonte |
| `purge --source X -p Y` | Cancella dalla cache i dati di una fonte o di una console |
| `platforms` | Elenco delle console supportate |

Opzioni comuni dello scraping: `-c config.toml`, `-p snes,psx` (console), `-s libretro,igdb` (fonti e ordine),
`-m cover,wheel` (media; `none` = nessuno), `--roms cartella`, `--refresh` (interroga anche i giochi già cercati),
`--limit 50` (solo i primi N giochi, per le prove).

## API locale

L'app espone un'API HTTP su `127.0.0.1:8765` (la porta si sceglie con `[app] port`). Serve a script, Stream Deck,
Home Assistant, webhook e alle estensioni.

**Il server risponde solo al PC su cui gira** e non va pubblicato su Internet: fra gli endpoint ci sono avvii di
giochi e di programmi. Per usarla da un altro dispositivo si passa da una rete privata con abbinamento (come per la
sincronizzazione) o da un tunnel attivo solo quando serve, mai da una porta aperta sul router.

### Le quattro famiglie di endpoint

| Prefisso | Per chi | Regola |
|---|---|---|
| `/api/v1/...` | Script, Stream Deck, Home Assistant, webhook | **Stabile**: gli endpoint restano e cambiano forma solo dopo una deprecazione. Vuole il **token** |
| `/api/...` | L'interfaccia dell'app | Interno: può cambiare a ogni versione. Non vuole token, perché risponde solo al PC (e il browser non può leggerlo da un altro sito) |
| `/plugin-api/<id>/...` | I plugin (vedi [Temi e plugin](ESTENSIONI.md#server-backendpy)) | Definiti dal plugin |
| `/m/api/...` | L'app del telefono e gli ospiti | Vuole il token **del dispositivo**, ottenuto con l'abbinamento (vedi [Telefono e streaming](telefono-streaming.md)) |

### Il token

Un segreto per PC, creato al primo avvio in `<cache>/api-token` (permessi 0600 su Linux e macOS). Si legge e si
cambia da **Impostazioni › Info › API locale** (**Copia** e **Nuovo token**); cambiandolo, gli script che usano quello
vecchio smettono di funzionare.

Tre modi equivalenti di presentarlo:

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/api/v1/games
curl -H "X-Api-Token: $TOKEN"             http://127.0.0.1:8765/api/v1/games
curl "http://127.0.0.1:8765/api/v1/games?token=$TOKEN"    # per EventSource, che non manda intestazioni
```

Non è una difesa contro altri programmi dello stesso utente, che possono leggere il file: serve a impedire che un
programma qualsiasi, una pagina web o un tunnel aperto per sbaglio usino l'API senza permesso.

### Gli endpoint stabili

| Metodo | Percorso | Cosa fa |
|---|---|---|
| `GET` | `/api/v1/status` | L'app c'è: versione, `api`, `library_built_at`. **L'unico senza token** |
| `GET` | `/api/v1/games` | Elenco leggero: `q`, `platform`, `store`, `status`, `key`, `favorite`, `versions`, `limit` (max 1000), `offset`, `sort` (`title`, `year`, `rating`, `playtime`, `last_played`; `-` per il contrario) |
| `GET` | `/api/v1/games/{id}` | Un gioco con le sue versioni, i dati utente e le immagini |
| `POST` | `/api/v1/games/{id}/play` | Avvia il gioco sul PC |
| `POST` | `/api/v1/games/{id}/favorite` | Aggiunge o toglie dai preferiti (`{"value": true}` o `false`), per tutte le versioni |
| `GET` | `/api/v1/running` | Gioco in esecuzione, se c'è |
| `POST` | `/api/v1/running/stop` | Chiude il gioco in esecuzione |
| `GET` | `/api/v1/platforms` | Console e store con quanti giochi, e i filtri disponibili |
| `GET` | `/api/v1/collections` | Collezioni automatiche e personali, con quanti giochi |
| `GET` | `/api/v1/collections/{slug}` | Gli id dei giochi di una collezione |
| `GET` | `/api/v1/jobs` | Lavori in corso o appena finiti, con percentuale |
| `POST` | `/api/v1/jobs/{azione}` | `stores`, `scan`, `rebuild`, `stop-scan`, `stop-game` |
| `GET` | `/api/v1/events` | Eventi in tempo reale (SSE): `game_started`, `game_stopped`, `library_built`, `scrape_finished`, `stores_refreshed` |
| `GET` | `/api/v1/saves/{id}` | Versioni dei salvataggi di un gioco |
| `POST` | `/api/v1/saves/{id}` | Crea subito una versione |
| `POST` | `/api/v1/saves/{id}/restore` | `{"name": "backup-..."}`; `409` se il gioco è aperto |
| `GET` | `/api/v1/backups` | Copie di sicurezza dei dati personali |
| `POST` | `/api/v1/backups` | Ne crea una |
| `GET` | `/api/v1/wishlist` | Lista desideri con i prezzi |

Lo schema completo e aggiornato è su `http://127.0.0.1:8765/openapi.json` (e `/docs` per provarlo dal browser).

### Errori

Sempre nella stessa forma, così un client non deve indovinare:

```json
{"detail": "Gioco non trovato", "code": "not_found"}
```

`404` quando la cosa non c'è, `409` quando c'è ma lo stato non permette l'operazione (gioco già in esecuzione, non
installato, salvataggi con il gioco aperto), `422` per parametri non validi, `401` senza token. `detail` è il testo da
mostrare alla persona, `code` il caso da gestire nel codice.

Le `POST` accettano un'intestazione **`Idempotency-Key`**: ripetendo la stessa richiesta (per esempio dopo un timeout)
entro 5 minuti si riceve la prima risposta e l'azione non viene rifatta. Consigliata su `play`, `jobs`, `saves` e
`backups`.

### Esempi

Con `curl`:

```bash
# il token sta in <cartella dati>/cache/api-token, oppure si copia da Impostazioni › Info
#   Windows: %LOCALAPPDATA%\GameScraper\cache\api-token
#   macOS:   ~/Library/Application Support/GameScraper/cache/api-token
#   Linux:   ~/.local/share/GameScraper/cache/api-token
TOKEN="..."

curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8765/api/v1/games?q=zelda&limit=5"
curl -s -H "Authorization: Bearer $TOKEN" -X POST -H "Idempotency-Key: sera-1" \
     http://127.0.0.1:8765/api/v1/games/1234/play
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8765/api/v1/events"   # eventi in streaming
```

In Python, con il client incluso (indirizzo e token si leggono da soli):

```python
from gamescraper import config
from gamescraper.api_client import Client, ApiError

c = Client(cfg=config.load())            # oppure Client("http://127.0.0.1:8765", token="...")
for g in c.games(q="zelda", limit=5)["items"]:
    print(g["id"], g["title"], g["year"])
c.play(g["id"], idem="sera-1")           # ripetibile: non avvia due volte
c.wait_end()                             # aspetta la fine della partita
for kind, data in c.events():
    print(kind, data)
```

`GAMESCRAPER_URL` e `GAMESCRAPER_API_TOKEN` impostano indirizzo e token per tutti gli script.
