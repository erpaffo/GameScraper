# Privacy

Pozzo Launcher non ha un server proprio, non ha account e **non raccoglie dati di utilizzo** (nessuna telemetria, nessuna statistica inviata).

## Dove stanno i tuoi dati

Tutto resta sul tuo PC, nella cartella dati (`%LOCALAPPDATA%\PozzoLauncher` su Windows, `~/Library/Application Support/PozzoLauncher` su macOS, `~/.local/share/PozzoLauncher` su Linux, oppure accanto all'eseguibile nella versione portatile): libreria, preferiti, tempo di gioco, note, impostazioni, copertine scaricate.

Chiavi delle fonti e accessi agli store stanno in `config.toml` e `settings.json` in quella cartella, **in chiaro**: proteggi la cartella come proteggeresti un file di password, e non condividerla.

Escono dal PC solo se lo scegli tu:

- **Sincronizzazione tra PC**: verso gli altri tuoi PC abbinati o la cartella condivisa che indichi.
- **Telefono**: i telefoni abbinati vedono la libreria sulla rete di casa; da fuori casa solo se imposti tu un indirizzo pubblico (tunnel).
- **Copie di sicurezza**: dove le salvi tu.

## Quali servizi contatta

L'app si collega a servizi esterni solo per le funzioni che usi. Alcuni controlli girano in sottofondo: aggiornamenti all'avvio, giochi gratis ogni 6 ore, port PC ogni 12 ore, prezzi della lista desideri.

| Funzione | Servizi |
|---|---|
| Dati e immagini dei giochi | libretro, IGDB (Twitch), ScreenScraper, TheGamesDB, LaunchBox, ArcadeDB, RAWG, Wikipedia, HowLongToBeat, GameTDB, recensioni Steam |
| Giochi PC | Steam, Epic Games, GOG, Amazon, Battle.net, Xbox, Minecraft: launcher installati e, dove serve, l'accesso con il tuo account |
| Obiettivi | RetroAchievements, con le tue credenziali |
| Giochi gratis e prezzi | allkeyshop (riquadro delle offerte gratuite), GamerPower, feed LootScraper, Epic Games Store, IsThereAnyDeal, GG.deals, Steam |
| Port e app | Quiver, GitHub, GitLab, Codeberg, Thunderstore |
| Store online | solo i siti che aggiungi tu |
| Aggiornamenti e store delle estensioni | GitHub |
| Discord | il client Discord sul PC (canale locale, nessun server) |

A questi servizi arrivano le normali informazioni di una richiesta web (indirizzo IP, titolo del gioco cercato) e, per le fonti con account, la tua chiave. Ognuno ha la propria informativa sulla privacy.

Le estensioni che installi possono contattare altri servizi: sono indicati nella loro descrizione (per esempio SteamGridDB, RAWG per il calendario delle uscite).
