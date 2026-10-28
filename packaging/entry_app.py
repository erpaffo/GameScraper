"""Punto d'ingresso dell'eseguibile con finestra (PozzoLauncher / PozzoLauncher.app).

Senza argomenti avvia l'app; con argomenti si comporta come la riga di comando.
Senza terminale (Windows/macOS) l'output va in un file di log nella cartella dei dati dell'utente.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # l'hashing usa processi separati
    if sys.stdout is None or sys.stderr is None:
        from pozzo.config import app_data_dir
        logs = app_data_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        f = open(logs / "pozzo.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    # macOS passa "-psn_..." quando l'app viene aperta dal Finder
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    from pozzo.cli import main
    main(args or ["app"])
