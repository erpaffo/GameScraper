# Contribuire

Grazie dell'interesse! Segnalazioni, idee e pull request sono benvenute.

- **Problemi e idee**: apri una [segnalazione](https://github.com/erpaffo/Pozzo-Launcher/issues/new/choose) con il modello adatto.
- **Sicurezza**: mai in pubblico, vedi [SECURITY.md](SECURITY.md).
- **Domande d'uso**: prima il [manuale](https://erpaffo.github.io/Pozzo-Launcher/).

## Preparare l'ambiente

Serve Python 3.12 (lo stesso della CI e delle release).

```bash
python -m pip install -r requirements.txt
python -m pozzo app        # avvia l'app dai sorgenti
```

I sorgenti usano la stessa cartella dati dell'eseguibile installato. Per non toccare la tua libreria mentre sviluppi, usa una configurazione separata: `POZZO_CONFIG=/percorso/prova/config.toml`.

## Test

```bash
PYTHONUTF8=1 python packaging/run_tests.py          # tutta la suite, come la CI
PYTHONUTF8=1 python tests/test_pipeline.py          # un solo file
```

I test sono script con `assert`: niente framework da imparare. La CI li esegue su Windows, macOS e Linux a ogni push e pull request. Per il JavaScript basta `node --check <file>.js`.

## Stile

- Il codice segue lo stile dei file vicini: nomi, commenti (in italiano, brevi, sul *perché*) e struttura.
- Una modifica piccola e completa è meglio di una grande: aggiorna anche test, manuale (`docs/`) e chiamanti che tocchi.
- Niente dipendenze nuove per poche righe.

## Changelog e versioni

Le voci del changelog si aggiungono con lo script, non a mano:

```bash
python tools/version.py add aggiunto "Cosa cambia per chi usa l'app"   # aggiunto, cambiato, migliorato, corretto, rimosso
python tools/version.py status
```

Numero di versione, commit di versione e release li gestisce il manutentore.

## Estensioni

Le estensioni stanno in `extensions/<id>/` e si installano dallo store dentro l'app. Guida: [Temi e plugin](https://erpaffo.github.io/Pozzo-Launcher/ESTENSIONI/). Dopo averne modificata una:

```bash
python tools/extensions.py index    # rigenera extensions/index.json (il test lo controlla)
python tools/extensions.py install  # la copia nella tua cartella plugin per provarla (da rifare dopo ogni modifica)
```

## Licenza dei contributi

Inviando una pull request accetti che il tuo contributo sia distribuito con la stessa licenza del progetto (vedi `LICENSE`).
