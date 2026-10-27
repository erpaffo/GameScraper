# Giochi gratis, lista desideri, port PC

## Giochi gratis

La vista **🎁 Giochi gratis** raccoglie le offerte gratuite del momento: giochi da tenere, inclusi in abbonamenti,
weekend gratuiti e DLC gratis.

| Fonte | Cosa dà |
|---|---|
| allkeyshop.com (riquadro *Daily game deals*) | Fonte principale: giochi gratis su tutti gli store, con tipo di offerta |
| LootScraper (feed pubblico) | Prime Gaming anche senza allkeyshop: link diretto al riscatto, copertina, scadenza, valore |
| Ricerca di Steam | Giochi normalmente a pagamento scontati del 100% (esclusi free to play, DLC, colonne sonore) |
| GamerPower | Giochi in regalo con data di fine |

Nella pagina:

- l'etichetta **Nuovo** e il numero 🆕 nella barra laterale indicano le offerte comparse dall'ultima visita;
- **✓ Preso** segna un'offerta già riscattata;
- l'ordine mette prima le offerte ancora da prendere, per scadenza più vicina;
- **♡** aggiunge il gioco alla lista desideri.

Le richieste sono poche: il risultato resta in cache 3 ore (12 ore per allkeyshop e Prime Gaming) e dopo un errore di
rete la fonte viene lasciata in pace per 3 ore. Se allkeyshop non risponde (blocca l'indirizzo dopo poche richieste)
si tiene l'ultimo elenco buono. Le copertine remote hanno tempi di attesa brevi, così un sito lento non blocca il
resto dell'app.

## Lista desideri

La vista **♡ Lista desideri** segue i prezzi dei giochi che vuoi comprare e avvisa quando conviene.

### Aggiungere giochi

- cerca un gioco nel campo *Cerca un gioco da aggiungere…*;
- **⬇ Importa da Steam** importa la tua lista desideri di Steam;
- **♡** nella pagina dei giochi gratis.

Un gioco che hai già in libreria è segnato con *già in libreria*.

### Prezzi

| Prezzi | Fonte | Cosa serve |
|---|---|---|
| Store ufficiali (Steam, GOG, Epic, Humble…) | IsThereAnyDeal | Chiave gratuita (⚙ della lista); senza chiave, il prezzo di Steam per i giochi con codice Steam |
| Negozi di key | GG.deals | Chiave gratuita (⚙ della lista); solo giochi PC con codice Steam |

Per ogni gioco la lista mostra il prezzo attuale, lo sconto, il **minimo storico** e, con GG.deals, il prezzo più
basso dei negozi di key con lo sconto rispetto al prezzo pieno ufficiale (per esempio 59,99 € su Steam → 18,50 € =
−69%). **🔑 ↗** apre il confronto dei negozi di key, **Apri ↗** la pagina dello store.

I prezzi si controllano al massimo due volte al giorno, oppure subito con **↻ Controlla prezzi**. Nelle impostazioni
della lista (⚙) si scelgono anche il **paese** e lo **sconto minimo** per gli avvisi.

### Avvisi e storico

Un avviso (🔔) nasce quando:

- lo sconto supera la soglia scelta;
- il prezzo, ufficiale **o** dei negozi di key, scende sotto quello indicato in *Avvisami quando costa meno di…*;
- il prezzo tocca il **minimo storico** da quando segui il gioco.

Ogni gioco ha uno **storico dei prezzi** (un punto al giorno, store e negozi di key) con un piccolo grafico.

## Port PC

La vista **🕹 Port PC** elenca i giochi console **ricompilati o ricostruiti per Windows** (Zelda64Recomp, Ship of
Harkinian, Banjo Recompiled…) dal catalogo di Quiver Launcher: circa 580 port, aggiornato ogni 24 ore.

### Trovare un port

Filtri per console, ricerca, **🎮 Ho la ROM** (port di giochi che hai già in libreria), **Installati**,
**Da aggiornare**. La scheda di una ROM mostra **Disponibile come port PC** quando ne esiste uno.

### Installare e avviare

- **Installa** scarica l'ultima release da GitHub, GitLab o Codeberg (il file Windows viene scelto da solo) nella
  cartella `Ports\<cartella>`, con `portable.txt` dove il port lo prevede.
- Il port diventa un **gioco PC**: tempo di gioco, Big Picture e avvio dal telefono funzionano come per gli altri.
- Se il port ha più eseguibili si sceglie quale avviare, e la scelta resta ricordata.
- **Disinstalla** sposta la cartella nel Cestino.
- **Aggiungi a mano**: da un repository GitHub (`utente/progetto`) o da una cartella già scaricata.

### ROM richiesta

Molti port richiedono la ROM originale. Per i port che indicano l'hash richiesto, **Copia dalla libreria** mette la
ROM giusta nella cartella del port dopo averla controllata.

### Aggiornamenti

Il controllo degli aggiornamenti avviene al massimo ogni 12 ore. Prima di aggiornare si fa una copia di sicurezza in
`Ports\.backup`; salvataggi, configurazione e mod (i file della cartella che non vengono dalla release) sono
conservati. **Salta questa versione** ignora un aggiornamento. Un token GitHub facoltativo (⚙ nella pagina) alza il
limite di richieste a GitHub.

### Mod e salvataggi

- **Mod** da Thunderstore: installa e rimuovi dalla pagina del port. GameBanana è disponibile solo come link.
- I **salvataggi** del port entrano nelle copie dei salvataggi per gioco (vedi [Giocare](giocare.md#salvataggi)).
