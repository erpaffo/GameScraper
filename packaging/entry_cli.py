"""Punto d'ingresso dell'eseguibile da terminale (pozzo-cli): stessi comandi di `python -m pozzo`."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from pozzo.cli import main
    main()
