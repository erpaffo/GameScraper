"""Spegnimento o sospensione del PC al termine delle operazioni lunghe (scraping, download dei port).

Il conto alla rovescia e' nostro (default 60 s) cosi' si puo' sempre annullare dall'app o dal telefono.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

MODES = ("none", "sleep", "shutdown")
DELAY = 60  # secondi per annullare

_lock = threading.Lock()
_state: dict = {"mode": "", "at": 0.0, "timer": None}


def pending() -> dict:
    with _lock:
        left = max(0, round(_state["at"] - time.time())) if _state["mode"] else 0
        return {"mode": _state["mode"], "seconds": left}


def cancel() -> bool:
    with _lock:
        timer, was = _state["timer"], bool(_state["mode"])
        _state.update(mode="", at=0.0, timer=None)
    if timer:
        timer.cancel()
    return was


def schedule(mode: str, delay: int = DELAY) -> dict:
    """Programma `mode` ("sleep" | "shutdown"); un secondo invio sostituisce quello in attesa."""
    if mode not in ("sleep", "shutdown"):
        raise ValueError("Azione non valida")
    if sys.platform != "win32":
        raise ValueError("Spegnimento e sospensione sono disponibili solo su Windows")
    cancel()
    timer = threading.Timer(max(5, int(delay)), _do, args=(mode,))
    timer.daemon = True
    with _lock:
        _state.update(mode=mode, at=time.time() + max(5, int(delay)), timer=timer)
    timer.start()
    return pending()


def _do(mode: str):
    with _lock:
        if _state["mode"] != mode:  # annullato nel frattempo
            return
        _state.update(mode="", at=0.0, timer=None)
    try:
        if mode == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", "0"], check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            # shortcut: con l'ibernazione attiva Windows puo' ibernare invece di sospendere; se da' fastidio usare
            # "powercfg /hibernate off" o un profilo di alimentazione dedicato
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        pass
