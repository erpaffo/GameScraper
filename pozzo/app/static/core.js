"use strict";
/* Pozzo Launcher - nucleo condiviso tra interfaccia desktop e Big Picture.
 *
 * Espone window.GS: utilita', chiamate API, decodifica della libreria e il MODELLO DI ESTENSIONI.
 * I plugin (plugins/<id>/frontend.js) ricevono GS e si agganciano a punti stabili invece di
 * modificare la pagina, cosi' sopravvivono agli aggiornamenti dell'interfaccia:
 *
 *   export default function (gs) {
 *     gs.addGameAction({ id: "hltb", label: "Quanto dura?", run: (game) => ... });
 *     gs.addDetailSection({ id: "note", title: "Note", render: async (game) => "<p>...</p>" });
 *     gs.addTileBadge({ id: "new", render: (g) => g.year >= 2024 ? "NUOVO" : null });
 *     gs.addSidebarItem({ id: "stats", label: "Statistiche", icon: "📊", view: "stats" });
 *     gs.registerView("stats", { title: "Statistiche", render: async (el) => { el.innerHTML = "..." } });
 *     gs.on("gameStarted", (info) => ...);
 *   }
 */

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const collator = new Intl.Collator("it", { sensitivity: "base", numeric: true });
const store = {
  get(k, d) { try { const v = localStorage.getItem("gs." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("gs." + k, JSON.stringify(v)); } catch { /* storage non disponibile */ } },
};
const SHORT = { megadrive: "MD", mastersystem: "SMS", gamegear: "GG", psx: "PS1", ps2: "PS2", ps3: "PS3", psp: "PSP",
  psvita: "VITA", gamecube: "GC", gc: "GC", wiiu: "WII U", switch: "SWITCH", xbox360: "X360", dreamcast: "DC",
  saturn: "SAT", segacd: "MCD", sega32x: "32X", pcengine: "PCE", nds: "DS", "3ds": "3DS", n64: "N64", windows: "PC" };
const STORE_SHORT = { steam: "STEAM", epic: "EPIC", gog: "GOG", amazon: "AMAZON", battlenet: "BATTLE.NET", riot: "RIOT", xbox: "XBOX",
  minecraft: "MINECRAFT", ports: "APP", manual: "PC" };
const STORE_ICON = { steam: "◉", epic: "◆", gog: "◎", amazon: "◗", battlenet: "✦", riot: "✪", xbox: "⬢", minecraft: "▣", manual: "⌂" };

function fmtSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n < 10 ? n.toFixed(2) : n.toFixed(1)).replace(".", ",") + " " + u[i];
}

/* ---- Store: giochi installabili da tutti gli store importati (posseduti su Steam/Epic/GOG/… non
 * installati + giochi delle pagine "Store online" delle console). Esclusi i giochi gia' presenti in
 * un'altra versione installata/sul disco. */
const GB = 1024 ** 3;
const SHOP_SIZES = [
  { id: "", label: "Qualsiasi dimensione", ok: () => true },
  { id: "s", label: "Meno di 1 GB", ok: (g) => g.size > 0 && g.size < GB },
  { id: "m", label: "1 – 10 GB", ok: (g) => g.size >= GB && g.size < 10 * GB },
  { id: "l", label: "10 – 50 GB", ok: (g) => g.size >= 10 * GB && g.size < 50 * GB },
  { id: "xl", label: "Oltre 50 GB", ok: (g) => g.size >= 50 * GB },
  { id: "u", label: "Dimensione sconosciuta", ok: (g) => !g.size },
];
const SHOP_SORTS = [
  { id: "title", label: "Titolo A–Z", cmp: (a, b) => collator.compare(a.sort, b.sort) },
  { id: "-rating", label: "Valutazione", cmp: (a, b) => (b.rating ?? -1) - (a.rating ?? -1) || collator.compare(a.sort, b.sort) },
  { id: "-year", label: "Più recenti", cmp: (a, b) => (b.year || 0) - (a.year || 0) || collator.compare(a.sort, b.sort) },
  { id: "year", label: "Più vecchi", cmp: (a, b) => (a.year || 9999) - (b.year || 9999) || collator.compare(a.sort, b.sort) },
  { id: "size", label: "Più leggeri", cmp: (a, b) => (a.size || Infinity) - (b.size || Infinity) || collator.compare(a.sort, b.sort) },
  { id: "-size", label: "Più pesanti", cmp: (a, b) => (b.size || 0) - (a.size || 0) || collator.compare(a.sort, b.sort) },
  { id: "store", label: "Store", cmp: (a, b) => collator.compare(shopSource(a), shopSource(b)) || collator.compare(a.sort, b.sort) },
];
const shopSource = (g) => (g.store === "online" ? "online:" + g.platform : g.store);

function shopGames(games) {
  const owned = new Set(games.filter((g) => g.installed).map((g) => g.gi));
  return games.filter((g) => g.store && g.store !== "manual" && !g.installed && !owned.has(g.gi) && !g.grp.hidden);
}

/** Origini presenti (per il filtro): store PC e pagine online delle console. */
function shopSources(items, stores, platName) {
  const n = new Map();
  for (const g of items) n.set(shopSource(g), (n.get(shopSource(g)) || 0) + 1);
  const pc = stores.filter((s) => n.has(s.id)).map((s) => ({ id: s.id, label: s.name, n: n.get(s.id) }));
  const on = [...n.keys()].filter((k) => k.startsWith("online:"))
    .map((k) => ({ id: k, label: `Online · ${platName(k.slice(7))}`, n: n.get(k) }))
    .sort((a, b) => collator.compare(a.label, b.label));
  return pc.concat(on);
}

function shopFilter(items, f) {
  const size = SHOP_SIZES.find((s) => s.id === (f.size || "")) || SHOP_SIZES[0];
  let it = items.filter((g) => g.content !== "update" && (!f.hideDlc || g.content !== "dlc")
    && (!f.src || shopSource(g) === f.src) && (!f.plat || g.platform === f.plat) && size.ok(g));
  // una sola copia per gioco (la principale se rispetta i filtri)
  const one = new Map();
  for (const g of it) if (!one.has(g.gi) || g.id === g.grp.primary) one.set(g.gi, g);
  it = [...one.values()];
  if (f.q) {
    const toks = fold(f.q).split(/\s+/).filter(Boolean);
    it = it.filter((g) => toks.every((t) => g.q.includes(t)));
  }
  return it.sort((SHOP_SORTS.find((s) => s.id === f.sort) || SHOP_SORTS[0]).cmp);
}

/* ---- Scopri: filtri "intelligenti" su valutazioni, voti, anni, giocatori (validi per ogni vista) */
const THIS_YEAR = new Date().getFullYear();
const byScore = (f) => (a, b) => (f(b) ?? -1) - (f(a) ?? -1) || collator.compare(a.sort, b.sort);
const best = (g) => Math.max(g.critics ?? 0, g.votes >= 20 ? g.rating ?? 0 : 0);
const PRESETS = [
  { id: "", label: "Tutti i giochi" },
  { id: "critics", label: "🏆 Migliori per la critica", hint: "voto Metacritic (RAWG) o della critica (IGDB) almeno 75",
    ok: (g) => g.critics >= 0.75, cmp: byScore((g) => g.critics), score: "critics" },
  { id: "loved", label: "❤️ Più amati dal pubblico", hint: "voto dei giocatori almeno 75% con almeno 20 voti",
    ok: (g) => g.rating >= 0.75 && g.votes >= 20, cmp: byScore((g) => g.rating), score: "rating" },
  { id: "top", label: "⭐ Capolavori", hint: "critica almeno 90 oppure pubblico almeno 85% con almeno 50 voti",
    ok: (g) => g.critics >= 0.9 || (g.rating >= 0.85 && g.votes >= 50), cmp: byScore(best), score: "best" },
  { id: "popular", label: "🔥 Più votati", hint: "ordinati per numero di voti dei giocatori",
    ok: (g) => g.votes > 0, cmp: byScore((g) => g.votes), score: "votes" },
  { id: "gems", label: "💎 Gemme nascoste", hint: "voto alto (almeno 80%) ma pochi voti (da 5 a 200)",
    ok: (g) => g.rating >= 0.8 && g.votes >= 5 && g.votes < 200, cmp: byScore((g) => g.rating), score: "rating" },
  { id: "classics", label: "🕹 Grandi classici", hint: "usciti prima del 2000 con voto almeno 75%",
    ok: (g) => g.year && g.year < 2000 && Math.max(g.rating ?? 0, g.critics ?? 0) >= 0.75, cmp: byScore((g) => Math.max(g.rating ?? 0, g.critics ?? 0)), score: "best" },
  { id: "new", label: "🆕 Usciti negli ultimi 3 anni", ok: (g) => g.year >= THIS_YEAR - 3,
    cmp: (a, b) => (b.year || 0) - (a.year || 0) || collator.compare(a.sort, b.sort) },
  { id: "multi", label: "👥 Multigiocatore in locale", hint: "almeno 2 giocatori sullo stesso schermo", ok: (g) => g.players >= 2,
    cmp: byScore((g) => g.rating) },
  { id: "short", label: "⏱ Brevi (meno di 5 ore)", hint: "storia principale entro 5 ore (HowLongToBeat)",
    ok: (g) => g.hltb != null && g.hltb <= 5, cmp: (a, b) => a.hltb - b.hltb || collator.compare(a.sort, b.sort), score: "hltb" },
  { id: "long", label: "🏔 Lunghi (oltre 40 ore)", hint: "storia principale di almeno 40 ore (HowLongToBeat)",
    ok: (g) => g.hltb >= 40, cmp: (a, b) => b.hltb - a.hltb || collator.compare(a.sort, b.sort), score: "hltb" },
  { id: "unplayed", label: "⏳ Mai giocati", ok: (g) => !g.grp.last && !g.grp.playtime, cmp: byScore((g) => g.rating) },
  { id: "worst", label: "👎 Peggio valutati", hint: "voto sotto il 50% con almeno 10 voti",
    ok: (g) => g.rating != null && g.rating < 0.5 && g.votes >= 10, cmp: (a, b) => a.rating - b.rating || collator.compare(a.sort, b.sort), score: "rating" },
  { id: "unrated", label: "❔ Senza valutazione", ok: (g) => g.rating == null && g.critics == null },
];

/** Applica preset, genere e decennio; restituisce null come ordine se il preset non ne impone uno. */
function discover(items, f = {}) {
  const p = PRESETS.find((x) => x.id === (f.preset || "")) || PRESETS[0];
  let it = p.ok ? items.filter(p.ok) : items;
  if (f.genre) it = it.filter((g) => fold(g.genres).split(/\s*,\s*/).includes(f.genre));
  if (f.decade) it = it.filter((g) => g.year && Math.floor(g.year / 10) * 10 === +f.decade);
  return { items: it, cmp: p.cmp || null, preset: p };
}

/** Generi piu' comuni (normalizzati), per il filtro. */
function genreList(games, max = 40) {
  const n = new Map(), label = new Map();
  for (const g of games) {
    for (const x of String(g.genres || "").split(/\s*,\s*/)) {
      if (!x) continue;
      const k = fold(x);
      n.set(k, (n.get(k) || 0) + 1);
      if (!label.has(k)) label.set(k, x);
    }
  }
  return [...n.entries()].filter(([, c]) => c >= 3).sort((a, b) => b[1] - a[1]).slice(0, max)
    .map(([k, c]) => ({ id: k, label: label.get(k), n: c })).sort((a, b) => collator.compare(a.label, b.label));
}

function decadeList(games) {
  const s = new Set(games.filter((g) => g.year > 1950).map((g) => Math.floor(g.year / 10) * 10));
  return [...s].sort();
}

/** Testo del punteggio da mostrare sotto la copertina per il preset attivo. */
function scoreText(g, kind) {
  const pct = (v) => Math.round(v * 100);
  if (kind === "critics" && g.critics != null) return `Critica ${pct(g.critics)}`;
  if (kind === "rating" && g.rating != null) return `★ ${pct(g.rating)}%${g.votes ? ` (${g.votes.toLocaleString("it")})` : ""}`;
  if (kind === "votes") return `${g.votes.toLocaleString("it")} voti`;
  if (kind === "hltb") return g.hltb != null ? `⏱ ${fmtHours(g.hltb)}` : "";
  if (kind === "best") return g.critics != null ? `Critica ${pct(g.critics)}` : g.rating != null ? `★ ${pct(g.rating)}%` : "";
  return "";
}

/* ---- stato del gioco */
const STATUS = {
  backlog: { label: "Da giocare", icon: "📋" }, playing: { label: "In corso", icon: "▶" },
  done: { label: "Finito", icon: "✔" }, dropped: { label: "Abbandonato", icon: "✖" },
};
const fmtHours = (h) => (h == null ? "" : h < 1 ? `${Math.round(h * 60)} min` : `${String(Math.round(h * 10) / 10).replace(".", ",")} h`);

/* ---- collezioni intelligenti: regole salvate, valutate sui dati della libreria */
const SMART_RULES = ["platforms", "stores", "genres", "status", "minRating", "minCritics", "minPlayers", "yearFrom", "yearTo",
  "maxHours", "minHours", "installed", "favorites", "unplayed"];

function smartMatch(g, r = {}) {
  if (g.grp.hidden || (g.store === "online" && !r.includeOnline)) return false;
  if (r.platforms?.length && !r.platforms.includes(g.platform)) return false;
  if (r.stores?.length && !r.stores.includes(g.store || "rom")) return false;
  if (r.genres?.length) {  // generi normalizzati (Azione, Gioco di ruolo...), uguali tra fonti e lingue
    const gs = typeof RECO !== "undefined" ? RECO.canonGenres(g.genres) : [];
    if (!r.genres.some((x) => gs.includes(x))) return false;
  }
  if (r.status?.length && !r.status.includes(g.grp.status || "none")) return false;
  if (r.minRating && !(g.rating >= r.minRating / 100)) return false;
  if (r.minCritics && !(g.critics >= r.minCritics / 100)) return false;
  if (r.minPlayers && !(g.players >= r.minPlayers)) return false;
  if (r.yearFrom && !(g.year >= r.yearFrom)) return false;
  if (r.yearTo && !(g.year && g.year <= r.yearTo)) return false;
  if (r.maxHours && !(g.hltb != null && g.hltb <= r.maxHours)) return false;
  if (r.minHours && !(g.hltb >= r.minHours)) return false;
  if (r.installed && !(!g.store || g.installed)) return false;
  if (r.favorites && !g.grp.favorite) return false;
  if (r.unplayed && (g.grp.last || g.grp.playtime || g.splay)) return false;
  return true;
}

/** Giochi di una collezione intelligente (una versione per gioco). */
function smartGames(games, rules) {
  return games.filter((g) => g.id === g.grp.primary && smartMatch(g, rules));
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "Content-Type": "application/json" } };
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.method = opts.method || "POST"; }
  const r = await fetch(path, init);
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).detail || m; } catch { /* risposta non JSON */ }
    throw new Error(m);
  }
  return r.json();
}

/** Decodifica la risposta compatta di /api/library in oggetti comodi da usare. */
function decodeLibrary(lib) {
  const plats = new Map(lib.platforms.map((p) => [p.id, p]));
  const colls = lib.collections.map((c, i) => ({ ...c, idx: i }));
  const groups = lib.groups.rows.map((r) => ({ primary: r[0], favorite: r[1], hidden: r[2], last: r[3], playtime: r[4],
    plays: r[5], colls: r[6], versions: r[7], status: r[8] || "" }));
  const games = lib.games.rows.map((r) => {
    const g = { id: r[0], title: r[1], sort: r[2], platform: r[3], year: r[4], cover: r[5], gi: r[6], rating: r[7],
      dev: r[8], genres: r[9], found: r[10], region: r[11], store: r[12] || null, installed: r[13], size: r[14] || 0,
      votes: r[15] || 0, critics: r[16] ?? null, players: r[17] || 0, splay: r[18] || 0, hltb: r[19] ?? null,
      content: r[20] || "game" };
    g.grp = groups[g.gi];
    g.q = fold(`${g.title} ${g.dev} ${plats.get(g.platform)?.name || ""} ${g.platform} ${g.store || ""}`);
    return g;
  });
  return { plats, colls, collBySlug: new Map(colls.map((c) => [c.slug, c])), groups, games,
    byId: new Map(games.map((g) => [g.id, g])), stores: lib.stores || [], builtAt: lib.built_at, smart: lib.smart || [] };
}

/* ------------------------------------------------------------------ estensioni */
const GS = (() => {
  const handlers = new Map();
  const ui = { gameActions: [], detailSections: [], tileBadges: [], sidebarItems: [], views: new Map(), settingsTabs: [] };
  const add = (list) => (item) => {
    if (!item || !item.id) throw new Error("serve un id");
    const i = list.findIndex((x) => x.id === item.id);
    if (i >= 0) list[i] = item; else list.push(item);
    GS.emit("uiChanged", { kind: list });
    return item;
  };
  const GS = {
    apiVersion: 1,
    mode: document.documentElement.dataset.mode || "desktop",
    api, esc, fold, store, ui, fmtSize,
    on(ev, fn) { if (!handlers.has(ev)) handlers.set(ev, new Set()); handlers.get(ev).add(fn); return () => handlers.get(ev).delete(fn); },
    emit(ev, data) { for (const fn of handlers.get(ev) || []) { try { fn(data); } catch (e) { console.error(`[GS] ${ev}:`, e); } } },
    addGameAction: add(ui.gameActions), // {id, label, icon?, when?(game), run(game)}
    addDetailSection: add(ui.detailSections), // {id, title, render(game) -> html|Element|Promise}
    addTileBadge: add(ui.tileBadges), // {id, render(game) -> testo|null}
    addSidebarItem: add(ui.sidebarItems), // {id, label, icon?, view?, onClick?}
    addSettingsTab: add(ui.settingsTabs), // {id, label, render(el)}
    registerView(id, view) { ui.views.set(id, view); GS.emit("uiChanged", { kind: "views" }); }, // {title, render(el)}
    // forniti dall'interfaccia attiva (desktop o Big Picture)
    toast: (m) => console.log(m),
    openGame: () => {},
    library: () => null,
    currentGame: () => null,
    plugins: [],
  };
  return GS;
})();
window.GS = GS;

/** Applica il tema attivo e carica i plugin abilitati. */
async function loadExtensions() {
  let ext;
  try { ext = await api("/api/extensions"); } catch (e) { console.warn("estensioni non disponibili", e); return; }
  const mode = GS.mode;
  document.querySelectorAll("link[data-ext]").forEach((l) => l.remove());
  const theme = ext.themes.find((t) => t.id === ext.active_theme);
  const css = theme && (mode === "bigpicture" ? theme.bigpicture : theme.desktop);
  // barra del titolo e bordo della finestra nativa dello stesso colore dello sfondo del tema
  function titlebarColor() {
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    const send = () => window.pywebview?.api?.set_titlebar?.(bg);
    if (window.pywebview?.api) send(); else window.addEventListener("pywebviewready", send, { once: true });
  }
  if (css) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = css; l.dataset.ext = "theme";
    l.onload = titlebarColor;
    document.head.appendChild(l);
  } else titlebarColor();
  document.documentElement.dataset.theme = ext.active_theme || "default";
  GS.plugins = ext.plugins;
  for (const p of ext.plugins.filter((x) => x.enabled)) {
    if (p.style) {
      const l = document.createElement("link");
      l.rel = "stylesheet"; l.href = p.style; l.dataset.ext = "plugin";
      document.head.appendChild(l);
    }
    if (p.frontend) {
      try {
        const m = await import(p.frontend);
        if (typeof m.default === "function") await m.default(GS, { id: p.id, settings: p.settings || {} });
      } catch (e) {
        console.error(`Plugin ${p.id}:`, e);
        GS.toast(`Il plugin «${p.name}» non si e' caricato: ${e.message}`, true);
      }
    }
  }
  GS.emit("extensionsLoaded", ext);
}
