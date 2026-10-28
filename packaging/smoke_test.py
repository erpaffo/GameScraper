"""Prova di avvio (smoke test) dell'eseguibile compilato con PyInstaller.

Avvia l'eseguibile usando una cartella dati temporanea isolata, attende fino a 60 secondi
che il server locale risponda 200 su GET /api/status, poi chiude il processo (e gli eventuali
processi figli) e restituisce codice 0 in caso di successo o 1 in caso di errore.

Uso:
    python packaging/smoke_test.py dist/PozzoLauncher/PozzoLauncher.exe
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _resolve_exe(raw: str) -> Path:
    p = Path(raw).resolve()
    if p.is_dir() and p.suffix == ".app":
        return p / "Contents" / "MacOS" / p.stem
    return p


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            if sys.platform != "win32":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass


def _read_logs(tmp: Path, out_file: Path) -> str:
    parts = []
    for candidate in (out_file, tmp / "PozzoLauncher" / "logs" / "pozzo.log"):
        if candidate.is_file():
            try:
                txt = candidate.read_text(encoding="utf-8", errors="replace").strip()
                if txt:
                    parts.append(f"--- {candidate.name} ---\n{txt[-2000:]}")
            except OSError:
                pass
    return "\n".join(parts)


def run_smoke(exe_path: str, port: int = 8765, timeout: float = 60.0) -> int:
    exe = _resolve_exe(exe_path)
    if not exe.is_file():
        print(f"ERRORE: eseguibile non trovato: {exe}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        cfg_file = tmp / "config.toml"
        cfg_file.write_text('[paths]\ncache = "cache"\noutput = "output"\n', encoding="utf-8")

        env = os.environ.copy()
        env["POZZO_CONFIG"] = str(cfg_file)
        env["LOCALAPPDATA"] = str(tmp)
        env["APPDATA"] = str(tmp)
        env["XDG_DATA_HOME"] = str(tmp)
        env["BROWSER"] = "echo"
        if sys.platform != "win32":
            env["HOME"] = str(tmp)

        cmd = [sys.executable, str(exe)] if exe.suffix.lower() == ".py" else [str(exe)]
        out_path = tmp / "stdout.log"
        url = f"http://127.0.0.1:{port}/api/status"

        with open(out_path, "w", encoding="utf-8") as out_f:
            kwargs: dict = {"cwd": str(tmp), "env": env, "stdout": out_f, "stderr": subprocess.STDOUT}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(cmd, **kwargs)

        t0 = time.monotonic()
        last_err = ""
        try:
            while time.monotonic() - t0 < timeout:
                if proc.poll() is not None:
                    logs = _read_logs(tmp, out_path)
                    print(f"ERRORE: il processo e' terminato in anticipo (codice {proc.returncode}).\n{logs}",
                          file=sys.stderr)
                    return 1
                try:
                    with urllib.request.urlopen(url, timeout=2) as resp:
                        body = resp.read().decode("utf-8", errors="replace")
                        if resp.status == 200:
                            data = json.loads(body)
                            if isinstance(data, dict) and ("building" in data or "build_stats" in data or "has_library" in data):
                                elapsed = time.monotonic() - t0
                                print(f"OK: {exe.name} risponde su {url} in {elapsed:.1f}s")
                                return 0
                            last_err = f"risposta JSON inattesa: {body[:200]}"
                except (urllib.error.URLError, OSError, ValueError) as e:
                    last_err = f"{type(e).__name__}: {e}"
                time.sleep(0.5)

            logs = _read_logs(tmp, out_path)
            print(f"ERRORE: timeout ({timeout:.0f}s) in attesa di {url} ({last_err}).\n{logs}", file=sys.stderr)
            return 1
        finally:
            _kill_tree(proc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Smoke test di avvio per l'eseguibile PozzoLauncher")
    ap.add_argument("exe", help="Percorso dell'eseguibile (es. dist/PozzoLauncher/PozzoLauncher.exe)")
    ap.add_argument("--port", type=int, default=8765, help="Porta del server locale (default: 8765)")
    ap.add_argument("--timeout", type=float, default=60.0, help="Secondi massimi di attesa (default: 60)")
    args = ap.parse_args(argv)
    return run_smoke(args.exe, port=args.port, timeout=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())

