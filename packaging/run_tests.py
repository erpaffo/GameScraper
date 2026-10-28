"""Esegue gli script di test uno per uno, con un limite di tempo per ognuno.

Serve alla CI: un test bloccato (per esempio una chiamata di rete che non risponde) faceva restare il
job appeso per sempre, senza dire dove. Qui ogni script e' un processo a se': se supera il limite gli si
chiede un dump dei thread (SIGABRT + faulthandler) e poi viene terminato, cosi' il log dice dove si e'
fermato. Uso: python packaging/run_tests.py [nome_test.py ...]
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# stessi script (e stesso ordine) del passo «Test» della release
TESTS = [
    "test_policy.py",
    "test_discover.py",
    "test_frontend.py",
    "test_pipeline.py",
    "test_stores.py",
    "test_launcher.py",
    "test_switchmeta.py",
    "test_wishlist.py",
    "test_frontends.py",
    "test_retroachievements.py",
    "test_controllers.py",
    "test_ports.py",
    "test_version.py",
    "test_sync.py",
    "test_dualscreen.py",
    "test_guests.py",
    "test_phone_extras.py",
    "test_guests.py",
    "test_extstore.py",
    "test_extensions_nuove.py",
]


def limit() -> float:
    try:
        return float(os.environ.get("GAMESCRAPER_TEST_TIMEOUT", "180"))
    except ValueError:
        return 180.0


def run(name: str, timeout: float) -> bool:
    path = ROOT / "tests" / name
    if not path.is_file():
        print(f"!! {name}: file mancante", flush=True)
        return False
    print(f"\n=== {name}", flush=True)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    start = time.time()
    proc = subprocess.Popen([sys.executable, "-X", "faulthandler", str(path)], cwd=ROOT, env=env)
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"!! {name}: fermo da piu' di {timeout:.0f} s, dump dei thread e interruzione", flush=True)
        if os.name == "posix":
            proc.send_signal(signal.SIGABRT)  # faulthandler stampa dove sono ferme le pile
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        return False
    took = time.time() - start
    print(f"--- {name}: {'ok' if code == 0 else 'FALLITO'} in {took:.1f} s", flush=True)
    return code == 0


def main(argv: list[str]) -> int:
    wanted = argv or TESTS
    timeout = limit()
    failed = [name for name in wanted if not run(name, timeout)]
    print(flush=True)
    if failed:
        print(f"TEST DA SISTEMARE ({len(failed)}): " + ", ".join(failed), flush=True)
        return 1
    print(f"TUTTI I TEST OK ({len(wanted)} script)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
