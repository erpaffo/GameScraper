# La libreria

La finestra principale ha tre parti: la **barra laterale** a sinistra (viste, console, store, collezioni), la
**barra degli strumenti** in alto (ricerca, ordinamento, filtri) e la **griglia** delle copertine. Un clic su una
copertina apre il **dettaglio** del gioco sulla destra.

La libreria arriva al programma in un'unica volta: ricerca, filtri e ordinamenti sono calcolati in memoria e sono
istantanei anche con decine di migliaia di giochi. Nella griglia esistono solo le copertine visibili, servite come
miniature WebP leggere.

## Le viste della barra laterale

| Vista | Cosa mostra |
|---|---|
| **✨ Per te** | Pagina di consigli in stile Netflix (vedi [Per te](#per-te-e-a-cosa-vuoi-giocare)) |
| **▦ Tutti i giochi** | Tutta la libreria: ROM e giochi PC |
| **★ Preferiti** | I giochi segnati con la stella |
| **🛒 Store** | Giochi da installare o scaricare: posseduti negli store ma non installati, e giochi degli store online |
| **👥 Multigiocatore locale** | Giochi con almeno 2 giocatori sullo stesso schermo |
| **🎁 Giochi gratis** | Offerte gratuite del momento (vedi [Giochi gratis](extra.md#giochi-gratis)) |
| **🕹 Port PC** | Giochi console ricompilati per PC (vedi [Port PC](extra.md#port-pc)) |
| **♡ Lista desideri** | Giochi che vuoi comprare, con prezzi e avvisi (vedi [Lista desideri](extra.md#lista-desideri)) |
| **📷 Schermate** | Le schermate fatte durante le partite |
| **🏆 Migliori giochi** | Classifiche Top 100 e Top 1000 (vedi [Migliori giochi](#migliori-giochi)) |
| **📊 Statistiche** | Tempo di gioco per console e store, giochi più giocati, completezza dei dati |
| **◷ Giocati di recente** | In ordine di ultima partita |
| **? Senza dati** | Giochi per cui nessuna fonte ha trovato informazioni |
| **⧉ Duplicati** | Le versioni secondarie dei giochi (altre regioni, revisioni) |
| **⊘ Nascosti** | I giochi che hai nascosto |

Sotto le viste ci sono le **console** (con il numero di giochi), la sezione **PC** con i singoli store, le
**collezioni** e le **collezioni intelligenti**. Gli stati dei giochi (Da giocare, In corso, Finito, Abbandonato)
sono viste anch'essi.

## Cercare, ordinare, raggruppare

- **Ricerca** (tasto `/`): cerca nel titolo, nello sviluppatore, nella console e nello store. Più parole devono
  esserci tutte; accenti e maiuscole non contano.
- **Ordinamento**: Titolo A–Z, Anno ↑/↓, Console, Ultimi giocati, Tempo di gioco, Valutazione, Dimensione ↑/↓,
  Durata ↑/↓ (HowLongToBeat), Store.
- **Raggruppamento**: nessuno, per console, per anno o per lettera, con un'intestazione per ogni gruppo.
- **Filtro installati** (nelle viste di console e store): *Tutti*, *Solo posseduti / installati*,
  *Solo da scaricare / installare*.
- **Nascondi duplicati**: mostra una sola versione per gioco (attivo di default).
- **Dimensione delle copertine**: il cursore accanto ai filtri.
- **🎲 Gioco a caso**: sceglie un gioco tra quelli mostrati (filtri e ricerca compresi), preferendo quelli avviabili
  subito.

### Scopri

Il pannello **Scopri** filtra e ordina la vista corrente per trovare cosa giocare:

| Filtro | Criterio |
|---|---|
| 🏆 Migliori per la critica | voto Metacritic (RAWG) o della critica (IGDB) almeno 75 |
| ❤️ Più amati dal pubblico | voto dei giocatori almeno 75% con almeno 20 voti |
| ⭐ Capolavori | critica almeno 90 oppure pubblico almeno 85% con almeno 50 voti |
| 🔥 Più votati | ordinati per numero di voti dei giocatori |
| 💎 Gemme nascoste | voto alto (almeno 80%) ma pochi voti (da 5 a 200) |
| 🕹 Grandi classici | usciti prima del 2000 con voto almeno 75% |
| 🆕 Usciti negli ultimi 3 anni | |
| 👥 Multigiocatore in locale | almeno 2 giocatori sullo stesso schermo |
| ⏱ Brevi / 🏔 Lunghi | storia principale entro 5 ore / di almeno 40 ore (HowLongToBeat) |
| ⏳ Mai giocati | nessuna partita registrata |
| 👎 Peggio valutati | voto sotto il 50% con almeno 10 voti |
| ❔ Senza valutazione | |

Si combina con un **genere** e un **decennio**. Quando un filtro Scopri decide l'ordine, l'ordinamento normale si
disattiva.

## Il dettaglio di un gioco

Il pannello di dettaglio mostra copertina, sfondo, descrizione, sviluppatore, editore, generi, anno, giocatori,
valutazioni (pubblico e critica), durata HowLongToBeat, peso, fonti dei dati e, se servono, i dati mancanti.

Azioni principali:

- **▶ Gioca** (o `Invio`, o doppio clic sulla copertina) avvia il gioco. Per un gioco PC non installato c'è
  **⬇ Installa**, per un gioco degli store online i pulsanti di download di ogni fonte.
- **★ Preferito**, **Stato** (📋 Da giocare, ▶ In corso, ✔ Finito, ✖ Abbandonato), **Nascondi**.
- **Nota personale** e **voto da 1 a 5 stelle**: valgono per tutte le versioni del gioco.
- **Collezioni**: aggiungi o togli il gioco da una collezione.
- **Versioni**: le altre versioni dello stesso gioco (regioni, revisioni, altri store); puoi scegliere quale usare
  come principale.
- **Mancano: …** con **⟳ Cerca dati mancanti**: riprova le fonti solo per questo gioco e le sue versioni.
- Sezioni aggiuntive secondo il gioco: salvataggi, RetroAchievements, aggiornamenti e DLC Switch, port PC
  disponibili. Sono descritte nei rispettivi capitoli.

### Menu del tasto destro

Un clic destro su una copertina apre un menu rapido: Gioca, Dettagli, Mostra nella cartella, preferiti, aggiungi a
una collezione, stato, nascondi. **Rimuovi da…** toglie il gioco dalla vista corrente (per esempio da
«Giocati di recente», che azzera solo l'ultima partita lasciando tempo di gioco e numero di avvii) o dalle altre
categorie a cui appartiene.

## Una versione per gioco

Le versioni regionali e le revisioni dello stesso gioco sono **raggruppate** in una sola scheda: Rubino, Ruby, Rubin
e Rubis sono un unico gioco. Il raggruppamento usa il codice prodotto Nintendo (per esempio AGB-**AXV**J), l'identità
IGDB e ScreenScraper. Viene mostrata la versione nella tua lingua, secondo `languages` e `regions` della
configurazione; le altre restano nel dettaglio.

Preferiti, tempo di gioco, stato e collezioni valgono per **tutto il gioco**, cioè per tutte le sue versioni. Lo
stesso vale per un gioco PC presente su più store (per esempio Steam ed Epic): è una scheda sola con più versioni, e
la principale è quella installata.

## Collezioni

### Collezioni automatiche

Raccolgono i giochi della stessa saga su tutte le console (Pokémon, Kingdom Hearts, Tomb Raider…). Sono generate dai
franchise di libretro e IGDB più un'euristica sul titolo. Una collezione nasce con almeno 3 giochi
(`[collections] min_games`); `exclude` toglie dei franchise e `aliases` unisce franchise con nomi diversi (vedi
[Riferimento](riferimento.md)).

Puoi **rinominarle**, **nasconderle**, **togliere o aggiungere giochi**. Le modifiche restano anche dopo ogni
ricostruzione della libreria. Le collezioni hanno icona e copertina scaricate da Wikipedia
(**Impostazioni › Fonti › Immagini delle collezioni**).

### Collezioni personali

Si creano con **＋** accanto a *Collezioni* e funzionano come le automatiche, ma le riempi tu.

### Collezioni intelligenti

Con **＋⚡** accanto a *Intelligenti* crei una collezione che si aggiorna da sola in base a regole:

- console e store (nessuna = tutte), generi (almeno uno), stato;
- valutazione minima (%) e voto minimo della critica (/100);
- giocatori in locale (almeno), anno da/fino a;
- durata minima e massima in ore (HowLongToBeat);
- solo giochi pronti da avviare (ROM presente o gioco installato).

## Per te e «A cosa vuoi giocare?»

La vista **✨ Per te** è una pagina di scaffali:

- **▶ Riprendi**: i giochi con stato *In corso*; **Giocati di recente**;
- **📋 Dalla tua lista da giocare**, i più brevi prima;
- **Consigliati per te** e **Perché hai giocato …**: giochi simili a quelli che hai giocato di più;
- **Il meglio di** un genere; **🧩 Continua le tue saghe**, **📚 Collezioni che potrebbero piacerti**,
  **🏆 Le saghe meglio valutate**, **🗂 Le collezioni più grandi**.

Il pulsante **🎲 A cosa vuoi giocare?** fa poche domande (quanto tempo hai, con quante persone giochi, che cosa
cerchi, generi, epoca, dove vuoi giocare, se vuoi qualcosa di nuovo) e propone una dozzina di giochi adatti. Le
risposte sui generi aiutano anche i consigli futuri.

## Migliori giochi

**🏆 Migliori giochi** mostra la classifica globale **Top 100 / Top 1000**; la pagina di ogni console ha
**🏆 Top 100** per quella console. Le classifiche comprendono sia i giochi posseduti sia quelli scaricabili dagli
store online.

- Entrano solo i giochi con **voti affidabili**: almeno 2 fonti, oppure un voto della critica, oppure 50 voti.
- Il punteggio unisce critica (Metacritic/IGDB) e giocatori.
- Nella classifica globale lo stesso gioco su più console conta una volta, preferendo la versione posseduta.
- **↻ Aggiorna voti online** chiede a IGDB e RAWG i voti dei candidati.

## Valutazioni

Il voto mostrato combina tutte le fonti pesandole per **numero di voti** (media bayesiana): 4,7 con 1.800 voti conta
più di 5,0 con 2 voti. Il voto della **critica** (Metacritic o IGDB) è mostrato a parte.

## Scorciatoie

| Tasto | Azione |
|---|---|
| `/` | Cerca |
| `Esc` | Chiude il menu, il dettaglio o la ricerca |
| `Invio` | Avvia il gioco aperto nel dettaglio |
| Doppio clic su una copertina | Avvia il gioco |
