"""Plugin di esempio (backend): un endpoint che calcola le statistiche dalla libreria e dai dati utente.

Disponibile su /plugin-api/statistiche/riepilogo
"""

import sqlite3
from collections import defaultdict
from pathlib import Path


def register(gs):
    cache = Path(gs.cfg.cache)

    @gs.route("riepilogo")
    def riepilogo():
        lib = sqlite3.connect(cache / "library.db")
        from pozzo.sync import profile_path  # statistiche del profilo utente attivo
        user = sqlite3.connect(profile_path(gs.cfg, gs.cfg.app.get("profile") or "default"))
        try:
            games = {k: (p, t, s, grp) for k, p, t, s, grp in
                     lib.execute("SELECT key, platform, title, store, grp FROM games")}
            primary = {k for (k,) in lib.execute("SELECT key FROM games WHERE is_primary=1")}
            per_platform = defaultdict(lambda: {"giochi": 0, "tempo": 0.0, "partite": 0})
            for k, (p, _, s, _) in games.items():
                if k in primary:
                    per_platform[s or p]["giochi"] += 1
            top = []
            for key, playtime, plays, last in user.execute(
                    "SELECT key, playtime, play_count, last_played FROM user_games WHERE play_count > 0"):
                if key not in games:
                    continue
                p, title, s, _ = games[key]
                per_platform[s or p]["tempo"] += playtime or 0
                per_platform[s or p]["partite"] += plays or 0
                top.append({"titolo": title, "piattaforma": s or p, "tempo": playtime or 0, "partite": plays, "ultima": last})
            top.sort(key=lambda x: -x["tempo"])
            found = lib.execute("SELECT COUNT(*) FROM games WHERE is_primary=1 AND found=1").fetchone()[0]
            return {"per_piattaforma": per_platform, "piu_giocati": top[:15], "giochi": len(primary),
                    "con_dati": found, "tempo_totale": sum(v["tempo"] for v in per_platform.values())}
        finally:
            lib.close()
            user.close()

    gs.on("game_stopped", lambda info: gs.log(f"[statistiche] sessione di {info.get('title')}: {info.get('played', 0):.0f} s"))
