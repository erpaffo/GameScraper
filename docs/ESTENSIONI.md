# Temi e plugin di GameScraper

GameScraper si personalizza in due modi:

| | Per chi | Cosa cambia | Linguaggio |
|---|---|---|---|
| **Tema** | Chiunque | Colori, caratteri, forme, sfondi, layout | Solo CSS |
| **Plugin** | Chi programma | Nuove funzioni: pulsanti, pagine, store, fonti di dati, reazioni agli eventi | JavaScript e/o Python |

Le cartelle si trovano accanto a `config.toml`:

```
GameScraper/
  config.toml
  themes/<id-tema>/
  plugins/<id-plugin>/
```

Temi e plugin si attivano da **Impostazioni › Aspetto**. Dopo aver aggiunto o abilitato un plugin, riavvia l'app.

---

## Temi

```
themes/mio-tema/
  theme.json
  theme.css          # vista normale
  bigpicture.css     # facoltativo: modalità Big Picture (se manca, usa "desktop")
```

**theme.json**
```json
{
  "name": "Il mio tema",
  "description": "Verde scuro",
  "author": "Io",
  "preview": "#0f1f14",
  "desktop": "theme.css",
  "bigpicture": "bigpicture.css"
}
```

**theme.css**: di solito basta ridefinire le variabili.
```css
:root {
  --bg: #0b140e;        /* sfondo */
  --bg-2: #102016;      /* barra laterale, campi */
  --bg-3: #183024;      /* elementi in rilievo */
  --panel: #0f1d14;     /* pannello dettaglio, finestre */
  --line: #24442f;      /* bordi */
  --text: #e9f5ec;
  --muted: #8fb39a;
  --accent: #3ddc84;    /* colore principale (selezione, pulsanti) */
  --accent-2: #00b894;  /* secondo colore dei gradienti */
  --fav: #ffd54a;
  --radius: 14px;       /* arrotondamento delle copertine */
}
```

Puoi anche cambiare qualsiasi elemento: `.tile`, `.cover`, `#sidebar`, `#detail`, `.btn.primary`… Nella Big Picture: `.card`, `.shelf`, `.bp-hero`, `.bp-btn`… Per i font esterni si può usare `@import` da Google Fonts.

Sulla pagina sono disponibili due attributi utili:

| Attributo | Valori |
|---|---|
| `<html data-mode>` | `desktop` / `bigpicture` |
| `<html data-theme>` | id del tema attivo |

I temi inclusi (`gamescraper/app/themes/`: OLED, Chiaro, Retrò, Notte blu) sono buoni esempi da copiare.

---

## Plugin

```
plugins/mio-plugin/
  plugin.toml      # obbligatorio
  frontend.js      # facoltativo: interfaccia
  style.css        # facoltativo
  backend.py       # facoltativo: Python, lato server
```

**plugin.toml**
```toml
[plugin]
name = "Il mio plugin"
version = "1.0"
author = "Io"
description = "Cosa fa"
api = 1
```

### Interfaccia (frontend.js)

È un modulo JavaScript che esporta una funzione. Riceve `gs`, l'API dell'app, che funziona sia nella vista normale sia nella Big Picture:

```js
export default function (gs, ctx) {
  // pulsante nel dettaglio di ogni gioco
  gs.addGameAction({
    id: "wiki", label: "Wiki", icon: "📖",
    when: (game) => !game.store,          // facoltativo: solo per alcuni giochi
    run: (game) => gs.api("/api/open", { body: { url: "https://it.wikipedia.org/wiki/" + encodeURIComponent(game.title) } }),
  });

  // sezione nel dettaglio (HTML o Element, anche asincrona)
  gs.addDetailSection({ id: "note", title: "Le mie note", render: async (game) => `<p>${gs.esc(game.title)}</p>` });

  // etichetta sulle copertine
  gs.addTileBadge({ id: "retro", render: (g) => (g.year && g.year < 1990 ? "RETRÒ" : null) });

  // voce nella barra laterale che apre una pagina
  gs.addSidebarItem({ id: "mia", label: "La mia pagina", icon: "✨", view: "mia-pagina" });
  gs.registerView("mia-pagina", { title: "La mia pagina", render: (el) => { el.innerHTML = "<h2>Ciao!</h2>"; } });

  // scheda nelle impostazioni
  gs.addSettingsTab({ id: "mio", label: "Mio plugin", render: (el) => { el.innerHTML = "…"; } });

  // eventi dell'interfaccia
  gs.on("gameOpened", (game) => console.log("aperto", game.title));
}
```

**Eventi dell'interfaccia**:
- `libraryLoaded`: libreria caricata o ricaricata;
- `viewChanged`: cambio di vista;
- `gameOpened`: dettaglio di un gioco aperto;
- `uiChanged`: un plugin ha aggiunto elementi;
- `extensionsLoaded`: temi e plugin caricati.

**Funzioni utili**:

| Funzione | Cosa fa |
|---|---|
| `gs.api(path, {body, method})` | Chiama le API dell'app o del tuo backend |
| `gs.library()` | Libreria in memoria (`games`, `plats`, `colls`, …) |
| `gs.openGame(id)` | Apre il dettaglio di un gioco |
| `gs.currentGame()` | Gioco aperto in questo momento |
| `gs.toast(msg, errore)` | Mostra una notifica |
| `gs.esc(testo)` | Rende sicuro un testo da inserire nell'HTML |
| `gs.mode` | `"desktop"` o `"bigpicture"` |

`ctx.settings` contiene le impostazioni salvate dal backend del plugin.

### Server (backend.py)

```python
def register(gs):
    # endpoint: GET /plugin-api/<id-plugin>/ciao
    @gs.route("ciao")
    def ciao():
        return {"messaggio": "ciao", "impostazioni": gs.settings}

    # eventi: game_started, game_stopped, library_built, scrape_finished, stores_refreshed
    gs.on("game_stopped", lambda info: gs.log(f"{info['title']}: {info.get('played', 0):.0f} secondi"))

    # impostazioni persistenti (in settings.json)
    gs.save_settings({"ultimo_avvio": "oggi"})
```

`gs` mette a disposizione:

| Membro | Cosa fa |
|---|---|
| `gs.route(path, methods=("GET",))` | Espone una funzione su `/plugin-api/<id>/<path>` (FastAPI) |
| `gs.add_store(ClasseStore)` | Aggiunge un **fornitore di libreria**, cioè uno store nuovo: sottoclasse di `gamescraper.stores.base.StoreProvider`, con un metodo `fetch()` che restituisce `StoreGame` (installati o da installare, con `launch` e `install_uri`) |
| `gs.add_source(ClasseFonte)` | Aggiunge una **fonte di metadati** per lo scraping: sottoclasse di `gamescraper.sources.base.Source`, con `lookup(game)` |
| `gs.on(evento, funzione)` | Reagisce agli eventi del server |
| `gs.cfg` | Configurazione (sola lettura) |
| `gs.settings`, `gs.save_settings(dict)` | Impostazioni del plugin |
| `gs.log(msg)` | Scrive nel log |

### Esempio completo

Il plugin incluso **Statistiche** (`gamescraper/app/plugins/statistiche/`) usa tutte le parti:
- un backend con un endpoint e un evento;
- un frontend con una pagina, una voce nella barra laterale e un'azione nel dettaglio;
- un foglio di stile.

### Plugin inclusi

| Plugin | Cosa fa |
|---|---|
| **Statistiche** | tempo di gioco per console e store, giochi più giocati, completezza dei metadati |
| **Link → Store** | legge una pagina che raccoglie altri siti, raggruppa i link per intestazione (h2/h3) e aggiunge ciascuno come store di una console o come store PC (repack / download diretto) |
| **Store Minerva + Switch** | importa in un clic le cartelle di minerva-archive.org elencate su r-roms.github.io **e** aggiunge gli store Switch preimpostati (NXBrew `game-index`, Ziperto NSP, Ziperto XCI) |

**Store Minerva + Switch** tiene i preset in `PRESETS` (`backend.py`): per aggiungerne altri basta una riga
(`{"console": ["https://…"]}`). Poiché gli elenchi della stessa console possono avere formati diversi
(`type_b`, `type_c`, `type_d`), quando una console ha più di un indirizzo il `type` fisso viene rimosso:
ogni pagina viene riconosciuta da sola da `catalog.detect_kind`.

**Link → Store** è anche un esempio di plugin che **scrive nelle impostazioni**: usa le stesse funzioni
dell'interfaccia (`config.set_setting`) e quindi le modifiche valgono subito, senza riavviare l'app.
La logica di lettura della pagina sta in `gamescraper/linklist.py` (`scan_page`, `add_store`), fuori dal
plugin, così è testabile senza avviare il server.

### Sicurezza

Un plugin con `backend.py` gira con gli stessi permessi dell'app: può leggere e scrivere file e avviare programmi. Installa solo plugin di cui ti fidi e di cui puoi leggere il codice.
