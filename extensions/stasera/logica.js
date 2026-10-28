// Scelta del gioco: funzioni pure (niente DOM), cosi' si provano da sole con node.

const DAY = 86400;

/** Giochi tra cui scegliere: una versione per gioco, posseduti, non nascosti, avviabili, ancora da giocare. */
export function candidates(games, f = {}) {
  const ok = new Set(f.statuses || ["backlog", ""]);
  return games.filter((g) => {
    if (g.id !== g.grp.primary || g.grp.hidden || g.store === "online") return false;
    if (g.store && !g.installed) return false;                       // gioco di uno store non installato: non si avvia
    if (!ok.has(g.grp.status || "")) return false;                   // di base: backlog o senza stato (non finiti/abbandonati)
    if (f.platform === "pc" ? !g.store : f.platform && (g.store || g.platform !== f.platform)) return false;
    if (f.genre && !String(g.genres || "").split(",").map((x) => x.trim()).includes(f.genre)) return false;
    if (f.hours) {                                                     // «finibile in una sera»: serve la durata nota
      if (g.hltb == null) return !!f.unknown;
      if (g.hltb > f.hours) return false;
    }
    return true;
  });
}

/** Peso: 1 per un gioco giocato oggi, fino a ~25 per uno mai avviato o fermo da due anni. */
export function weight(g, now = Date.now() / 1000) {
  const last = g.grp.last || 0;
  const idle = last ? Math.min(730, Math.max(0, (now - last) / DAY)) : 730;
  return 1 + idle / 30;
}

/** Estrae un gioco a caso tenendo conto dei pesi; `skip` = id gia' proposti. Null se non resta niente. */
export function pick(list, skip = new Set(), rnd = Math.random, now = Date.now() / 1000) {
  const pool = list.filter((g) => !skip.has(g.id));
  if (!pool.length) return null;
  const w = pool.map((g) => weight(g, now));
  let r = rnd() * w.reduce((a, b) => a + b, 0);
  for (let i = 0; i < pool.length; i++) { r -= w[i]; if (r < 0) return pool[i]; }
  return pool[pool.length - 1];
}

/** Generi presenti tra i candidati, dal piu' frequente. */
export function genresOf(games) {
  const n = new Map();
  for (const g of games) for (const x of String(g.genres || "").split(",").map((s) => s.trim()).filter(Boolean)) n.set(x, (n.get(x) || 0) + 1);
  return [...n].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k]) => k);
}
