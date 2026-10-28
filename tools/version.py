"""Versioni, changelog e commit «Version x.y.z» (mai push). Istruzioni in VERSIONING.md.

La sezione in cima a changelog.md e' la versione «in preparazione» finche' non e' su GitHub: ogni modifica va li'.
Il numero si calcola da solo dalla versione precedente: c'e' «### Aggiunto» -> +0.1.0, altrimenti +0.0.1;
--patch (+0.0.1), --minor (+0.1.0) o --major (+1.0.0) lo scelgono a mano. Le attivita' non finite stanno in «### In corso» con i loro file: il commit le lascia fuori.

  python tools/version.py add aggiunto|cambiato|migliorato|corretto|rimosso "testo"
  python tools/version.py wip "attivita'" [file ...]     # inizio di un lavoro lungo (i file restano fuori dai commit)
  python tools/version.py done "attivita'"               # lavoro finito: i suoi file rientrano nei commit
  python tools/version.py status
  python tools/version.py commit [--patch|--minor|--major] [--force]   # changelog + __init__ + git commit "Version x.y.z"
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "changelog.md"
INIT = ROOT / "pozzo" / "__init__.py"
LOCK = ROOT / ".git" / "version.lock"
KINDS = {"aggiunto": "Aggiunto", "cambiato": "Cambiato", "migliorato": "Migliorato", "corretto": "Corretto", "rimosso": "Rimosso"}
WIP = "In corso"
HEAD_RE = re.compile(r"^## (\d+)\.(\d+)\.(\d+)\b.*$|^## Non rilasciato\s*$", re.M)


def vt(s: str) -> tuple[int, int, int]:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3])  # type: ignore[return-value]


def vs(v) -> str:
    return "%d.%d.%d" % tuple(v)


def bump(v, level: str):
    return (v[0] + 1, 0, 0) if level == "major" else (v[0], v[1] + 1, 0) if level == "minor" else (v[0], v[1], v[2] + 1)


def git(*a, check=True) -> str:
    r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(a)}: {r.stderr.strip()}")
    return r.stdout.strip()


def pushed_version() -> tuple[int, int, int] | None:
    """Versione su GitHub (origin/main, come l'ultima volta che si e' fatto fetch)."""
    t = git("show", "origin/main:pozzo/__init__.py", check=False)
    m = re.search(r"__version__\s*=\s*[\"'](\d+\.\d+\.\d+)", t)
    return vt(m[1]) if m else None


# ------------------------------------------------------------------ changelog
def split(text: str):
    """(prima, [(intestazione, corpo)]): una voce per ogni sezione «## ...» di versione."""
    heads = list(HEAD_RE.finditer(text))
    if not heads:
        return text, []
    secs = [(m.group(0), text[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]) for i, m in enumerate(heads)]
    return text[:heads[0].start()], secs


def join(pre, secs) -> str:
    return pre + "".join(h + body for h, body in secs)


def subsections(body: str) -> dict[str, list[str]]:
    out, cur = {}, None
    for line in body.splitlines():
        m = re.match(r"^### (.+?)\s*$", line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
        elif cur and line.strip():
            out[cur].append(line)
    return out


def level_of(body: str, level: str | None = None) -> str:
    """level: scelto a mano ("patch", "minor", "major"); altrimenti dal changelog."""
    return level or ("minor" if subsections(body).get("Aggiunto") else "patch")


def pending(secs, pushed) -> int | None:
    """Indice della sezione in preparazione: «Non rilasciato» o una versione piu' alta di quella su GitHub."""
    if not secs:
        return None
    h = secs[0][0]
    if h.startswith("## Non rilasciato"):
        return 0
    return 0 if pushed is None or vt(h) > pushed else None


def ensure_pending(text: str, pushed) -> tuple[str, list, int]:
    pre, secs = split(text)
    i = pending(secs, pushed)
    if i is None:
        body = "\n\n"
        if secs:  # versione appena pubblicata: le attivita' ancora in corso passano alla prossima
            for t, files in wip_items(secs[0][1]):
                body = add_line(body, WIP, f"- {t}" + (" — " + " ".join(f"`{f}`" for f in files) if files else ""))
            secs[0] = (secs[0][0], drop_wip(secs[0][1]))
        secs.insert(0, ("## Non rilasciato", body.rstrip("\n") + "\n\n"))
        i = 0
    return pre, secs, i


def add_line(body: str, title: str, line: str) -> str:
    """Aggiunge una riga in fondo alla sottosezione (creandola se manca; «In corso» sempre per ultima)."""
    lines = body.split("\n")
    idx = next((k for k, x in enumerate(lines) if x.strip() == f"### {title}"), None)
    if idx is None:
        wip = next((k for k, x in enumerate(lines) if x.strip() == f"### {WIP}"), None)
        at = wip if wip is not None and title != WIP else len(lines)
        while at > 1 and not lines[at - 1].strip():  # lines[0] e' la fine della riga «## ...»
            at -= 1
        lines[at:at] = ["", f"### {title}", "", line]
        return "\n".join(lines)
    end = idx + 1
    while end < len(lines) and not lines[end].startswith("### "):
        end += 1
    while end > idx + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, line)
    return "\n".join(lines)


def wip_items(body: str) -> list[tuple[str, list[str]]]:
    """[(attivita', [file])] da righe «- attivita' — `file` `file`»."""
    out = []
    for line in subsections(body).get(WIP, []):
        m = re.match(r"^- (.+?)(?: — (.*))?$", line.strip())
        if m:
            out.append((m.group(1).strip(), re.findall(r"`([^`]+)`", m.group(2) or "")))
    return out


def drop_wip(body: str, task: str | None = None) -> str:
    """Toglie un'attivita' (None = tutte) e la sottosezione se resta vuota."""
    lines = body.split("\n")
    out, in_wip = [], False
    for x in lines:
        if x.startswith("### "):
            in_wip = x.strip() == f"### {WIP}"
        elif in_wip and x.strip().startswith("- ") and (
                task is None or re.match(rf"^- {re.escape(task)}(?: — .*)?$", x.strip())):
            continue
        out.append(x)
    body = "\n".join(out)
    if not wip_items(body):
        body = re.sub(rf"\n*### {WIP}\n+(?=###|\Z)", "\n\n", body)
    return body


# ------------------------------------------------------------------ comandi
class Lock:
    """Un solo agente alla volta modifica changelog e fa il commit."""

    def __enter__(self):
        for _ in range(100):
            try:
                self.fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.time() - LOCK.stat().st_mtime > 120:  # lasciato da un processo morto
                    LOCK.unlink(missing_ok=True)
                time.sleep(0.2)
        raise SystemExit("Un altro agente sta aggiornando il changelog: riprova tra poco")

    def __exit__(self, *a):
        os.close(self.fd)
        LOCK.unlink(missing_ok=True)


def read() -> str:
    return CHANGELOG.read_text(encoding="utf-8")


def write(text: str):
    CHANGELOG.write_text(text, encoding="utf-8")


def cmd_add(kind: str, text: str):
    title = KINDS.get(kind.lower())
    if not title:
        raise SystemExit(f"Tipo sconosciuto: {kind} (usa {', '.join(KINDS)})")
    with Lock():
        pre, secs, i = ensure_pending(read(), pushed_version())
        h, body = secs[i]
        secs[i] = (h, add_line(body, title, "- " + text.strip()))
        write(join(pre, secs))
    print(f"Aggiunto in «{title}»")


def cmd_wip(task: str, files: list[str]):
    with Lock():
        pre, secs, i = ensure_pending(read(), pushed_version())
        h, body = secs[i]
        body = drop_wip(body, task)
        line = f"- {task}" + (" — " + " ".join(f"`{f}`" for f in files) if files else "")
        secs[i] = (h, add_line(body, WIP, line))
        write(join(pre, secs))
    print(f"In corso: {task}" + (f" ({len(files)} file fuori dai commit)" if files else ""))


def cmd_done(task: str):
    with Lock():
        pre, secs, i = ensure_pending(read(), pushed_version())
        h, body = secs[i]
        if task not in [t for t, _ in wip_items(body)]:
            raise SystemExit(f"Nessuna attività in corso chiamata «{task}»")
        secs[i] = (h, drop_wip(body, task))
        write(join(pre, secs))
    print(f"Finito: {task}")


def plan(text: str, pushed, level: str | None = None):
    """(versione nuova, versione precedente, livello, attivita' in corso)."""
    pre, secs = split(text)
    i = pending(secs, pushed)
    if i is None:
        return None
    prev = next((vt(h) for h, _ in secs[i + 1:] if not h.startswith("## Non rilasciato")), None)
    base = max(x for x in (prev, pushed, (0, 0, 0)) if x is not None)
    if level is None and not secs[i][0].startswith("## Non rilasciato"):
        # numero gia' scelto da un commit non ancora pubblicato: resta quello (lo cambia solo --patch/--minor/--major)
        keep = vt(secs[i][0])
        return keep, base, "major" if keep[0] > base[0] else "minor" if keep[1] > base[1] else "patch", wip_items(secs[i][1])
    lvl = level_of(secs[i][1], level)
    return bump(base, lvl), base, lvl, wip_items(secs[i][1])


def cmd_status():
    p = plan(read(), pushed_version())
    print(f"Su GitHub: {vs(pushed_version()) if pushed_version() else '?'}  ·  nel codice: {current_init()}")
    if not p:
        return print("Nessuna modifica in preparazione nel changelog")
    new, base, lvl, wip = p
    print(f"In preparazione: {vs(new)} ({lvl}, da {vs(base)})")
    for t, files in wip:
        print(f"  in corso: {t}" + (f"  [{', '.join(files)}]" if files else ""))


def current_init() -> str:
    m = re.search(r"__version__\s*=\s*[\"']([^\"']+)", INIT.read_text(encoding="utf-8"))
    return m[1] if m else "?"


def cmd_commit(level: str | None = None, force=False):
    with Lock():
        text = read()
        pushed = pushed_version()
        p = plan(text, pushed, level)
        if not p:
            raise SystemExit("Niente da rilasciare: aggiungi prima le modifiche al changelog (tools/version.py add ...)")
        new, base, lvl, wip = p
        if wip and not force:
            raise SystemExit("Attività non finite: " + "; ".join(t for t, _ in wip)
                             + "\nFinisci (done) o usa --force: i loro file restano fuori dal commit.")
        pre, secs = split(text)
        secs[0] = (f"## {vs(new)} ({time.strftime('%Y-%m-%d')})", secs[0][1])
        write(join(pre, secs))
        INIT.write_text(re.sub(r"__version__\s*=\s*[\"'][^\"']+[\"']", f'__version__ = "{vs(new)}"',
                               INIT.read_text(encoding="utf-8")), encoding="utf-8")
        git("add", "-A")
        held = sorted({f for _, files in wip for f in files})
        if held:
            git("reset", "-q", "--", *held)
        if not git("diff", "--cached", "--name-only"):
            raise SystemExit("Niente da committare")
        git("commit", "-q", "-m", f"Version {vs(new)}")
    print(f"Commit «Version {vs(new)}» ({lvl}, da {vs(base)})" + (f"; lasciati fuori: {', '.join(held)}" if held else "")
          + ". Per pubblicare: git push")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        return print(__doc__)
    c, a = argv[0], argv[1:]
    flags = {x for x in a if x.startswith("--")}
    a = [x for x in a if not x.startswith("--")]
    if c == "add" and len(a) == 2:
        cmd_add(*a)
    elif c == "wip" and a:
        cmd_wip(a[0], a[1:])
    elif c == "done" and len(a) == 1:
        cmd_done(a[0])
    elif c == "status":
        cmd_status()
    elif c == "commit":
        cmd_commit(next((lv for lv in ("major", "minor", "patch") if f"--{lv}" in flags), None), "--force" in flags)
    else:
        print(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
