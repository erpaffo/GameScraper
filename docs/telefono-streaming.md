# Telefono e streaming

Il telefono (o il tablet) può fare tre cose con GameScraper:

1. **telecomando della libreria**: sfogliare i giochi, avviarli e chiuderli sul PC;
2. **controller**: tasti a schermo, giroscopio, fino a 4 telefoni per 4 giocatori;
3. **schermo**: giocare sul telefono, sulla TV o su un altro PC con lo streaming Sunshine + Moonlight.

Tutto si configura in **Impostazioni › 📱 Telefono e streaming**.

## L'app per il telefono

### Attivarla e abbinare il telefono

1. **Attiva l'accesso dal telefono**. L'app pubblica sulla rete di casa una web app in HTTPS.
2. Inquadra il **QR** con il telefono. Il codice di abbinamento ha 6 cifre, vale 10 minuti e si usa una volta sola.
3. Aggiungi la pagina alla **schermata Home** del telefono: si apre come un'app.

La prima apertura mostra l'avviso «connessione non privata»: è il certificato del tuo PC, si accetta con
**Avanzate › Procedi**. Windows può chiedere di consentire l'app sulle reti private.

### Sicurezza

Senza abbinamento il telefono non raggiunge niente. Anche abbinato può solo sfogliare la libreria, avviare e chiudere
i giochi, fare schermate e fare da controller: le impostazioni e i file del PC restano fuori portata.

### Cosa si fa dal telefono

- Home con *In corso*, *Giocati di recente*, *Preferiti* e giochi PC installati; ricerca e filtro per console.
- Scheda del gioco con **Avvia**, stato, preferito e **Togli da Giocati di recente**.
- **Schermate** del gioco in corso.
- **📱 Gioca su questo telefono**: avvia il gioco sul PC e spiega come entrarci da Moonlight con un tocco, tramite
  l'app **▶ Gioco in corso** pubblicata in Sunshine. Aprire Moonlight direttamente su un gioco da una pagina web non è
  possibile (né su iOS né su Android).

## Il telefono come controller

Il controller sullo schermo diventa un **controller Xbox virtuale** sul PC: serve il driver **ViGEmBus**. Fino a 4
telefoni = 4 giocatori.

### Aspetti del controller

In ⚙ › **Aspetto del controller**:

| Aspetto | Per cosa |
|---|---|
| **Xbox** | Il layout predefinito |
| **PlayStation** | Croce in alto a sinistra, levette in basso, △ ○ ✕ □ con i loro colori, L1/L2/R1/R2 |
| **Nintendo Switch** | Pro Controller: X in alto, A a destra, B in basso, Y a sinistra |
| **GameCube** | A grande, C-stick giallo, tasti per lettera |
| **Arcade** | 6 tasti, per i picchiaduro |
| **Buzz!** | Pulsante rosso grande e 4 colori, telefono in verticale; un telefono per giocatore |
| **Wii Remote** | In verticale o in orizzontale (come un NES), anche con **Nunchuk**: tasti, levetta, inclinazione, scuotimento e puntatore arrivano a Dolphin |
| **Volante** | Si sterza inclinando il telefono, pedali a pressione, *Centra* |
| **Chitarra** | Guitar Hero / Rock Band: 5 tasti colorati, plettro, leva del vibrato, Star Power |
| **Pistola ottica / touchpad** | Il telefono muove il mouse del PC (GunCon, Zapper, pennino del DS). Va permesso sul PC |
| **Microfono** | SingStar: la voce va in un cavo audio virtuale del PC (per esempio VB-Cable) che PCSX2 usa come microfono |

Con il layout Switch i tasti mandano al PC la stessa **posizione** di un controller Xbox (valgono le impostazioni che
l'emulatore ha già per i controller veri); un'opzione fa mandare invece la **lettera** (A Nintendo = A Xbox).

**Aspetto automatico**: con un gioco PlayStation in corso il telefono passa da solo al layout PlayStation, con un gioco
Switch al layout Switch, e lo stesso per GameCube, Wii e per i giochi Buzz!, Guitar Hero/Rock Band e SingStar.
Si disattiva in ⚙.

### Tasti personalizzabili

⚙ › **Sposta e ridimensiona i tasti**: si trascina ogni gruppo (levette, croce, tasti azione, dorsali, tasti
centrali) e se ne cambia la dimensione. La disposizione si salva per ogni aspetto, in percentuale dello schermo;
**Ripristina** torna a quella originale.

### Giroscopio

- **Movimento**: il protocollo DSU (cemuhook) su `127.0.0.1:26760` per Cemu, Ryujinx, Dolphin, Azahar/Citra.
  Nell'emulatore scegli la sorgente di movimento DSU.
- **🎯 Mira**: l'inclinazione muove la levetta destra, per i giochi senza giroscopio.

### Vibrazione

Quando il gioco fa vibrare il controller virtuale, vibra il telefono, con impulsi più lunghi per vibrazioni più forti.
Funziona su Android; Safari su iPhone e iPad non permette la vibrazione alle pagine web.

### Controller Bluetooth collegato al telefono

Se al telefono o al tablet è collegato un controller Bluetooth (PS5, PS4, Xbox, Switch Pro, MFi), l'app lo usa come
controller del PC al posto dei tasti a schermo. Il pulsante 🎮 sceglie *Auto*, *A schermo* o *Il mio*.

### Sala giocatori

Ogni telefono ha un **numero e un colore di giocatore**, visibili sul telefono e sul PC. I telefoni si scambiano il
posto senza ricollegarsi. Il numero è quello che vedono giochi ed emulatori (indice XInput).

### Configurazione automatica degli emulatori

Alcuni controller speciali richiedono impostazioni nell'emulatore. L'app le scrive da sola:

- **PCSX2** per Buzz! o per la chitarra;
- **Dolphin** per il Wii Remote: server DSU, profili «GameScraper Telefono N», Wii Remote 1…N.

I file originali vengono copiati prima; **Annulla** rimette le impostazioni dell'emulatore com'erano.

## Streaming con Sunshine e Moonlight

Per giocare **lontano dal PC** (telefono, tablet, TV, un altro PC) si usano
[Sunshine](https://app.lizardbyte.dev/Sunshine), sul PC, e [Moonlight](https://moonlight-stream.org), sul
dispositivo che guarda.

### Pubblicare i giochi in Moonlight

**Pubblica in Moonlight** aggiunge a Sunshine l'app «GameScraper Big Picture» e i tuoi giochi principali con le
copertine; le altre app di Sunshine restano. Moonlight avvia `gamescraper-cli play --key …`, che resta aperto finché
giochi: lo streaming si chiude da solo a fine partita.

### Sunshine sempre pronto

Se Sunshine è fermo o impostato su avvio manuale, Moonlight non trova il PC. **▶ Avvia Sunshine** lo avvia e lo
imposta per partire con Windows, con una sola richiesta dei permessi di amministratore. La scheda avvisa se è in
avvio manuale e la pagina **Salute** segnala quando è fermo.

### Ottimizza lo streaming

**⚡ Ottimizza lo streaming** controlla:

- Sunshine avviato e configurato per partire con Windows;
- il preset dell'encoder NVIDIA, il two-pass e la correzione degli errori;
- se il PC è collegato via cavo o in Wi-Fi.

Per ogni problema dà le istruzioni per correggerlo, più i consigli per TV, box e telefono: Modalità Gioco della TV,
cavo di rete, frame pacing, bitrate, statistiche di Moonlight, e un box dedicato (Nvidia Shield, Apple TV 4K,
Google TV Streamer) al posto dell'app della TV, che decodifica molto più in fretta.

### Come giocare: TV, telefono, iPad

- **TV**: il controller si collega in Bluetooth alla TV (o al box); gli input arrivano al PC come controller Xbox
  virtuale tramite Moonlight → Sunshine → ViGEmBus.
- **Telefono o iPad**: tasti a schermo di Moonlight o un controller Bluetooth.
- **Telefono come controller** di un gioco mostrato sulla TV o sul monitor: con l'app del telefono descritta sopra.
