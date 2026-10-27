# Store online: parser selezionabili (`filelist`, `type_a`, `type_b`)

Documento di riepilogo delle modifiche implementate per estendere il meccanismo di **catalogo online**
con un registro di parser scelti dal campo `type` della sezione `catalogs` di `settings.json`.

Il comportamento storico (`filelist`) e la logica Minerva **non sono stati modificati**.

---

## 1. Configurazione: `settings.json`

### Prima (unico formato)

```json
{
  "catalogs": {
    "nds": ["https://example.test/roms/"]
  }
}
```

### Dopo (il formato precedente continua a funzionare)

```json
{
  "catalogs": {
    "nds":  { "urls": ["https://example-a.test/all/"], "type": "type_a" },
    "gba":  { "urls": ["https://example-b.test/all"], "type": "type_b",
              "css": { "az_list": "PLACEHOLDER_CSS_B-az-list", "game": "PLACEHOLDER_CSS_B-game" } },
    "snes": ["https://example-c.test/roms/"]
  }
}
```

- Forma **lista di URL** → tipo `filelist` (default, invariato).
- Forma **oggetto** → `urls` resta la lista dei cataloghi, tutto il resto diventa opzioni del parser.
- `css` è opzionale e serve solo a `type_b` (classi dei due elementi della pagina).
- Togliere tutti gli URL fa uscire la console dai cataloghi **insieme** alle sue opzioni.

Aggiunta in `Config`: `catalog_options: dict` (`console -> {"type": ..., "css": {...}}`).
In `apply_settings()` la sezione viene ricostruita da zero a ogni rilettura, così le console rimosse
spariscono anche dal config in memoria.

---

## 2. `gamescraper/catalog.py`

### Registro dei parser

```python
PARSERS: dict[str, ParserFunc] = {
    "filelist": _parse_filelist,   # adapter su parse_listing(), comportamento invariato
    "type_a":   parse_type_a_index,
    "type_b":   parse_type_b,
}
```

Tutte le funzioni hanno la stessa firma `(html, base_url, exts, config)`; `type_a` restituisce anche
il numero di pagine:

```python
def parse_type_a_index(page, base_url, exts=None, config=None) -> tuple[list[CatalogItem], int]
def parse_type_a_page(page, base_url, exts=None, config=None) -> list[CatalogItem]   # pagine 2..N
def parse_type_b(page, base_url, exts=None, config=None) -> list[CatalogItem]
def parse_type_c_index(page, base_url, exts=None, config=None) -> tuple[list[CatalogItem], int]
def parse_type_c_page(page, base_url, exts=None, config=None) -> list[CatalogItem]   # pagine 2..N
```

Scelta del parser:

```python
parser_type(cfg, pid, options=None) -> str
# 1. `options["type"]` se passato a fetch()
# 2. cfg.catalog_options[pid]["type"]
# 3. "filelist"
```

### Modello delle voci

Le voci erano già `dict` in tutto il progetto (nessuna classe `CatalogItem` esistente): è stato aggiunto
un `TypedDict` che documenta i campi **senza cambiare il formato dei dati**.

```python
class CatalogItem(TypedDict):
    name: str            # titolo del gioco (per type_a/type_b non è un nome di file)
    size: int | None     # dimensione in byte, se la pagina la riporta
    url: str             # link assoluto alla scheda/scarica
    magnet: str | None
    group: NotRequired[str | None]   # lettera del raggruppamento (solo type_b)
```

### `type_a` — elenco paginato

- Voci: `<ul class="lcp_catlist">` → `li > a[href]`; nome = testo del link (spazi multipli normalizzati,
  titoli su più righe uniti).
- Paginazione: `<ul class="lcp_paginator">` → massimo `?lcp_page0=N` nei link; `1` se il paginatore manca.
- Le voci puntano alla **scheda** del gioco, non a un file: **nessun filtro per estensione** (altrimenti
  si perderebbero tutte).
- `fetch()` scarica la prima pagina, poi le pagine `2..N` con `page_url(url, n)` che imposta `lcp_page0`
  preservando gli altri parametri e il fragment; i nomi già visti non vengono duplicati.

### `type_c` — archivio paginato (Ziperto)

- Voci: ogni `<article>` → `h2.post-title a` (o `h2.entry-title a`, `a[rel=bookmark]`), nome = testo del link.
- Paginazione: `/categoria/page/N/`; il totale è il massimo `N` dei link del paginatore
  (Ziperto: `https://www.ziperto.com/nintendo-switch-nsp/` → 1.373 pagine, 10 giochi per pagina).
- Se il paginatore non mostra numeri ma solo «Next», si seguono i link `rel="next"` fino in fondo.
- Classi CSS non configurabili: la struttura WordPress è standard, quindi vale per più archivi.

### `type_b` — elenco A‑Z statico
- Contenitore `<div class="...-az-list">`, separatori `<h2 id="letter-X">`, item
  `<div class="...-game"><a href>…</a></div>`.
- Ogni voce porta `group` con la lettera del separatore (`#`, `A`, `B`, …).
- Le classi CSS sono configurabili (`config["css"]["az_list"]`, `config["css"]["game"]`), con i valori
  generici come default: lo stesso parser vale per più siti senza toccare il codice.
- Nessuna paginazione: **una sola richiesta**.
- Scarta link `javascript:`, `#`, `magnet:` e link fuori dagli item.

### Cache (`kv` di `cache.db`)

| Tipo | Chiave | Contenuto |
|------|--------|-----------|
| `filelist` | `catalog:<pid>:<sha1(url)[:12]>` | invariata |
| `type_a` | `catalog:type_a:<url>?lcp_page0=N` | una voce per pagina |
| `type_b` | `catalog:type_b:<url>` | indice unico |

Ogni valore contiene `url`, `index_url`, `pid`, `time`, `entries` (già parsate) e, per `type_a`,
`page` e `total_pages`. Salvare le voci già parsate evita di riparsare l'HTML in `build.py`.

Helper di lettura:

```python
cached_info(cache, pid, url)    # {"entries": [...], "time": float, "total_pages": int | None}
cached_entries(cache, pid, url) # solo le voci
```

**Potatura** (`_prune`): dopo ogni scaricamento si eliminano le chiavi residue per lo stesso
`(pid, url)` di un tipo diverso e le pagine oltre l'ultima valida. Serve in due casi reali:

- si cambia `type` in `settings.json` (le voci del tipo precedente sparivano solo dopo un riavvio);
- un elenco paginato si accorcia (pag. 5 → 2: le voci delle pagine 3–5 restavano in libreria).

### Ritmo delle richieste

- Policy `catalog` esistente: `RateLimiter(0.5, 1)` + `Ledger` (contabilità come prima).
- In più, fra una pagina e la successiva: pausa casuale 200–800 ms (`catalog.PAGE_DELAY`, azzerata nei test).

---

## 3. `gamescraper/library/build.py`

Nuova funzione (mapping puro, firma richiesta):

```python
def _catalog_to_store_game(item: CatalogItem, console_pid: str) -> dict
```

Produce la voce "da scaricare" usata dalla libreria: `key` = `<pid>/net:<sha1(url)[:16]>`, `pid`, `name`,
`title` (via `clean_title`), `url`, `magnet`, `size`, `region`, `grp` = `<pid>/net:<titolo normalizzato>`,
più i campi vuoti che `build()` si aspetta (`year`, `genres`, `media`, `ratings`, …).

`_catalog_entries()` ora itera su `cfg.catalogs` e usa `cached_entries(...)`, quindi funziona con tutti e
tre i tipi senza duplicare logica; deduplica per nome file, esclude i giochi già posseduti e arricchisce
con LaunchBox come prima. La catena Minerva non è toccata.

---

## 4. `gamescraper/app/server.py`

- `GET /api/catalogs/{pid}`: per ogni URL riporta `url`, `games`, `time`, `type`, `total_pages`
  (via `cached_info`, quindi corretto anche per `type_a`/`type_b`).
- `POST /api/catalogs/{pid}`: cambiando solo gli URL **conserva** `type`/`css`; accetta `type` nel body
  per cambiarlo; con `urls: []` rimuove la console e le sue opzioni sia da `settings.json` sia dal config
  in memoria.

---

## 5. Test

Nuova cartella `tests/catalog/` — 16 test, eseguibili con `pytest` **e** come script (convenzione del repo).

| File | Copre |
|------|-------|
| `tests/catalog/test_type_a.py` | 2/3 voci, `total_pages=147`, URL/chiavi di cache, fetch su 2 pagine con deduplica, elenco accorciato 3→1 e potatura |
| `tests/catalog/test_type_b.py` | 4 voci con gruppi `#`,`A`,`A`,`B`, classi CSS configurabili, link non validi, indice unico |
| `tests/catalog/test_registry.py` | registro e default `filelist`, `parse_listing` invariato, chiave di cache storica invariata, cambio tipo con potatura, tipo sconosciuto rifiutato |
| `tests/catalog/test_storemeta.py` | copertina/descrizione da nxbrew e ziperto, segnaposto 200x200 scartato, Title ID, peso, generi, lingue, similarità e ricerca |
| `tests/catalog/test_gametdb.py` | indice dal mini-XML, sinossi IT preferita, copertine per regione, ricerca con titoli rumorosi, riuso dell'indice |
| `tests/catalog/test_build_store.py` | titoli online ripuliti, unione di descrizione/copertina/screenshot nella libreria, build con voce online |

```bat
set PYTHONUTF8=1
python -m pytest tests/catalog -q
:: oppure
python tests/catalog/test_type_a.py
python tests/catalog/test_type_b.py
python tests/catalog/test_registry.py
```

I test sostituiscono solo il confine HTTP (`catalog._get_page`): **nessuna chiamata di rete**.
Non si mocka `requests.get` perché il progetto usa `httpx` async, non `requests`.

---

## 6. Verifiche eseguite

| Verifica | Esito |
|----------|-------|
| `py_compile` su `catalog.py`, `config.py`, `library/build.py`, `app/server.py` | OK |
| `pytest tests/catalog` | 16 passed |
| Test come script (`test_type_a`, `test_type_b`, `test_registry`) | TUTTI OK |
| `tests/test_stores.py` (libreria, store, plugin, avvio giochi) | TUTTI OK |
| `tests/test_minerva.py` | TUTTI OK |
| `tests/test_policy.py` | TUTTI OK |
| Casi limite: 1 pagina → 1 richiesta; nomi ripetuti; pagina vuota; HTML senza struttura | OK |
| Casi limite: cambio tipo `type_a → type_b → filelist` senza residui in cache | OK |
| Casi limite: due console con lo stesso URL non si mescolano | OK |
| Integrazione `build()` con cache `type_a` + `type_b` (`online: 3`) | OK |
| Endpoint HTTP: conteggio/tipo/pagine, cambio URL, cambio tipo, rimozione | OK |

### Bug trovati durante la verifica (e corretti)

1. **Cache non potata**: cambiando `type`, o con un elenco paginato più corto, restavano in libreria le
   voci del tipo/pagine precedenti. Risolto con `_prune()` + test di regressione.
2. **Rimozione URL incompleta**: `POST /api/catalogs/{pid}` con `urls: []` lasciava
   `{"type": ..., "urls": []}` in `settings.json` e la console restava nel config in memoria. Risolto
   eliminando la voce e ricostruendo `catalogs`/`catalog_options` a ogni `apply_settings()`, con test
   di regressione.

---

## 7. Vincoli rispettati

- **`filelist` invariato**: `parse_listing()` non è stato modificato; il tipo resta il default e la chiave
  di cache storica è identica (coperto da test).
- **Minerva invariata**: nessuna modifica a `app/plugins/minerva/`.
- **Nessuna dipendenza nuova**: `beautifulsoup4` era già usata/installata; `requests` non viene usato
  (il progetto usa `httpx`). Se `bs4` manca, `type_a`/`type_b` danno un errore esplicito.
- **Niente Playwright**: le pagine indice descritte non lo richiedono.

---

## 8. Aggiungere un nuovo tipo in futuro

1. Scrivere `parse_type_x(html, base_url, exts=None, config=None) -> list[CatalogItem]`.
2. Registrarla in `PARSERS`.
3. Se serve paginazione, restituire `(items, total_pages)` e gestire il ramo in `fetch()`.

`fetch()`, la cache, la potatura, `build.py` e il server funzionano senza altre modifiche.

---

## 9. Copertine e descrizioni dalle pagine degli store

Le schede lette da `type_a`/`type_b` sono **pagine di gioco**, non file: per renderle "pulite" servono
copertina, descrizione e metadati che l'indice non contiene. Sono stati aggiunti due moduli e un job.

### `gamescraper/gametdb.py` — database offline GameTDB (Switch)

- Scarica una volta `switchtdb.zip` (≈3,9 MB) da gametdb.com e ne estrae `switchtdb.xml` (23 MB, ~24.000 giochi),
  valido 30 giorni; percorso alternativo con la variabile `GAMESCRAPER_GAMETDB`.
- Converte l'XML in un indice JSON compatto (`<cache>/gametdb/index.json`, ~18 MB, costruito in ~6 s e
  riusato a ogni avvio): `games` per ID e `titles` come mappa titolo normalizzato → ID.
- Per ogni gioco: titolo, sinossi (italiano → inglese → prima lingua), editore, sviluppatore, data,
  generi, giocatori e **copertine candidate** `art.gametdb.com/switch/coverHQ/<regione>/<ID>.jpg`
  (prima le regioni del gioco, poi US/EN/JA/FR/DE, poi la versione leggera `cover/`).
- `find(title)` confronta i titoli senza accenti, parentesi, versioni e parole dei siti ROM
  (`Switch NSP`, `XCI`, `eShop`, …), quindi «Zelda: TotK Switch NSP (eShop)» trova la scheda ufficiale.

### `gamescraper/storemeta.py` — lettura della pagina del gioco

Regole per sito (le stesse del progetto switch-library) con fallback generico:

| Sito | Copertina (in ordine) | Contenuto |
|------|------------------------|-----------|
| nxbrew.net | `og:image`, `figure.wp-block-media-text__media img`, `article .entry img`, `img.wp-post-image` | `.entry`, `article` |
| ziperto.com | `img.aligncenter`, `p[style*='text-align: center'] img`, `p strong img`, `img.wp-post-image` | `.entry-content`, `article` |
| altri | `og:image`, `img.aligncenter`, `img.wp-post-image`, `article img` | `.entry-content`, `article` |

- **Segnaposto** scartati (`200x200.png`, `placeholder`, logo, avatar, pixel).
- **Descrizione**: testo dopo `Description:` (nxbrew) oppure i primi paragrafi utili, saltando righe
  `Title:/Release Date:/Publisher:/…` e i link di download; in ultima istanza la `meta description`.
- **Metadati**: `Title ID`, `Release Date`, `Publisher`, `Category` → generi, `Title Size` → byte,
  `Language` → codici, `Version`; più le schermate dell'articolo (escluse copertina e segnaposto).
- **Ricerca per titolo** (`search_url`, `best_match`): usata su ziperto quando manca la copertina;
  somiglianza = parole in comune / massimo fra i due insiemi, soglia 0,6 (quindi «Silk» ≠ «Silk Road»).
- **Cache**: ogni pagina letta finisce in `netmeta:<url>` (60 giorni), con `source` = `gametdb` | `page` | `search`.

### Job «Copertine e descrizioni»

`POST /api/catalogs/{pid}/netmeta` (anche dal pulsante 🖼 nella finestra *Store online*):

1. **GameTDB offline** — riempie subito copertina/descrizione/giocatori/editore dei giochi Switch
   (nessuna richiesta HTTP); le scritture sono a blocchi di 250 per non bloccare l'interfaccia.
2. **Pagine dei siti** — solo per le voci ancora senza copertina o descrizione, con policy `netmeta`
   (2 richieste/s, 6 in volo, 403/404 gestiti) e ripresa: le pagine già lette non si rileggono.
3. **Ricerca su ziperto** — solo per le voci rimaste senza copertina.
4. Alla fine ricostruisce la libreria. `POST …/netmeta/stop` interrompe (il lavoro fatto resta in cache).

`solo schede di gioco`: il job ignora gli indici di file (`filelist`, es. Minerva) — i loro metadati
arrivano dallo scraper normale — e GameTDB viene usato solo per la console `switch`.

### Nella libreria

- Copertina: `media["cover"] = "url:<a>|<b>|<c>"` — il server prova gli indirizzi in ordine e salva la
  prima immagine che risponde (`/img/<id>/cover`), così le copertine GameTDB mancanti in una regione
  non lasciano spazi vuoti.
- Schermata: la prima immagine dell'articolo in `media["screenshot"]`.
- Descrizione, editore/sviluppatore, generi, giocatori, data e peso: riempiono solo i campi vuoti
  (LaunchBox, quando c'è, ha la precedenza).
- Titoli ripuliti: «Zumba World – The Marble Adventure Switch NSP (eShop)» → «Zumba World – The Marble
  Adventure». Il nome originale resta in `name`. «Switch» come parola vera non viene toccata
  («1-2-Switch», «Nintendo Switch Sports»).

### Risultati misurati sul catalogo reale (7.879 voci Switch)

| Passo | Esito |
|-------|-------|
| GameTDB | ~6.000 voci con copertina e metadati in pochi secondi (offline) |
| Pagine nxbrew | ~1,35 pagine/s; ogni scheda porta copertina + descrizione + Title ID, data, editore, peso |
| Copertine in libreria | ~6.900 su 7.654 voci online (89%) con il solo giro GameTDB |
| Descrizioni | progressive: crescono a ogni esecuzione del job (ripresa da dove si era fermato) |

---

### Stesso gioco da più fonti

Le voci sono raggruppate per **(console, titolo normalizzato)** dopo la pulizia del titolo: se lo stesso
gioco compare su NXBrew e su Ziperto (anche con nomi diversi, es. «Gioco Switch NSP» e «Gioco Switch NSP
(eShop)»), la libreria mostra **una sola scheda** con l'elenco delle fonti.

- In libreria i link stanno in `launch.sources` = `[{site, url, magnet, name}, …]`; `install_uri`/`page`
  puntano alla **fonte principale** (la prima trovata, o quella con un link magnet).
- Nella scheda del gioco compare un pulsante per ogni fonte («⬇ nxbrew.net», «⬇ ziperto.com») più il
  pulsante Torrent quando c'è un magnet; con una sola fonte resta il pulsante «⬇ Scarica (browser)».
- Metadati e copertina si prendono dalla prima fonte che li ha: se NXBrew non ha la descrizione, si usa
  quella letta dalla pagina di Ziperto.
- Per aggiungere Ziperto: nella finestra *Store online* incollare, oltre all'indice NXBrew,
  `https://www.ziperto.com/nintendo-switch-nsp/` (eventualmente anche `…/nintendo-switch-xci/`).

---

## 10. Store PC non ufficiali (repack / download diretto)

Nuova sezione in **Impostazioni › Store**, separata dagli store ufficiali (Steam, Epic, GOG…).

- `settings.json` → `pc_stores`: lista di `{"url": "https://…", "kind": "repack" | "direct"}`.
- L'elenco si legge con lo **stesso registro di parser** delle console (`filelist`, `type_a`, `type_b`,
  `type_c`, con rilevamento automatico del formato): si incolla l'indirizzo di una pagina elenco e basta.
- Le voci diventano **giochi PC** (piattaforma `windows`, `store="online"`), quindi compaiono nella
  libreria, nei filtri e nella scheda come gli altri giochi PC, con il pulsante di scaricamento.
- Categoria salvata in `launch.kind` e in ogni fonte (`launch.sources[].kind`):
  la scheda mostra «📦 Repack (download corto, installazione lunga)» oppure «⬇ Download diretto».
- Stesso gioco su più siti (repack e direct, o due repack) → **una scheda** con un pulsante per fonte,
  come per gli store delle console.
- Nella sezione c'è un **filtro** Tutti / 📦 Repack / ⬇ Download diretto sulla tabella degli elenchi,
  oltre ad aggiunta, cambio categoria e rimozione per riga; «↻ Leggi gli elenchi» avvia la lettura
  (job `PcStoreJob`) e ricostruisce la libreria.

Comandi/endpoint: `GET /api/pc-stores`, `POST /api/pc-stores`, `POST /api/pc-stores/refresh`.

> L'app **non contiene** un elenco precompilato di siti: gli indirizzi li aggiungi tu. Vale la stessa
> avvertenza della sezione console (scaricare solo ciò che si ha il diritto di scaricare).

### Port PC negli store

La tabella degli store mostra ora anche la riga **Port PC** (prima nascosta): numero di port installati e
pulsante **Gestisci** che apre la pagina dei port.

---

## 11. Stato del repository

Le modifiche sono **non committate** (scelta esplicita dell'utente). File toccati:

```
gamescraper/catalog.py        (registro parser, type_a/type_b, cache, potatura, rilevamento formato)
gamescraper/config.py         (catalog_options, forma estesa di catalogs)
gamescraper/gametdb.py        (nuovo: database offline GameTDB)
gamescraper/storemeta.py      (nuovo: copertina/descrizione dalle pagine)
gamescraper/library/build.py  (_catalog_to_store_game, titoli puliti, metadati nelle voci online)
gamescraper/app/server.py     (endpoint catalogs: tipo, pagine, rimozione, netmeta, copertine multiple)
gamescraper/app/jobs.py       (NetMetaJob)
gamescraper/policy.py         (policy «netmeta»: 2 richieste/s)
gamescraper/app/static/*      (pulsante «Copertine e descrizioni» con avanzamento e stop)
tests/catalog/                (32 test, nuovi)
changelog.md                  (voci «Aggiunto» e «Migliorato»)
```

Comandi utili:

```bat
python tools/version.py status
git add gamescraper/catalog.py gamescraper/config.py gamescraper/library/build.py ^
        gamescraper/app/server.py tests/catalog changelog.md
python tools/version.py commit --force
```
