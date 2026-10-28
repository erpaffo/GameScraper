"use strict";
/* GameScraper - Big Picture: interfaccia a schermo intero per controller (e tastiera).
 *
 * Controller: Gamepad API standard (Xbox, PlayStation, Switch Pro...). Croce/stick per muoversi,
 * A conferma, B indietro, Y cerca, X preferito, LB/RB salti veloci, Select+Start tenuti premuti
 * chiudono il gioco in esecuzione. Tastiera: frecce, Invio, Esc/Backspace, F (preferito), / (cerca).
 * Ogni schermata e' un oggetto con render/move/ok/back: la navigazione e' per indici, non per
 * coordinate, quindi resta fluida anche con migliaia di giochi (le griglie sono virtualizzate).
 */

const BP = { lib: null, stack: [], details: new Map(), pad: "xbox", sound: store.get("bpSound", true), running: null,
  palette: store.get("bpPalette", "default"), text: store.get("bpText", "normal"), talk: store.get("bpTalk", false) };
const main = $("#bp-main");

/* ------------------------------------------------------------------ utilita' */
function hueOf(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
function platName(id) { return BP.lib.plats.get(id)?.name || id; }
function fmtTime(sec) { if (!sec) return ""; const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60); return h ? `${h} h ${m} min` : `${m} min`; }

function toast(msg, err = false) {
  const t = $("#bp-toast");
  t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
  announce(msg);
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), err ? 6000 : 3000);
}
GS.toast = toast;

/* ------------------------------------------------------------------ accessibilità */
/** Legge un messaggio con il lettore di schermo (regione aria-live, nessun effetto visivo). */
function announce(text) {
  if (!BP.talk || !text) return;
  const live = $("#bp-live");
  if (!live) return;
  live.textContent = ""; // svuotare prima: alcuni lettori ignorano testi identici consecutivi
  setTimeout(() => { live.textContent = String(text).slice(0, 300); }, 60);
}

/** Applica palette, dimensione dei testi e lettura vocale, ricordando le scelte sul PC. */
function applyLook() {
  const html = document.documentElement;
  html.dataset.palette = BP.palette || "default";
  html.dataset.bptext = BP.text || "normal";
}

/** Annuncia con il lettore di schermo ciò che si sposta il fuoco (card, pulsanti, schede). */
let lastSpoken = "", lastSpokenAt = 0;
function watchFocus() {
  const obs = new MutationObserver(() => {
    const el = document.querySelector("#bp-main .card.focus, #bp-main .bp-btn.focus, #bp-nav .bp-tab.focus");
    if (!el) return;
    const txt = (el.querySelector(".lbl")?.textContent || el.getAttribute("aria-label") || el.textContent || "")
      .replace(/\s+/g, " ").trim();
    if (!txt || txt === lastSpoken) return;
    const now = Date.now();
    if (now - lastSpokenAt < 250) return;
    lastSpoken = txt;
    lastSpokenAt = now;
    const section = el.closest(".shelf")?.querySelector("h2")?.textContent?.trim() || "";
    announce(section ? `${txt} — ${section}` : txt);
  });
  for (const root of ["#bp-main", "#bp-nav"]) {
    const node = $(root);
    if (node) obs.observe(node, { subtree: true, attributes: true, attributeFilter: ["class"] });
  }
}

const PALETTES = [
  { id: "default", label: "Predefinita" },
  { id: "deuteranopia", label: "Daltonismo · verde/rosso (deuteranopia)" },
  { id: "protanopia", label: "Daltonismo · rosso (protanopia)" },
  { id: "tritanopia", label: "Daltonismo · blu/giallo (tritanopia)" },
  { id: "contrast", label: "Alto contrasto (sfondo nero)" },
];
const TEXTS = [
  { id: "normal", label: "Normali" },
  { id: "large", label: "Grandi" },
  { id: "xlarge", label: "Molto grandi" },
];

let actx = null;
function beep(kind) {
  if (!BP.sound) return;
  try {
    actx = actx || new AudioContext();
    const o = actx.createOscillator(), g = actx.createGain();
    o.frequency.value = { move: 520, ok: 880, back: 330, err: 180 }[kind] || 500;
    o.type = "sine"; g.gain.value = 0.035;
    o.connect(g); g.connect(actx.destination);
    o.start(); g.gain.exponentialRampToValueAtTime(0.0001, actx.currentTime + 0.07); o.stop(actx.currentTime + 0.08);
  } catch { /* audio non disponibile */ }
}

function visibleGames() {
  return BP.lib.games.filter((g) => g.id === g.grp.primary && !g.grp.hidden && g.store !== "online");
}

function card(g, i, extra = "", score = "") {
  const tag = g.store ? (STORE_SHORT[g.store] || "PC") : (SHORT[g.platform] || g.platform.toUpperCase());
  return `<div class="card${g.store && !g.installed ? " notinst" : ""}" data-i="${i}" ${extra}>`
    + `<div class="cv" style="--h:${hueOf(g.title)}"><div class="ph">${esc(g.title)}</div>`
    + (g.cover ? `<img loading="lazy" decoding="async" src="/img/${g.id}/cover?w=300" onerror="this.remove()">` : "")
    + `<span class="tag">${esc(tag)}</span>${score ? `<span class="score">${esc(score)}</span>` : ""}</div><div class="lbl">${esc(g.title)}</div></div>`;
}

function textCard(label, sub, i, hueKey, img, it = {}) {
  if (it.art) {  // collezione: copertina a tutto riquadro + logo
    return `<div class="card tile-text coll-card" data-i="${i}"><div class="cv" style="--h:${hueOf(hueKey || label)}">`
      + `<img class="art" src="${it.art}" alt="" loading="lazy" onerror="this.remove()">`
      + (it.icon ? `<img class="logo" src="${it.icon}" alt="" loading="lazy" onerror="this.remove()">` : "")
      + `<div class="txt">${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</div></div></div>`;
  }
  return `<div class="card tile-text${img ? " with-img" : ""}" data-i="${i}"><div class="cv" style="--h:${hueOf(hueKey || label)}">`
    + (img ? `<img class="sys" src="${img}" alt="" loading="lazy" onerror="this.remove()">` : "")
    + `<div class="txt">${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</div></div></div>`;
}

/* ------------------------------------------------------------------ sfondo e dettagli */
async function detail(id) {
  if (!BP.details.has(id)) BP.details.set(id, api(`/api/game/${id}`).catch(() => null));
  return BP.details.get(id);
}

let bgTimer = 0, bgFront = 0;
function setBackground(id) {
  clearTimeout(bgTimer);
  bgTimer = setTimeout(async () => {
    const d = id ? await detail(id) : null;
    const m = d?.media || {};
    const src = m.hero || m.screenshot || m.fanart || m.header || m.title || m.cover;
    const layers = document.querySelectorAll("#bp-bg .bg-img");
    const next = layers[1 - bgFront];
    if (!src) { layers.forEach((l) => l.classList.remove("on")); return; }
    next.style.backgroundImage = `url("${src}?w=1280")`;
    next.classList.add("on");
    layers[bgFront].classList.remove("on");
    bgFront = 1 - bgFront;
  }, 220);
}

/* ------------------------------------------------------------------ schermate */
function push(screen) { BP.stack.push(screen); show(); }
function pop() { if (BP.stack.length > 1) { BP.stack.pop(); beep("back"); show(); } }
function cur() { return BP.stack[BP.stack.length - 1]; }
function show() { const s = cur(); main.innerHTML = ""; s.render(main); renderNav(); renderHints(); announce(s.title || ""); lastSpoken = ""; }

const TABS = [
  { id: "home", label: "Home", open: () => { BP.stack = [homeScreen()]; show(); } },
  { id: "foryou", label: "Per te", open: () => push(forYouScreen()) },
  { id: "all", label: "Libreria", open: () => push(discoverScreen("Libreria", sortTitle(visibleGames()), "all")) },
  { id: "pc", label: "PC", open: () => push(pcScreen()) },
  { id: "shop", label: "Store", open: () => push(shopScreen()) },
  { id: "plat", label: "Console", open: () => push(platformsScreen()) },
  { id: "coll", label: "Collezioni", open: () => push(collectionsScreen()) },
  { id: "free", label: "Gratis", open: () => push(freeScreen()) },
  { id: "fav", label: "Preferiti", open: () => push(gridScreen("Preferiti", sortTitle(visibleGames().filter((g) => g.grp.favorite)))) },
  { id: "search", label: "Cerca", open: () => push(searchScreen()) },
  { id: "look", label: "Aspetto", open: () => push(lookScreen()) },
  { id: "exit", label: "Esci", open: () => exitBigPicture() },
];

/** Apre una scheda del menu' in alto (le schede non si accumulano: Home + scheda). */
function openTab(i) {
  const t = TABS[i];
  if (!t) return;
  if (t.id === "exit") { t.open(); return; }
  BP.stack = [homeScreen()];
  if (t.id === "home") show(); else t.open();
}

/** L1/R1 (LB/RB), Q/E, Tab: scheda precedente/successiva ("Esci" non si apre scorrendo). */
function stepTab(d) {
  const ids = TABS.map((_, i) => i).filter((i) => TABS[i].id !== "exit");
  let k = ids.findIndex((i) => TABS[i].id === cur().tab);
  if (k < 0) k = 0;
  k = (k + d + ids.length) % ids.length;
  beep("move");
  openTab(ids[k]);
  const el = document.querySelectorAll(".bp-tab")[ids[k]];
  if (el) { el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump"); }
}

function renderNav() {
  const s = cur();
  $("#bp-nav").innerHTML = TABS.map((t, i) => `<span data-tab="${i}" class="bp-tab${s.tab === t.id ? " active" : ""}${s.navFocus === i ? " focus" : ""}">${esc(t.label)}</span>`).join("");
}

const sortTitle = (list) => list.slice().sort((a, b) => collator.compare(a.sort, b.sort));

/* ---- home: scaffali orizzontali */
function homeScreen() {
  const games = visibleGames();
  const recent = games.filter((g) => g.grp.last).sort((a, b) => b.grp.last - a.grp.last).slice(0, 30);
  const favs = sortTitle(games.filter((g) => g.grp.favorite)).slice(0, 40);
  const pcInst = sortTitle(games.filter((g) => g.store && g.installed)).slice(0, 40);
  const counts = new Map();
  for (const g of games) for (const i of g.grp.colls) counts.set(i, (counts.get(i) || 0) + 1);
  const topColls = BP.lib.colls.filter((c) => !c.hidden && (counts.get(c.idx) || 0) >= 4)
    .sort((a, b) => (counts.get(b.idx) || 0) - (counts.get(a.idx) || 0)).slice(0, 6);
  const shelves = [];
  if (recent.length) shelves.push({ title: "Continua a giocare", items: recent });
  if (favs.length) shelves.push({ title: "Preferiti", items: favs });
  if (pcInst.length) shelves.push({ title: "Giochi PC installati", items: pcInst });
  for (const id of ["loved", "critics", "gems"]) {
    const d = discover(games, { preset: id });
    if (d.items.length >= 5) shelves.push({ title: d.preset.label, items: d.items.slice().sort(d.cmp).slice(0, 30),
      more: () => { store.set("bpDisc", id); push(discoverScreen("Libreria", sortTitle(games), "all", id)); } });
  }
  const shop = shopGames(BP.lib.games);
  if (shop.length) shelves.push({ title: "Store · da installare", sub: `${shop.length} giochi`,
    items: shopFilter(shop, { sort: "-rating" }).slice(0, 40), more: () => push(shopScreen()) });
  for (const c of topColls) {
    const items = games.filter((g) => g.grp.colls.includes(c.idx)).sort((a, b) => (a.year || 9999) - (b.year || 9999));
    shelves.push({ title: c.name, sub: `${items.length} giochi`, items: items.slice(0, 40), more: () => push(gridScreen(c.name, items, "coll")) });
  }
  const plats = [...BP.lib.plats.values()].filter((p) => p.id !== "windows").sort((a, b) => collator.compare(a.name, b.name));
  const stores = BP.lib.stores.filter((s) => games.some((g) => g.store === s.id));
  if (stores.length) shelves.push({ title: "Store", text: true, items: stores.map((s) => ({ label: s.name, key: s.id,
    sub: `${games.filter((g) => g.store === s.id && g.installed).length} installati / ${games.filter((g) => g.store === s.id).length}`,
    img: `/icons/store/${s.id}`,
    ok: () => push(gridScreen(s.name, sortTitle(games.filter((g) => g.store === s.id)), "pc")) })) });
  shelves.push({ title: "Console", text: true, items: plats.map((p) => ({ label: p.name, sub: `${p.count} giochi`, key: p.id,
    img: `/icons/platform/${encodeURIComponent(p.id)}`,
    ok: () => push(gridScreen(p.name, sortTitle(games.filter((g) => g.platform === p.id)), "plat")) })) });
  if (!shelves.length || shelves.every((s) => !s.items.length)) shelves.unshift({ title: "Libreria", items: sortTitle(games).slice(0, 40) });

  return shelvesScreen(shelves, "home");
}

function shelvesScreen(shelves, tab) {
  const scr = {
    tab, row: 0, cols: shelves.map(() => 0), navFocus: null, shelves,
    render(el) {
      el.innerHTML = `<div class="bp-home"><div class="bp-hero" id="hero"></div><div class="bp-shelves"><div class="bp-shelves-inner" id="shelves">`
        + shelves.map((sh, r) => `<section class="shelf" data-r="${r}"><h2>${esc(sh.title)}${sh.sub ? `<span>${esc(sh.sub)}</span>` : ""}</h2>`
          + `<div class="shelf-row">${sh.items.map((it, i) => (sh.text ? textCard(it.label, it.sub, i, it.key, it.img, it) : card(it, i))).join("")}</div></section>`).join("")
        + `</div></div></div>`;
      this.update();
    },
    current() { const sh = shelves[this.row]; return sh && !sh.text ? sh.items[this.cols[this.row]] : null; },
    update() {
      document.querySelectorAll(".card.focus").forEach((c) => c.classList.remove("focus"));
      renderNav();
      if (this.navFocus !== null) return;
      const sec = document.querySelector(`.shelf[data-r="${this.row}"]`);
      if (!sec) return;
      const c = sec.querySelectorAll(".card")[this.cols[this.row]];
      c?.classList.add("focus");
      // scorrimento: scaffale corrente in alto, carta corrente visibile
      $("#shelves").style.transform = `translateY(${-sec.offsetTop}px)`;
      const row = sec.querySelector(".shelf-row");
      const off = c ? Math.max(0, c.offsetLeft - row.clientWidth * 0.3) : 0;
      row.style.transform = `translateX(${-off}px)`;
      const g = this.current();
      const hero = $("#hero");
      if (g) {
        hero.innerHTML = `<h1>${esc(g.title)}</h1><div class="meta">${esc([g.store ? STORE_SHORT[g.store] : platName(g.platform), g.year, g.dev].filter(Boolean).join("  ·  "))}</div>`;
        setBackground(g.id);
        detail(g.id).then((d) => {
          if (d && d.media.logo && this.current() === g) hero.innerHTML = `<img class="logo-img" src="${d.media.logo}?w=700" onerror="this.outerHTML='<h1>${esc(g.title).replace(/'/g, "")}</h1>'">`
            + `<div class="meta">${esc([d.store_name || d.platform_name, d.year, d.developer].filter(Boolean).join("  ·  "))}</div>`;
        });
      } else {
        const it = shelves[this.row].items[this.cols[this.row]];
        hero.innerHTML = it ? `<h1>${esc(it.label)}</h1><div class="meta">${esc(it.sub || "")}</div>` : "";
        setBackground(null);
      }
    },
    move(dx, dy) {
      if (this.navFocus !== null) {
        if (dy > 0) { this.navFocus = null; this.update(); return true; }
        const n = this.navFocus + dx;
        if (n < 0 || n >= TABS.length) return false;
        this.navFocus = n; renderNav(); return true;
      }
      if (dy) {
        const r = this.row + dy;
        if (r < 0) { this.navFocus = 0; this.update(); return true; }
        if (r >= shelves.length) return false;
        this.row = r;
      } else {
        const c = this.cols[this.row] + dx;
        if (c < 0 || c >= shelves[this.row].items.length) return false;
        this.cols[this.row] = c;
      }
      this.update(); return true;
    },
    jump(d) { this.cols[this.row] = Math.max(0, Math.min(shelves[this.row].items.length - 1, this.cols[this.row] + d * 6)); this.update(); },
    ok() {
      if (this.navFocus !== null) { openTab(this.navFocus); return; }
      const sh = shelves[this.row], it = sh.items[this.cols[this.row]];
      if (!it) return;
      if (sh.text) it.ok(); else push(gameScreen(it.id));
    },
    fav() { const g = this.current(); if (g) toggleFav(g); },
    hints: [["a", "Apri"], ["y", "Cerca"], ["x", "Preferito"]],
  };
  return scr;
}

/* ---- griglia virtualizzata (console, collezione, PC, libreria) */
/** Griglia con i filtri Scopri: LT/RT cambiano il filtro, l'intestazione mostra quello attivo. */
function discoverScreen(title, base, tab, preset = store.get("bpDisc", ""), i = 0) {
  const d = discover(base, { preset });
  const items = d.cmp ? d.items.slice().sort(d.cmp) : d.items;
  const head = `<div class="shop-chips"><span class="chip"><b>${glyph("lt")}</b><b>${glyph("rt")}</b> ${esc(d.preset.label)}</span>`
    + (d.preset.hint ? `<span class="chip muted">${esc(d.preset.hint)}</span>` : "") + `</div>`;
  const scr = gridScreen(title, items, tab, { head, score: d.preset.score });
  scr.i = Math.min(i, Math.max(0, items.length - 1));
  const go = (dir) => {
    const k = (PRESETS.findIndex((p) => p.id === preset) + dir + PRESETS.length) % PRESETS.length;
    store.set("bpDisc", PRESETS[k].id);
    BP.stack[BP.stack.length - 1] = discoverScreen(title, base, tab, PRESETS[k].id);
    show(); beep("move");
  };
  scr.lt = () => go(-1);
  scr.rt = () => go(1);
  scr.hints = [["a", "Apri"], ["b", "Indietro"], ["x", "Preferito"], ["lt", "Filtro"], ["rt", "Filtro"]];
  return scr;
}

function gridScreen(title, items, tab = "all", opts = {}) {
  const scr = {
    tab, title, items, i: 0, navFocus: null, cols: 6, cw: 0, ch: 0,
    render(el) {
      el.innerHTML = `<div class="bp-grid-view"><div class="bp-grid-head"><h1>${esc(title)}</h1><span>${items.length.toLocaleString("it")} ${opts.unit || "giochi"}</span>`
        + (opts.head || "") + `</div><div class="bp-grid" id="grid"><div class="bp-grid-inner" id="grid-inner"></div></div></div>`;
      if (!items.length) { $("#grid-inner").innerHTML = `<p style="padding:0 3rem" class="muted">Nessun gioco.</p>`; return; }
      const W = $("#grid").clientWidth - 6 * 16;
      const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
      const cardW = (opts.text ? 15 : 11) * rem, gap = 1.3 * rem;
      this.cols = Math.max(1, Math.floor((W + gap) / (cardW + gap)));
      this.cw = cardW + gap;
      this.ch = (opts.text ? cardW * 9 / 16 : cardW * 4 / 3) + (opts.text ? 1.4 : 3.2) * rem;
      this.update();
    },
    update() {
      renderNav();
      const inner = $("#grid-inner"), grid = $("#grid");
      if (!inner || !items.length) return;
      const row = Math.floor(this.i / this.cols), H = grid.clientHeight;
      const rowsVisible = Math.ceil(H / this.ch) + 2;
      const scrollRow = Math.max(0, row - Math.floor(rowsVisible / 2) + 1);
      const from = Math.max(0, (scrollRow - 1) * this.cols), to = Math.min(items.length, (scrollRow + rowsVisible + 1) * this.cols);
      inner.style.transform = `translateY(${-scrollRow * this.ch}px)`;
      const pad = 3 * parseFloat(getComputedStyle(document.documentElement).fontSize);
      let html = "";
      for (let k = from; k < to; k++) {
        const r = Math.floor(k / this.cols), c = k % this.cols, it = items[k];
        const pos = `style="left:${pad + c * this.cw}px;top:${r * this.ch + 16}px"`;
        html += opts.text ? textCard(it.label, it.sub, k, it.key, it.img, it).replace(`data-i="${k}"`, `data-i="${k}" ${pos}`)
          : card(it, k, pos, opts.score ? scoreText(it, opts.score) : "");
      }
      inner.innerHTML = html;
      if (this.navFocus === null) inner.querySelector(`.card[data-i="${this.i}"]`)?.classList.add("focus");
      const cur = items[this.i];
      if (cur && !opts.text) setBackground(cur.id);
    },
    move(dx, dy) {
      if (this.navFocus !== null) {
        if (dy > 0) { this.navFocus = null; this.update(); return true; }
        const n = this.navFocus + dx;
        if (n < 0 || n >= TABS.length) return false;
        this.navFocus = n; renderNav(); return true;
      }
      let n = this.i + dx + dy * this.cols;
      if (dy < 0 && n < 0) { this.navFocus = 0; this.update(); return true; }
      if (dx && Math.floor(n / this.cols) !== Math.floor(this.i / this.cols)) return false;
      if (n < 0) return false;
      if (n >= items.length) { if (dy > 0 && Math.floor(this.i / this.cols) < Math.floor((items.length - 1) / this.cols)) n = items.length - 1; else return false; }
      this.i = n; this.update(); return true;
    },
    jump(d) { this.i = Math.max(0, Math.min(items.length - 1, this.i + d * this.cols * 3)); this.update(); },
    ok() {
      if (this.navFocus !== null) { openTab(this.navFocus); return; }
      const it = items[this.i];
      if (!it) return;
      if (opts.text) it.ok(); else push(gameScreen(it.id));
    },
    fav() { const it = items[this.i]; if (it && !opts.text) toggleFav(it); },
    hints: opts.text ? [["a", "Apri"], ["b", "Indietro"]] : [["a", "Apri"], ["b", "Indietro"], ["x", "Preferito"], ["lt", "Pagina"]],
  };
  return scr;
}

/* ---- Aspetto (accessibilità): palette per il daltonismo, dimensione dei testi, suoni, lettura vocale */
function lookScreen() {
  const items = [];
  const same = (a, b) => a === b;
  for (const p of PALETTES) {
    items.push({
      label: `${same(BP.palette, p.id) ? "●" : "○"} ${p.label}`, sub: same(BP.palette, p.id) ? "in uso" : "",
      key: "pal:" + p.id,
      ok: () => {
        BP.palette = p.id; store.set("bpPalette", p.id); applyLook(); beep("ok");
        toast(`Palette: ${p.label}`); push(lookScreen());
      },
    });
  }
  for (const t of TEXTS) {
    items.push({
      label: `${same(BP.text, t.id) ? "●" : "○"} Testi ${t.label.toLowerCase()}`,
      sub: same(BP.text, t.id) ? "in uso" : "", key: "txt:" + t.id,
      ok: () => {
        BP.text = t.id; store.set("bpText", t.id); applyLook(); beep("ok");
        toast(`Testi: ${t.label}`); push(lookScreen());
      },
    });
  }
  items.push({
    label: `${BP.sound ? "●" : "○"} Suoni dell'interfaccia`, sub: BP.sound ? "attivi" : "spenti", key: "snd",
    ok: () => { BP.sound = !BP.sound; store.set("bpSound", BP.sound); beep("ok"); push(lookScreen()); },
  });
  items.push({
    label: `${BP.talk ? "●" : "○"} Lettura vocale`, sub: BP.talk ? "attiva" : "spenta", key: "talk",
    ok: () => {
      BP.talk = !BP.talk; store.set("bpTalk", BP.talk);
      toast(BP.talk ? "Lettura vocale attiva" : "Lettura vocale spenta");
      announce(BP.talk ? "Lettura vocale attiva" : "Lettura vocale spenta");
      push(lookScreen());
    },
  });
  const scr = gridScreen("Aspetto", items, "look", { text: true, unit: "opzioni" });
  scr.hints = [["a", "Scegli"], ["b", "Indietro"], ["lt", "Scheda"]];
  return scr;
}


/* ---- Store: giochi da installare da tutti gli store; X = store, Y = ordine, LT/RT (Q/E) = dimensione/piattaforma */
function shopScreen(f = store.get("bpShopF", { src: "", plat: "", size: "", sort: "title" }), i = 0) {
  const all = shopGames(BP.lib.games);
  const items = shopFilter(all, f);
  const srcs = [{ id: "", label: "Tutti gli store" }].concat(shopSources(all, BP.lib.stores, platName));
  const plats = [""].concat([...new Set(all.map((g) => g.platform))].sort((a, b) => collator.compare(platName(a), platName(b))));
  const cycle = (list, v, key = (x) => x) => list[(list.findIndex((x) => key(x) === v) + 1) % list.length];
  const redo = (nf) => { store.set("bpShopF", nf); BP.stack[BP.stack.length - 1] = shopScreen(nf, 0); show(); beep("move"); };
  const chip = (k, l) => `<span class="chip"><b>${glyph(k)}</b> ${esc(l)}</span>`;
  const head = `<div class="shop-chips">`
    + chip("x", (srcs.find((s) => s.id === f.src) || srcs[0]).label)
    + chip("y", (SHOP_SORTS.find((s) => s.id === f.sort) || SHOP_SORTS[0]).label)
    + chip("lt", (SHOP_SIZES.find((s) => s.id === (f.size || "")) || SHOP_SIZES[0]).label)
    + chip("rt", f.plat ? platName(f.plat) : "Tutte le piattaforme") + `</div>`;
  const scr = gridScreen("Store", items, "shop", { head });
  scr.i = Math.min(i, Math.max(0, items.length - 1));
  scr.fav = () => redo({ ...f, src: cycle(srcs, f.src, (s) => s.id).id });
  scr.alt = () => redo({ ...f, sort: cycle(SHOP_SORTS, f.sort || "title", (s) => s.id).id });
  scr.lt = () => redo({ ...f, size: cycle(SHOP_SIZES, f.size || "", (s) => s.id).id });
  scr.rt = () => redo({ ...f, plat: cycle(plats, f.plat || "") });
  scr.hints = [["a", "Apri"], ["b", "Indietro"], ["x", "Store"], ["y", "Ordine"], ["lt", "Peso"], ["rt", "Piattaforma"]];
  return scr;
}

/* ---- "Per te": righe di consigli + questionario */
function forYouScreen() {
  RECO.setPlatName(platName);
  const rows = RECO.rows(BP.lib.games, RECO.tastes());
  const shelves = [{ title: "A cosa vuoi giocare?", text: true, items: [
    { label: "🎲 A cosa vuoi giocare?", sub: "Qualche domanda e ti suggerisco cosa giocare adesso", key: "quiz", ok: () => push(quizScreen(0, {})) }] }]
    .concat(rows.map((r) => ({ title: r.title, sub: r.why, items: r.items })));
  // sezione collezioni: saghe da continuare, collezioni affini, le meglio valutate
  const games = visibleGames();
  for (const r of RECO.collectionRows(BP.lib.games, BP.lib.colls, RECO.tastes())) {
    shelves.push({ title: r.title, text: true, items: r.items.map((x) => ({ label: x.coll.name, sub: x.why, key: x.coll.slug, ...collArt(x.coll.slug),
      ok: () => push(gridScreen(x.coll.name, games.filter((g) => g.grp.colls.includes(x.coll.idx)).sort((a, b) => (a.year || 9999) - (b.year || 9999)), "coll")) })) });
  }
  const scr = shelvesScreen(shelves, "foryou");
  scr.row = Math.min(1, shelves.length - 1);
  return scr;
}

function quizScreen(step, a) {
  const Q = RECO.QUESTIONS;
  if (step >= Q.length) {
    RECO.saveTastes(a);
    const res = RECO.suggest(BP.lib.games, a, 18);
    const scr = gridScreen("Da giocare adesso", res.map((x) => x.g), "foryou", { head: `<span>in base alle tue risposte</span>` });
    return scr;
  }
  const q = Q[step];
  const sel = new Set(q.multi ? a[q.id] || [] : []);
  const items = q.options.map((o) => ({ label: (sel.has(o.id) ? "✓ " : "") + o.label, key: o.id,
    ok: () => {
      if (q.multi) {
        sel.has(o.id) ? sel.delete(o.id) : sel.add(o.id);
        const i = cur().i;
        BP.stack[BP.stack.length - 1] = quizScreen(step, { ...a, [q.id]: [...sel] });
        cur().i = i; show();
      } else { BP.stack[BP.stack.length - 1] = quizScreen(step + 1, { ...a, [q.id]: o.id }); show(); }
    } }));
  if (q.multi) items.push({ label: "Avanti ›", sub: sel.size ? `${sel.size} scelti` : "oppure salta", key: "next",
    ok: () => { BP.stack[BP.stack.length - 1] = quizScreen(step + 1, a); show(); } });
  return gridScreen(`${step + 1}/${Q.length} · ${q.title}`, items, "foryou", { text: true });
}

/* ---- giochi gratis: A apre la pagina dell'offerta nel browser */
function freeScreen() {
  const scr = gridScreen("Giochi gratis", [], "free", { text: true });
  const base = scr.render.bind(scr);
  scr.render = async (el) => {
    el.innerHTML = `<div class="bp-grid-view"><div class="bp-grid-head"><h1>Giochi gratis</h1><span>Carico…</span></div></div>`;
    let d;
    try { d = await api("/api/free"); } catch (e) { toast(e.message, true); return; }
    const KIND = { "Free to keep": "Da tenere", "Free with Prime": "Con Prime", "Free with PS+": "Con PS Plus", Gamepass: "Game Pass", "Free DLC": "DLC gratis" };
    scr.items = d.items.filter((i) => !i.owned).map((i) => ({ label: i.title, key: i.title,
      sub: [KIND[i.kind] || i.kind, ({ pc: "PC", playstation: "PlayStation", xbox: "Xbox" })[i.console] || i.console,
        i.end ? "fino al " + new Date(i.end.replace(" ", "T")).toLocaleDateString("it") : ""].filter(Boolean).join(" · "),
      img: i.cover ? `/remote-img?u=${encodeURIComponent(i.cover)}` : null,
      ok: () => api("/api/open", { body: { url: i.url } }).then(() => toast(`Apro «${i.title}» nel browser`)) }));
    const fresh = gridScreen("Giochi gratis", scr.items, "free", { text: true, head: `<span>A = apri l'offerta nel browser</span>` });
    BP.stack[BP.stack.length - 1] = fresh;
    show();
  };
  void base;
  return scr;
}

function platformsScreen() {
  const games = visibleGames();
  const plats = [...BP.lib.plats.values()].filter((p) => p.id !== "windows").sort((a, b) => collator.compare(a.name, b.name));
  return gridScreen("Console", plats.map((p) => ({ label: p.name, sub: `${p.count} giochi`, key: p.id,
    img: `/icons/platform/${encodeURIComponent(p.id)}`,
    ok: () => push(discoverScreen(p.name, sortTitle(games.filter((g) => g.platform === p.id)), "plat")) })), "plat", { text: true, unit: "console" });
}

const collArt = (slug) => ({ art: `/coll-img/${encodeURIComponent(slug)}/cover?w=640`, icon: `/coll-img/${encodeURIComponent(slug)}/icon?w=256` });

function collectionsScreen() {
  const games = visibleGames();
  const counts = new Map();
  for (const g of games) for (const i of g.grp.colls) counts.set(i, (counts.get(i) || 0) + 1);
  const colls = BP.lib.colls.filter((c) => !c.hidden && counts.get(c.idx)).sort((a, b) => (counts.get(b.idx) || 0) - (counts.get(a.idx) || 0));
  const smart = (BP.lib.smart || []).map((s) => ({ s, items: smartGames(BP.lib.games, s.rules) })).filter((x) => x.items.length);
  return gridScreen("Collezioni", smart.map(({ s, items }) => ({ label: `${s.icon || "⚡"} ${s.name}`, sub: `${items.length} giochi · intelligente`, key: s.id,
    ok: () => push(gridScreen(s.name, sortTitle(items), "coll")) })).concat(colls.map((c) => ({ label: c.name, sub: `${counts.get(c.idx)} giochi`, key: c.slug, ...collArt(c.slug),
    ok: () => push(gridScreen(c.name, games.filter((g) => g.grp.colls.includes(c.idx)).sort((a, b) => (a.year || 9999) - (b.year || 9999)), "coll")) }))),
  "coll", { text: true, unit: "collezioni" });
}

function pcScreen() {
  const games = visibleGames().filter((g) => g.platform === "windows");
  const inst = sortTitle(games.filter((g) => g.installed)), other = sortTitle(games.filter((g) => !g.installed));
  return gridScreen("Giochi PC", inst.concat(other), "pc", { head: `<span>${inst.length} installati · ${other.length} da installare</span>` });
}

/* ---- pagina del gioco */
function gameScreen(id) {
  const scr = {
    tab: null, d: null, i: 0, buttons: [], navFocus: null,
    async render(el) {
      el.innerHTML = `<div class="bp-game"><div class="big-cover"></div><div class="info"><h1>…</h1></div></div>`;
      const d = this.d || await detail(id);
      if (!d) { toast("Gioco non trovato", true); pop(); return; }
      this.d = d;
      setBackground(id);
      const pc = !!d.store;
      this.buttons = [];
      if (pc && !d.installed) this.buttons.push({ label: "⬇ Installa", cls: "main", run: () => installGame(d) });
      else this.buttons.push({ label: "▶ Gioca", cls: "main", run: () => playGame(d), disabled: !d.emulator });
      const stOrder = ["", "backlog", "playing", "done", "dropped"];
      const st = d.user.status || "";
      if (!d.store || d.store !== "online") this.buttons.push({ label: st ? `${STATUS[st].icon} ${STATUS[st].label}` : "📋 Stato",
        cls: st ? "on" : "", run: async () => {
          const next = stOrder[(stOrder.indexOf(st) + 1) % stOrder.length];
          try { await api(`/api/game/${id}/flag`, { body: { field: "status", value: next } }); } catch (e) { return toast(e.message, true); }
          const g = BP.lib.byId.get(id);
          if (g) g.grp.status = next;
          BP.details.delete(id);
          toast(next ? `Stato: ${STATUS[next].label}` : "Stato tolto");
          const i = this.i; this.d = null; await this.render(main); this.i = i; this.update();
        } });
      this.buttons.push({ label: d.user.favorite ? "★ Preferito" : "☆ Preferito", cls: d.user.favorite ? "on" : "", run: () => toggleFav(BP.lib.byId.get(id)) });
      if (d.user.last_played) this.buttons.push({ label: "✖ Togli dai recenti", run: async () => {
        try { await api(`/api/game/${id}/flag`, { body: { field: "last_played", value: null } }); } catch (e) { return toast(e.message, true); }
        const g = BP.lib.byId.get(id);
        if (g) g.grp.last = 0;
        BP.details.delete(id);
        toast("Tolto da «Continua a giocare»");
        const i = this.i; this.d = null; await this.render(main); this.i = Math.min(i, this.buttons.length - 1); this.update();
      } });
      if (d.versions.length > 1) this.buttons.push({ label: `Versioni (${d.versions.length})`, run: () => push(versionsScreen(d)) });
      if (d.shots) this.buttons.push({ label: `📷 Schermate (${d.shots})`, run: () => push(shotsScreen(d)) });
      this.buttons.push({ label: "Indietro", run: () => pop() });
      const facts = [["Uscita", d.release_date || d.year], ["Sviluppatore", d.developer], ["Generi", (d.genres || []).join(", ")],
        ["Giocatori", d.players_local],
        ["Durata", d.hltb ? [d.hltb.main && `storia ${fmtHours(d.hltb.main)}`, d.hltb.complete && `100% ${fmtHours(d.hltb.complete)}`].filter(Boolean).join(" · ") : ""], ["Tempo di gioco", fmtTime(d.user.playtime || d.store_playtime)],
        ["Dimensione", fmtSize(d.size)],
        ["Valutazione", d.rating != null ? `${(d.rating * 5).toFixed(1)} / 5${d.rating_count ? ` (${d.rating_count.toLocaleString("it")} voti)` : ""}` : ""],
        ["Critica", d.rating_critics != null ? `${Math.round(d.rating_critics * 100)} / 100` : ""]].filter(([, v]) => v);
      el.innerHTML = `<div class="bp-game"><div class="big-cover">${d.media.cover ? `<img src="${d.media.cover}?w=700">` : ""}</div>
        <div class="info">${d.media.logo ? `<img class="logo-img" src="${d.media.logo}?w=700" onerror="this.replaceWith(Object.assign(document.createElement('h1'),{textContent:${JSON.stringify(d.title)}}))">` : `<h1>${esc(d.title)}</h1>`}
        <div class="meta">${esc([pc ? d.store_name : d.platform_name, d.year].filter(Boolean).join("  ·  "))}
          ${pc ? (d.installed ? " · installato" : " · non installato") : d.emulator ? " · " + esc(d.emulator) : " · nessun emulatore configurato"}</div>
        <div class="bp-actions">${this.buttons.map((b, i) => `<div class="bp-btn ${b.cls || ""}${b.disabled ? " disabled" : ""}" data-i="${i}">${esc(b.label)}</div>`).join("")}</div>
        <div class="bp-facts">${facts.map(([k, v]) => `<span>${k}: <b>${esc(v)}</b></span>`).join("")}</div>
        ${d.description ? `<div class="bp-desc">${esc(d.description)}</div>` : ""}</div></div>`;
      this.update();
    },
    update() {
      renderNav();
      document.querySelectorAll(".bp-btn").forEach((b) => b.classList.toggle("focus", +b.dataset.i === this.i && this.navFocus === null));
    },
    move(dx, dy) {
      if (this.navFocus !== null) {
        if (dy > 0) { this.navFocus = null; this.update(); return true; }
        const n = this.navFocus + dx;
        if (n < 0 || n >= TABS.length) return false;
        this.navFocus = n; renderNav(); return true;
      }
      if (dy < 0) { this.navFocus = 0; this.update(); return true; }
      const n = this.i + dx;
      if (n < 0 || n >= this.buttons.length) return false;
      this.i = n; this.update(); return true;
    },
    ok() {
      if (this.navFocus !== null) { openTab(this.navFocus); return; }
      const b = this.buttons[this.i];
      if (b && !b.disabled) b.run(); else if (b?.disabled) { beep("err"); toast("Nessun emulatore configurato per questa console (Impostazioni › Emulatori)", true); }
    },
    fav() { toggleFav(BP.lib.byId.get(id)); },
    hints: [["a", "Seleziona"], ["b", "Indietro"], ["x", "Preferito"]],
  };
  return scr;
}

/* ---- schermate del gioco: visualizzatore a tutto schermo, sinistra/destra per scorrere */
function shotsScreen(d) {
  const scr = {
    tab: null, i: 0, list: [], navFocus: null,
    async render(el) {
      el.innerHTML = `<div class="bp-shots"><p class="muted">Carico…</p></div>`;
      try { this.list = (await api(`/api/game/${d.id}/shots`)).shots; } catch (e) { toast(e.message, true); return; }
      this.update();
    },
    update() {
      renderNav();
      const s = this.list[this.i];
      const el = document.querySelector(".bp-shots");
      if (!el) return;
      el.innerHTML = s ? `<img src="${s.url}" alt=""><div class="bp-shots-bar"><b>${esc(d.title)}</b>
        <span>${new Date(s.taken * 1000).toLocaleString("it", { dateStyle: "medium", timeStyle: "short" })} · ${esc(s.source)}</span>
        <span>${this.i + 1} / ${this.list.length}</span></div>` : `<p class="muted">Nessuna schermata.</p>`;
    },
    move(dx) {
      const n = this.i + dx;
      if (!dx || n < 0 || n >= this.list.length) return false;
      this.i = n; this.update(); return true;
    },
    ok() {},
    hints: [["b", "Indietro"]],
  };
  return scr;
}

function versionsScreen(d) {
  return gridScreen(`${d.title} · versioni`, d.versions.map((v) => ({
    label: v.store ? `${v.file}${v.installed ? "" : " (da installare)"}` : v.file, sub: v.primary ? "principale" : "", key: v.file,
    ok: async () => {
      const vd = await detail(v.id);
      if (vd) (vd.store && !vd.installed ? installGame(vd) : playGame(vd));
    },
  })), null, { text: true });
}

/* ---- ricerca con tastiera a schermo */
function searchScreen() {
  const KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".split("").concat(["␣", "⌫", "Cancella"]);
  const scr = {
    tab: "search", q: "", k: 0, results: [], navFocus: null, inResults: false, r: 0,
    render(el) {
      el.innerHTML = `<div class="bp-search"><div class="kbd">${KEYS.map((k, i) => `<div class="k${k.length > 1 ? " wide" : ""}" data-k="${i}">${k}</div>`).join("")}</div>
        <div><div class="search-box" id="sq"></div><div class="search-res" id="sr"></div></div></div>`;
      this.search();
    },
    search() {
      const toks = fold(this.q).split(/\s+/).filter(Boolean);
      this.results = toks.length ? visibleGames().filter((g) => toks.every((t) => g.q.includes(t))).slice(0, 24) : [];
      $("#sq").textContent = this.q || "Scrivi per cercare…";
      $("#sr").innerHTML = this.results.map((g, i) => card(g, i)).join("");
      this.r = Math.min(this.r, Math.max(0, this.results.length - 1));
      this.update();
    },
    update() {
      renderNav();
      document.querySelectorAll(".kbd .k").forEach((e) => e.classList.toggle("focus", +e.dataset.k === this.k && !this.inResults && this.navFocus === null));
      document.querySelectorAll("#sr .card").forEach((e) => e.classList.toggle("focus", +e.dataset.i === this.r && this.inResults));
      if (this.inResults && this.results[this.r]) setBackground(this.results[this.r].id);
    },
    move(dx, dy) {
      if (this.navFocus !== null) {
        if (dy > 0) { this.navFocus = null; this.update(); return true; }
        const n = this.navFocus + dx;
        if (n < 0 || n >= TABS.length) return false;
        this.navFocus = n; renderNav(); return true;
      }
      if (this.inResults) {
        const per = Math.max(1, Math.floor($("#sr").clientWidth / ($("#sr .card")?.offsetWidth + 16 || 160)));
        let n = this.r + dx + dy * per;
        if (dx < 0 && this.r % per === 0) { this.inResults = false; this.update(); return true; }
        if (n < 0 || n >= this.results.length) return false;
        this.r = n; this.update(); return true;
      }
      const cols = 7;
      if (dy < 0 && this.k < cols) { this.navFocus = 0; this.update(); return true; }
      if (dx > 0 && (this.k % cols === cols - 1 || this.k === KEYS.length - 1) && this.results.length) { this.inResults = true; this.update(); return true; }
      const n = this.k + dx + dy * cols;
      if (n < 0 || n >= KEYS.length) return false;
      this.k = n; this.update(); return true;
    },
    ok() {
      if (this.navFocus !== null) { openTab(this.navFocus); return; }
      if (this.inResults) { const g = this.results[this.r]; if (g) push(gameScreen(g.id)); return; }
      const key = KEYS[this.k];
      if (key === "⌫") this.q = this.q.slice(0, -1);
      else if (key === "Cancella") this.q = "";
      else this.q += key === "␣" ? " " : key.toLowerCase();
      this.search();
    },
    typed(ch) { if (ch === "Backspace") this.q = this.q.slice(0, -1); else this.q += ch.toLowerCase(); this.search(); },
    hints: [["a", "Scrivi"], ["b", "Indietro"]],
  };
  return scr;
}

/* ------------------------------------------------------------------ azioni */
async function toggleFav(g) {
  const v = !g.grp.favorite;
  try { await api(`/api/game/${g.id}/flag`, { body: { field: "favorite", value: v } }); } catch (e) { return toast(e.message, true); }
  g.grp.favorite = v ? 1 : 0;
  BP.details.delete(g.id);
  beep("ok");
  toast(v ? `★ «${g.title}» nei preferiti` : `«${g.title}» tolto dai preferiti`);
  const s = cur();
  if (s.d) { s.d = null; show(); }
}

async function installGame(d) {
  try { await api(`/api/game/${d.id}/install`, { body: {} }); } catch (e) { return toast(e.message, true); }
  toast(`Apro ${d.store_name} per installare «${d.title}»`);
}

async function playGame(d) {
  try { await api(`/api/game/${d.id}/play`, { body: {} }); } catch (e) { beep("err"); return toast(e.message, true); }
  beep("ok");
  BP.running = { id: d.id, title: d.title };
  showRunning("Avvio…");
  pollRunning();
}

function showRunning(msg) {
  const o = $("#bp-running");
  o.hidden = false;
  o.innerHTML = `<div class="logo big" style="color:var(--accent);font-size:3rem">◆</div><h2>${esc(BP.running?.title || "")}</h2>`
    + `<p class="muted">${esc(msg)}</p><p class="muted">Per chiudere il gioco: tieni premuti ${glyph("back")} + ${glyph("start")} per 2 secondi</p>`;
}

async function pollRunning() {
  let r;
  try { r = await api("/api/running"); } catch { r = {}; }
  if (["starting", "extracting", "running"].includes(r.status)) {
    showRunning(r.status === "extracting" ? (r.message || "Estrazione…") : r.status === "starting" ? "Avvio…"
      : `In esecuzione con ${r.emulator} · Select+R1 per una schermata${r.shots ? ` (${r.shots})` : ""}`);
    if (BP.running && (r.shots || 0) > (BP.running.shots || 0)) toast("📷 Schermata salvata");
    if (BP.running) BP.running.shots = r.shots || 0;
    setTimeout(pollRunning, 1500);
    return;
  }
  $("#bp-running").hidden = true;
  if (BP.running) {
    if (r.status === "error") toast(r.message, true);
    else if (r.status === "finished" && r.played > 30) toast(`Sessione: ${fmtTime(r.played)}${r.shots ? ` · 📷 ${r.shots}` : ""}`);
    BP.running = null;
    try { window.pywebview?.api?.focus_window?.(); } catch { /* browser */ }
    await reloadLibrary();
  }
}

async function reloadLibrary() {
  const lib = await api("/api/library");
  BP.lib = decodeLibrary(lib);
  BP.details.clear();
  const s = cur();
  if (s.tab === "home") { BP.stack = [homeScreen()]; show(); }
}

async function exitBigPicture() {
  try { await window.pywebview?.api?.set_fullscreen?.(false); } catch { /* browser */ }
  location.href = "/";
}

/* ------------------------------------------------------------------ input: controller e tastiera */
const GLYPHS = {
  xbox: { a: "A", b: "B", x: "X", y: "Y", lb: "LB", rb: "RB", lt: "LT", rt: "RT", back: "View", start: "Menu" },
  ps: { a: "✕", b: "○", x: "□", y: "△", lb: "L1", rb: "R1", lt: "L2", rt: "R2", back: "Share", start: "Options" },
};
function glyph(k) {
  const t = GLYPHS[BP.pad][k];
  return `<span class="btn-glyph ${k}${t.length > 1 ? " rect" : ""}">${esc(t)}</span>`;
}
function renderHints() {
  const s = cur();
  const h = s.hints || [["a", "Seleziona"], ["b", "Indietro"]];
  $("#bp-hints").className = BP.pad === "ps" ? "ps" : "";
  const all = h.some(([k]) => k === "y") ? h : h.concat([["y", "Cerca"]]);
  $("#bp-hints").innerHTML = `<span class="hint" data-k="rb">${glyph("lb")}${glyph("rb")} Menù</span>`
    + all.map(([k, l]) => `<span class="hint" data-k="${k}">${glyph(k)} ${esc(l)}</span>`).join("");
}

function input(action) {
  const s = cur();
  if (!$("#bp-running").hidden) return; // durante il gioco l'interfaccia ignora i comandi
  switch (action) {
    case "left": if (s.move(-1, 0)) beep("move"); break;
    case "right": if (s.move(1, 0)) beep("move"); break;
    case "up": if (s.move(0, -1)) beep("move"); break;
    case "down": if (s.move(0, 1)) beep("move"); break;
    case "a": beep("ok"); s.ok(); break;
    case "b": if (s.navFocus !== null && s.navFocus !== undefined) { s.navFocus = null; s.update(); beep("back"); } else pop(); break;
    case "x": s.fav?.(); break;
    case "y": if (s.alt) s.alt(); else { push(searchScreen()); beep("ok"); } break;
    case "lt": if (s.lt) s.lt(); else s.jump?.(-1); break;
    case "rt": if (s.rt) s.rt(); else s.jump?.(1); break;
    case "lb": stepTab(-1); break;
    case "rb": stepTab(1); break;
    default: break;
  }
}

document.addEventListener("keydown", (e) => {
  const map = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", Enter: "a", Escape: "b",
    PageUp: "lt", PageDown: "rt", "/": "y", q: "lb", Q: "lb", e: "rb", E: "rb", " ": "a" };
  const s = cur();
  if (s.typed && (e.key.length === 1 && /[\p{L}\p{N} ]/u.test(e.key) || e.key === "Backspace")) { e.preventDefault(); s.typed(e.key); return; }
  if (e.key === "Backspace") { e.preventDefault(); input("b"); return; }
  if (e.key === "Tab") { e.preventDefault(); input(e.shiftKey ? "lb" : "rb"); return; }
  if (s.typed && e.key === " ") { e.preventDefault(); s.typed(" "); return; }
  if (e.key === "f" || e.key === "F") { input("x"); return; }
  if (map[e.key]) { e.preventDefault(); input(map[e.key]); }
});
/* ---- mouse: clic su schede, giochi, pulsanti, tasti e suggerimenti; rotella per scorrere */
$("#bp-nav").addEventListener("click", (e) => {
  const t = e.target.closest(".bp-tab");
  if (t) { beep("ok"); openTab(+t.dataset.tab); }
});
$("#bp-hints").addEventListener("click", (e) => {
  const h = e.target.closest(".hint");
  if (h) input(h.dataset.k);
});
main.addEventListener("click", (e) => {
  if (!$("#bp-running").hidden) return;
  const s = cur();
  const btn = e.target.closest(".bp-btn[data-i]");
  if (btn && s.buttons) { s.navFocus = null; s.i = +btn.dataset.i; s.update(); beep("ok"); s.ok(); return; }
  const key = e.target.closest(".kbd .k");
  if (key && "k" in s) { s.navFocus = null; s.inResults = false; s.k = +key.dataset.k; beep("ok"); s.ok(); return; }
  const c = e.target.closest(".card[data-i]");
  if (!c) return;
  s.navFocus = null;
  if (c.closest("#sr")) { s.inResults = true; s.r = +c.dataset.i; }
  else if (c.closest(".shelf")) { s.row = +c.closest(".shelf").dataset.r; s.cols[s.row] = +c.dataset.i; }
  else s.i = +c.dataset.i;
  s.update?.();
  beep("ok");
  s.ok();
});
let wheelAt = 0;
main.addEventListener("wheel", (e) => {
  e.preventDefault();
  const now = performance.now();
  if (now - wheelAt < 90 || !$("#bp-running").hidden) return;
  wheelAt = now;
  const s = cur();
  const horiz = Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.shiftKey;
  const d = Math.sign(horiz ? e.deltaX || e.deltaY : e.deltaY);
  if (!d) return;
  if (s.navFocus !== null && s.navFocus !== undefined) { s.navFocus = null; s.update?.(); }
  if (s.move?.(horiz ? d : 0, horiz ? 0 : d)) beep("move");
}, { passive: false });
// tasto "indietro" del mouse e clic destro = Indietro
document.addEventListener("mouseup", (e) => { if (e.button === 3) { e.preventDefault(); input("b"); } });
document.addEventListener("contextmenu", (e) => { e.preventDefault(); input("b"); });

document.addEventListener("mousemove", () => { $("#bp").classList.add("mouse"); clearTimeout(document.mt); document.mt = setTimeout(() => $("#bp").classList.remove("mouse"), 2500); });

// Gamepad: mappatura standard, ripetizione tenendo premuto, combinazione per chiudere il gioco
const padState = { held: {}, next: {}, comboSince: 0 };
function pollPads(ts) {
  const pads = navigator.getGamepads ? [...navigator.getGamepads()].filter(Boolean) : [];
  if (pads.length) {
    const p = pads[0];
    const kind = /054c|sony|dualsense|dualshock|wireless controller/i.test(p.id) ? "ps" : "xbox";
    if (kind !== BP.pad) { BP.pad = kind; renderHints(); }
    const b = (i) => !!p.buttons[i]?.pressed;
    const ax = p.axes[0] || 0, ay = p.axes[1] || 0;
    const dirs = { left: b(14) || ax < -0.55, right: b(15) || ax > 0.55, up: b(12) || ay < -0.55, down: b(13) || ay > 0.55,
      a: b(0), b: b(1), x: b(2), y: b(3), lb: b(4), rb: b(5), lt: b(6), rt: b(7) };
    for (const [k, on] of Object.entries(dirs)) {
      const rep = ["left", "right", "up", "down", "lt", "rt"].includes(k);
      if (on && !padState.held[k]) { padState.held[k] = true; padState.next[k] = ts + 380; input(k); }
      else if (on && rep && ts >= padState.next[k]) { padState.next[k] = ts + 85; input(k); }
      else if (!on) padState.held[k] = false;
    }
    // Select/View (8) + Start/Menu (9) tenuti premuti: chiude il gioco in esecuzione
    if (b(8) && b(9) && BP.running) {
      if (!padState.comboSince) padState.comboSince = ts;
      if (ts - padState.comboSince > 1800) { padState.comboSince = Infinity; api("/api/running/stop", { body: {} }).then(() => toast("Chiusura del gioco…")); }
    } else padState.comboSince = 0;
  }
  requestAnimationFrame(pollPads);
}
window.addEventListener("gamepadconnected", (e) => toast(`Controller collegato: ${e.gamepad.id.split("(")[0].trim()}`));

/* ------------------------------------------------------------------ avvio */
function clock() { $("#bp-clock").textContent = new Date().toLocaleTimeString("it", { hour: "2-digit", minute: "2-digit" }); }

(async function start() {
  clock(); setInterval(clock, 15000);
  applyLook(); // palette e dimensione dei testi scelte la volta prima
  watchFocus(); // lettura vocale di ciò che si seleziona (se attiva)
  try {
    const lib = await api("/api/library");
    BP.lib = decodeLibrary(lib);
  } catch (e) {
    $("#bp-loading").innerHTML = `<p>Errore: ${esc(e.message)}</p>`;
    return;
  }
  GS.library = () => BP.lib;
  GS.openGame = (id) => push(gameScreen(id));
  await loadExtensions();
  $("#bp-loading").hidden = true;
  BP.stack = [homeScreen()];
  show();
  requestAnimationFrame(pollPads);
  // se un gioco e' gia' in esecuzione (es. avviato prima di entrare)
  const r = await api("/api/running").catch(() => ({}));
  if (["starting", "extracting", "running"].includes(r.status)) { BP.running = { title: r.title }; pollRunning(); }
})();
