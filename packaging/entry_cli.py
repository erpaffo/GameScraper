"""Punto d'ingresso dell'eseguibile da terminale (gamescraper-cli): stessi comandi di `python -m gamescraper`."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from gamescraper.cli import main
    main()
