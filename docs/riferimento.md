# Riferimento: configurazione e comandi

## File e cartelle

| Percorso | Contenuto | Chi lo scrive |
|---|---|---|
| `config.toml` | Configurazione di base (cartelle, fonti, credenziali) | Tu; l'app non lo riscrive mai |
| `settings.json` | Scelte fatte dall'app: emulatori, store, cataloghi, tema, plugin, pianificazioni | L'app (accanto a `config.toml`) |
| `cache/cache.db` | Risposte delle fonti, stato dello scraping, elenchi degli store online | L'app |
| `cache/library.db` | La libreria pronta per l'interfaccia | L'app, a ogni ricostruzione |
| `cache/userdata.db` | Preferiti, tempo di gioco, stati, note, collezioni personali | L'app; mai toccato dalle ricostruzioni |
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
