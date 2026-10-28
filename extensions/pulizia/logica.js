// Pulizia libreria: funzioni pure (niente DOM), cosi' si provano da sole con node.

const DAY = 86400;
const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** Una versione per gioco, posseduto e visibile. */
export function owned(games) {
  return games.filter((g) => g.id === g.grp.primary && !g.grp.hidden && g.store !== "online");
}

/** Gruppi di giochi con lo stesso titolo (senza maiuscole e punteggiatura) sulla stessa console: [[g, g, ...], ...]. */
export function duplicates(games) {
  const by = new Map();
  for (const g of owned(games)) {
    const k = `${g.store || g.platform}|${fold(g.title)}`;
    if (!fold(g.title)) continue;
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(g);
  }
  return [...by.values()].filter((l) => l.length > 1);
}

/** Mai avviati: nessuna partita, nessun tempo di gioco (nemmeno quello registrato dallo store). */
export function neverPlayed(games) {
  return owned(games).filter((g) => !g.grp.last && !g.grp.playtime && !g.splay && g.grp.status !== "done");
}

/** Fermi da `years` anni: giocati almeno una volta ma non da allora. */
export function stale(games, years, now = Date.now() / 1000) {
  return owned(games).filter((g) => g.grp.last && g.grp.last < now - years * 365 * DAY && g.grp.status !== "done")
    .sort((a, b) => a.grp.last - b.grp.last);
}

/** Che dati mancano: copertina, anno, genere, trovato dalle fonti. */
export function missing(g) {
  const out = [];
  if (!g.found) out.push("non trovato");
  if (!g.cover) out.push("copertina");
  if (!g.year) out.push("anno");
  if (!g.genres) out.push("genere");
  return out;
}

export function incomplete(games) {
  return owned(games).map((g) => [g, missing(g)]).filter(([, m]) => m.length).map(([g, m]) => ({ g, m }));
}
