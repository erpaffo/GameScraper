import sys

from .cli import main

if __name__ == "__main__":
    # eseguibile aperto con un doppio clic (nessun argomento): si avvia l'app, non lo scraping
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        sys.argv.append("app")
    if getattr(sys, "frozen", False):
        import multiprocessing
        multiprocessing.freeze_support()  # hashing in processi separati anche nell'eseguibile
    main()
