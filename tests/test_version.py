"""tools/version.py in un repository git di prova: changelog, numero calcolato, attivita' in corso, commit.

Esegui: python tests/test_version.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "tools" / "version.py"


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        t = Path(t)
        remote, repo = t / "remote.git", t / "repo"
        g = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout  # noqa: E731
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        repo.mkdir()
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        (repo / "tools").mkdir()
        shutil.copy(SRC, repo / "tools" / "version.py")
        (repo / "gamescraper").mkdir()
        (repo / "gamescraper" / "__init__.py").write_text('__version__ = "0.2.1"\n')
        (repo / "changelog.md").write_text("# Changelog\n\nIntro.\n\n## 0.2.1 (2026-01-01)\n\n### Corretto\n\n- vecchio\n")
        g("add", "-A")
        g("commit", "-q", "-m", "Version 0.2.1")
        g("remote", "add", "origin", str(remote))
        g("push", "-q", "-u", "origin", "main")

        def v(*a, ok=True):
            r = subprocess.run([sys.executable, "tools/version.py", *a], cwd=repo, capture_output=True, text=True, encoding="utf-8")
            assert (r.returncode == 0) == ok, (a, r.stdout, r.stderr)
            return r.stdout + r.stderr

        cl = lambda: (repo / "changelog.md").read_text(encoding="utf-8")  # noqa: E731
        v("add", "corretto", "un bug")
        assert "## Non rilasciato\n\n### Corretto\n\n- un bug\n" in cl(), cl()
        assert "In preparazione: 0.2.2 (patch" in v("status")
        v("add", "aggiunto", "una funzione")
        assert "In preparazione: 0.3.0 (minor" in v("status")
        # lavoro lungo non finito: il commit si rifiuta, con --force lascia fuori i suoi file
        (repo / "port.py").write_text("in corso\n")
        (repo / "ok.py").write_text("finito\n")
        v("wip", "Port PC", "port.py")
        assert "Port PC" in v("commit", ok=False)
        assert "lasciati fuori: port.py" in v("commit", "--force")
        assert g("log", "-1", "--format=%s") == "Version 0.3.0\n"
        files = g("show", "--name-only", "--format=", "HEAD").split()
        assert "ok.py" in files and "port.py" not in files and "gamescraper/__init__.py" in files, files
        assert '"0.3.0"' in (repo / "gamescraper" / "__init__.py").read_text()
        assert cl().index("## 0.3.0 (") < cl().index("### Aggiunto") < cl().index("### In corso") < cl().index("## 0.2.1"), cl()
        # prima del push le modifiche finiscono nella stessa versione
        v("add", "corretto", "altro bug")
        assert "In preparazione: 0.3.0" in v("status")
        v("commit", "--force")
        assert g("log", "-1", "--format=%s") == "Version 0.3.0\n" and cl().count("## 0.3.0") == 1
        # dopo il push: nuova sezione, l'attivita' in corso passa li'; finita, i suoi file entrano nel commit
        g("push", "-q")
        v("add", "migliorato", "piu' veloce")
        assert cl().index("## Non rilasciato") < cl().index("- Port PC") < cl().index("## 0.3.0"), cl()
        v("done", "Port PC")
        assert "Port PC" not in cl() and "### In corso" not in cl(), cl()
        v("add", "aggiunto", "Port PC")
        assert "Version 0.4.0" in v("commit")
        assert "port.py" in g("show", "--name-only", "--format=", "HEAD").split()
        assert "Niente da committare" in v("commit", ok=False)
        g("push", "-q")
        assert "Niente da rilasciare" in v("commit", ok=False)
        v("add", "rimosso", "roba")
        assert "0.4.1" in v("status") and "1.0.0" in v("commit", "--major")
    print("ok  versioni: numero dal changelog, stessa versione fino al push, attività in corso fuori dal commit")
    print("test_version: OK")


if __name__ == "__main__":
    main()
