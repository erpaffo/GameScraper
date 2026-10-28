"use strict";
/* Pozzo Launcher - app per il telefono: libreria, avvio dei giochi sul PC, controller con giroscopio. */

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const hue = (s) => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; };
const store = {
  get(k, d) { try { const v = localStorage.getItem("gsm." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("gsm." + k, JSON.stringify(v)); } catch { /* niente */ } },
};
const GUEST = { on: false };  // ospite a distanza: solo il controller
const SHORT = { megadrive: "MD", mastersystem: "SMS", gamegear: "GG", psx: "PS1", ps2: "PS2", ps3: "PS3", psp: "PSP", psvita: "VITA",
  gc: "GC", wiiu: "WII U", switch: "SWITCH", xbox360: "X360", dreamcast: "DC", saturn: "SAT", nds: "DS", "3ds": "3DS", n64: "N64", windows: "PC" };
const STORE_SHORT = { steam: "STEAM", epic: "EPIC", gog: "GOG", amazon: "AMAZON", battlenet: "B.NET", xbox: "XBOX", minecraft: "MINECRAFT", manual: "PC" };

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "Content-Type": "application/json" }, credentials: "same-origin" };
  if (opts.cache) init.cache = opts.cache;
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.method = opts.method || "POST"; }
  const r = await fetch(path, init);
  if (r.status === 401) { if (GUEST.on) { location.href = "/g/"; throw new Error("Invito scaduto"); } showPair(); throw new Error("Telefono non abbinato"); }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch { /* */ } throw new Error(m); }
  return r.status === 204 ? null : r.json();
}

let toastT = 0;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 2600);
}

/* ------------------------------------------------------------------ abbinamento */
function showPair() {
  // niente piu' giri in sottofondo finche' il telefono non e' di nuovo abbinato
  clearTimeout(runT); clearTimeout(actT);
  $("#pair").hidden = false; $("#main").hidden = true; $("#pad").hidden = true;
  $("#pad-lobby").hidden = true;
  const code = (location.hash.match(/code=(\d{6})/) || [])[1];
  if (code) $("#pair-code").value = code;
  const ua = navigator.userAgent;
  $("#pair-name").value = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua)
    ? ((ua.match(/Android[^;]*;\s*([^;)]+)/) || [])[1] || "Android").replace(/Build.*$/, "").trim() : "Telefono";
}
$("#pair-go").onclick = async () => {
  $("#pair-err").textContent = "";
  try {
    await api("/m/api/pair", { body: { code: $("#pair-code").value, name: $("#pair-name").value } });
    history.replaceState(null, "", "/m/");
    start();
  } catch (e) { $("#pair-err").textContent = e.message; }
};

/* ------------------------------------------------------------------ libreria */
const L = { games: [], byId: new Map(), plats: new Map(), q: "", plat: store.get("plat", ""), chip: store.get("chip", "all"),
  sort: store.get("sort", "title"), shown: 0, items: [], rated: new Map(), built_at: 0 };

function decode(lib) {
  const gc = Object.fromEntries(lib.games.cols.map((c, i) => [c, i]));
  const rc = Object.fromEntries(lib.groups.cols.map((c, i) => [c, i]));
  const groups = lib.groups.rows;
  L.plats = new Map(lib.platforms.map((p) => [p.id, p.name]));
  L.built_at = lib.built_at || 0;
  L.games = lib.games.rows.map((r) => {
    const g = groups[r[gc.group]];
    return { id: r[gc.id], title: r[gc.title], sort: r[gc.sort], platform: r[gc.platform], year: r[gc.year], cover: r[gc.cover],
      store: r[gc.store] || null, installed: r[gc.installed], primary: g[rc.primary] === r[gc.id], fav: g[rc.favorite],
      hidden: g[rc.hidden], last: g[rc.last_played], playtime: g[rc.playtime], status: g[rc.status] || "",
      rating: r[gc.rating] ?? null, genre: (r[gc.genres] || "").split(",")[0].trim(), q: fold(r[gc.title]) };
  }).filter((g) => g.primary && !g.hidden && g.store !== "online");
  L.byId = new Map(L.games.map((g) => [g.id, g]));
  // copia minuscola per la Home della prossima apertura: i giochi recenti, in gioco e preferiti (vedi start)
  const top = L.games.filter((g) => g.last).sort((a, b) => b.last - a.last).slice(0, 60);
  store.set("home", [...new Set([...top, ...L.games.filter((g) => g.fav || g.status === "playing")])]);
}

function card(g) {
  const tag = g.store ? (STORE_SHORT[g.store] || "PC") : (SHORT[g.platform] || g.platform.toUpperCase());
  return `<div class="card" data-id="${g.id}"><div class="cv" style="--h:${hue(g.title)}"><div class="ph">${esc(g.title)}</div>`
    + (g.cover ? `<img loading="lazy" src="/img/${g.id}/cover?w=260" alt="" onerror="this.remove()">` : "")
    + `<span class="tag">${esc(tag)}</span></div><div class="t">${esc(g.title)}</div></div>`;
}

function renderHome() {
  const playing = L.games.filter((g) => g.status === "playing").sort((a, b) => (b.last || 0) - (a.last || 0));
  const recent = L.games.filter((g) => g.last && g.status !== "playing").sort((a, b) => b.last - a.last).slice(0, 20);
  const favs = L.games.filter((g) => g.fav).sort((a, b) => a.sort.localeCompare(b.sort)).slice(0, 30);
  const pc = L.games.filter((g) => g.store && g.installed).sort((a, b) => (b.last || 0) - (a.last || 0)).slice(0, 20);
  const row = (t, list) => (list.length ? `<h2 class="sec">${t}</h2><div class="row-scroll">${list.map(card).join("")}</div>` : "");
  const rows = row("▶ In corso", playing) + row("◷ Giocati di recente", recent) + row("★ Preferiti", favs)
    + row("🖥 Giochi PC installati", pc);
  // senza partite recenti, preferiti o giochi PC la Home non deve dire «libreria vuota»: si mostrano i titoli
  $("#v-home").innerHTML = rows || (L.games.length
    ? row("🎲 Tutti i giochi", L.games.slice().sort((a, b) => a.sort.localeCompare(b.sort)).slice(0, 30))
      + `<p class="empty small">${L.games.length} giochi: aprine uno o cerca in <b>🔍 Libreria</b>.</p>`
    : `<p class="empty">Libreria vuota: aggiungi i giochi dall'app sul PC.</p>`);
}

function filterLib() {
  const toks = fold(L.q).split(/\s+/).filter(Boolean);
  let it = L.games;
  if (L.plat) it = it.filter((g) => (L.plat === "pc" ? !!g.store : g.platform === L.plat && !g.store));
  if (L.chip === "fav") it = it.filter((g) => g.fav);
  else if (L.chip === "playing") it = it.filter((g) => g.status === "playing");
  else if (L.chip === "backlog") it = it.filter((g) => g.status === "backlog");
  else if (L.chip === "recent") it = it.filter((g) => g.last);
  if (toks.length) it = it.filter((g) => toks.every((t) => g.q.includes(t)));
  const byTitle = (a, b) => a.sort.localeCompare(b.sort);
  const sorters = {
    "-year": (a, b) => (b.year || 0) - (a.year || 0) || byTitle(a, b),
    year: (a, b) => (a.year || 9999) - (b.year || 9999) || byTitle(a, b),
    "-rating": (a, b) => (b.rating ?? -1) - (a.rating ?? -1) || byTitle(a, b),
    playtime: (a, b) => (b.playtime || 0) - (a.playtime || 0) || byTitle(a, b),
    last: (a, b) => (b.last || 0) - (a.last || 0) || byTitle(a, b),
    title: byTitle,
  };
  it = it.slice().sort(sorters[L.sort] || byTitle);
  L.items = it; L.shown = 0;
  $("#grid").innerHTML = it.length ? "" : `<p class="empty">Nessun gioco trovato.</p>`;
  moreLib();
}
function moreLib() {
  const next = L.items.slice(L.shown, L.shown + 60);
  L.shown += next.length;
  $("#grid").insertAdjacentHTML("beforeend", next.map(card).join(""));
}
new IntersectionObserver((e) => { if (e[0].isIntersecting && L.shown < L.items.length) moreLib(); }, { rootMargin: "600px" }).observe($("#more"));

function renderLibTools() {
  const plats = [...new Set(L.games.filter((g) => !g.store).map((g) => g.platform))].sort((a, b) => (L.plats.get(a) || a).localeCompare(L.plats.get(b) || b));
  $("#plat").innerHTML = `<option value="">Tutte</option>` + (L.games.some((g) => g.store) ? `<option value="pc">PC</option>` : "")
    + plats.map((p) => `<option value="${esc(p)}">${esc(L.plats.get(p) || p)}</option>`).join("");
  // il filtro salvato puo' non esistere piu' (console tolta, libreria ricostruita): si torna a «Tutte»
  if (![...$("#plat").options].some((o) => o.value === L.plat)) { L.plat = ""; store.set("plat", ""); }
  $("#plat").value = L.plat;
  const chips = [["all", "Tutti"], ["recent", "Recenti"], ["fav", "★ Preferiti"], ["playing", "▶ In corso"], ["backlog", "📋 Da giocare"]];
  $("#lib-chips").innerHTML = chips.map(([k, l]) => `<button data-chip="${k}" class="${L.chip === k ? "on" : ""}">${l}</button>`).join("");
  // ordinamento: titolo, anno, voto, tempo di gioco, ultimo avviato
  const SORTS = [["title", "Titolo A–Z"], ["-year", "Anno ↓"], ["year", "Anno ↑"], ["-rating", "Valutazione"],
    ["playtime", "Tempo di gioco"], ["last", "Ultimo avviato"]];
  $("#sort").innerHTML = SORTS.map(([k, l]) => `<option value="${k}">${l}</option>`).join("");
  $("#sort").value = L.sort;
}

/* ------------------------------------------------------------------ scheda del gioco */
async function openGame(id) {
  const g = L.byId.get(id);
  const box = $(".sheet-box");
  $("#sheet").hidden = false;
  box.innerHTML = `<div class="g-head">${g && g.cover
    ? `<img src="/img/${id}/cover?w=300" alt="" onerror="this.remove()">`
    : `<div class="g-nocover">${esc(g ? g.title : "")}</div>`}<div><h2>${esc(g ? g.title : "")}</h2>
    <div class="muted" id="g-meta">…</div></div></div><div class="g-btns" id="g-btns"></div><div class="g-desc" id="g-desc"></div>`;
  let d;
  try { d = await api(`/api/game/${id}`); } catch (e) { return toast(e.message, true); }
  const pc = !!d.store;
  $("#g-meta").innerHTML = esc([pc ? d.store_name : d.platform_name, d.year].filter(Boolean).join(" · "))
    + (d.user.playtime ? `<br>${Math.round(d.user.playtime / 3600 * 10) / 10} h giocate` : "")
    + (d.emulator ? `<br><span class="small">${esc(d.emulator)}</span>` : "");
  const canPlay = pc ? d.installed : !!d.emulator;
  $("#g-btns").innerHTML = canPlay
    ? `<button class="btn primary big" data-act="play">▶ Gioca sul PC</button><button class="btn big" data-act="playpad">🎮 Gioca con il telefono come controller</button>`
      + `<button class="btn big" data-act="here">📱 Gioca su questo telefono (Moonlight)</button>`
    : `<p class="muted">${pc ? "Non installato sul PC" : "Nessun emulatore configurato per questa console"}</p>`;
  const ST = { backlog: "📋 Da giocare", playing: "▶ In corso", done: "✔ Finito", dropped: "✖ Abbandonato" };
  $("#g-btns").insertAdjacentHTML("beforeend", `<button class="btn" data-act="fav">${d.user.favorite ? "★ Nei preferiti" : "☆ Aggiungi ai preferiti"}</button>`
    + `<div class="g-status">${Object.entries(ST).map(([k, l]) => `<button class="btn${d.user.status === k ? " primary" : ""}" data-act="status" data-st="${k}">${l}</button>`).join("")}</div>`
    + (d.user.last_played ? `<button class="btn" data-act="unrecent">✖ Togli da «Giocati di recente»</button>` : ""));
  $("#g-desc").textContent = d.description || "";
  $("#g-btns").onclick = async (e) => {
    const a = e.target.dataset.act;
    if (a === "here") {
      try { await api(`/api/game/${id}/play`, { body: {} }); } catch (err) { return toast(err.message, true); }
      pollRun(true);
      showMoonlightSteps(d.title);
    } else if (a === "play" || a === "playpad") {
      try { await api(`/api/game/${id}/play`, { body: {} }); } catch (err) { return toast(err.message, true); }
      toast(`Avvio di «${d.title}» sul PC…`);
      closeSheet();
      pollRun(true);
      if (a === "playpad") openPad();
    } else if (a === "fav") {
      await api(`/api/game/${id}/flag`, { body: { field: "favorite", value: !d.user.favorite } });
      if (g) g.fav = !d.user.favorite;
      openGame(id);
    } else if (a === "status") {
      const st = d.user.status === e.target.dataset.st ? "" : e.target.dataset.st; // di nuovo lo stesso = togli
      try { await api(`/api/game/${id}/flag`, { body: { field: "status", value: st } }); } catch (err) { return toast(err.message, true); }
      if (g) g.status = st;
      openGame(id);
    } else if (a === "unrecent") {
      try { await api(`/api/game/${id}/flag`, { body: { field: "last_played", value: null } }); } catch (err) { return toast(err.message, true); }
      if (g) g.last = 0;
      toast("Tolto da «Giocati di recente»");
      openGame(id);
    }
  };
}
/* Moonlight non accetta link dalle pagine web verso un gioco preciso (su iOS nessuno schema URL, su Android il
   collegamento ai giochi non e' raggiungibile dal browser): il gioco parte sul PC e in Moonlight si entra nell'app
   «▶ Gioco in corso», che mostra lo schermo del PC. */
function showMoonlightSteps(title) {
  const android = /Android/i.test(navigator.userAgent);
  const store = android ? "https://play.google.com/store/apps/details?id=com.limelight"
    : "https://apps.apple.com/app/moonlight-game-streaming/id1000551566";
  $(".sheet-box").innerHTML = `<h2>📱 ${esc(title)}</h2>
    <p>Il gioco si sta avviando sul PC. Per giocarlo qui:</p>
    <ol class="steps">
      <li>Apri <b>Moonlight</b>${android ? ` (<a href="intent://#Intent;package=com.limelight;S.browser_fallback_url=${encodeURIComponent(store)};end">apri</a>)` : ""}.</li>
      <li>Tocca il tuo <b>PC</b>.</li>
      <li>Scegli <b>▶ Gioco in corso</b>: sei nel gioco. Comandi: tasti a schermo di Moonlight o un controller Bluetooth collegato al telefono.</li>
    </ol>
    <p class="muted small">La prima volta: in Pozzo Launcher sul PC premi «📡 Pubblica in Moonlight» (Impostazioni › Telefono e streaming),
      altrimenti «▶ Gioco in corso» non compare. Non hai Moonlight? <a href="${store}" target="_blank" rel="noopener">Installalo</a>.</p>
    <button class="btn primary big" id="moon-ok">Ok</button>`;
  $("#moon-ok").onclick = closeSheet;
}
function showSheet(html) { $("#sheet").hidden = false; $(".sheet-box").innerHTML = html; }
function closeSheet() { $("#sheet").hidden = true; }
$(".sheet-bg").onclick = closeSheet;

/* ------------------------------------------------------------------ gioco in esecuzione */
let runT = 0, RUN = {};
async function pollRun(soon = false) {
  clearTimeout(runT);
  try { RUN = await api("/api/running"); } catch { RUN = {}; }
  const active = ["starting", "extracting", "running"].includes(RUN.status);
  const m = $("#run-mini");
  m.hidden = !active;
  if (active) {
    m.innerHTML = `<span class="dot"></span><b>${esc(RUN.title)}</b><button data-run="pad">🎮</button><button data-run="shot">📷</button><button data-run="stop">✕</button>`;
    showPlayer();
  }
  if (!$("#pad").hidden) applyLayout(); // gioco cambiato: aspetto automatico (PlayStation, Switch)
  runT = setTimeout(pollRun, active || soon ? 2000 : 6000);
}
$("#run-mini").onclick = async (e) => {
  const a = e.target.dataset.run;
  if (a === "pad") openPad();
  else if (a === "shot") { await api("/api/running/screenshot", { body: {} }); toast("📷 Schermata salvata sul PC"); }
  else if (a === "stop" && confirm(`Chiudere «${RUN.title}» sul PC?`)) { await api("/api/running/stop", { body: {} }); pollRun(); }
};

/* ------------------------------------------------------------------ controller */
const BTN = { up: 0x0001, down: 0x0002, left: 0x0004, right: 0x0008, start: 0x0010, back: 0x0020, ls: 0x0040, rs: 0x0080,
  lb: 0x0100, rb: 0x0200, guide: 0x0400, a: 0x1000, b: 0x2000, x: 0x4000, y: 0x8000 };
const PAD = { ws: null, player: 0, buttons: 0, lt: 0, rt: 0, lx: 0, ly: 0, rx: 0, ry: 0, dirty: true, lastSend: 0, lastMotion: 0,
  pointers: new Map(), aim: { x: 0, y: 0 }, wake: null, retry: 0 };
const SET = {
  gyro: store.get("gyro", "off"), sens: store.get("sens", 1), invx: store.get("invx", false), invy: store.get("invy", false),
  vib: store.get("vib", true), scale: store.get("scale", 1),
  src: store.get("src", "auto"), // auto: controller collegato se c'e', altrimenti tasti a schermo | touch | gamepad
  layout: store.get("layout", "xbox"), // aspetto dei tasti a schermo: xbox | ps | switch
  nlabel: store.get("nlabel", false), // Switch: true = la lettera A manda A; false = stessa posizione di un controller Xbox
  rumble: store.get("rumble", true), // vibrazione quando il gioco fa vibrare il controller
  autoLayout: store.get("autoLayout", true), // aspetto scelto dalla console del gioco in corso (PlayStation, Switch)
};

/* ---- vibrazioni del gioco (motori del controller, 0-255). Il browser non regola l'intensita': impulsi piu' lunghi
   per una vibrazione piu' forte. Safari su iPhone/iPad non permette la vibrazione alle pagine web. */
const RUMBLE = { k: 0, timer: 0 };
function rumble(large, small) {
  RUMBLE.k = Math.max(large, small) / 255;
  clearInterval(RUMBLE.timer);
  if (!SET.rumble || !navigator.vibrate) return;
  if (RUMBLE.k < 0.05) { try { navigator.vibrate(0); } catch { /* */ } return; }
  const tick = () => { try { navigator.vibrate(Math.round(25 + 75 * RUMBLE.k)); } catch { /* */ } };
  tick();
  RUMBLE.timer = setInterval(tick, 110);
}

/* ---- aspetto del controller a schermo. Gli elementi restano quelli Xbox (classe = posizione: .fb.a in basso,
   .fb.b a destra, .fb.x a sinistra, .fb.y in alto); cambiano scritte, colori, disposizione e, su richiesta, il tasto inviato. */
const LAYOUTS = {
  xbox: { shoulder: { lt: "LT", lb: "LB", rb: "RB", rt: "RT" }, face: { a: "A", b: "B", x: "X", y: "Y" },
    mid: { back: "⧉", guide: "◆", start: "☰" } },
  ps: { shoulder: { lt: "L2", lb: "L1", rb: "R1", rt: "R2" }, face: { a: "✕", b: "○", x: "□", y: "△" },
    mid: { back: "Create", guide: "PS", start: "Options" } },
  // posizioni Nintendo: in basso B, a destra A, a sinistra Y, in alto X
  switch: { shoulder: { lt: "ZL", lb: "L", rb: "R", rt: "ZR" }, face: { a: "B", b: "A", x: "Y", y: "X" },
    mid: { back: "−", guide: "⌂", start: "+" } },
  // GameCube: A grande al centro, B in basso a sinistra, X a destra, Y in alto; i tasti mandano sempre la loro lettera
  gc: { shoulder: { lt: "L", lb: "—", rb: "Z", rt: "R" }, face: { a: "A", b: "X", x: "B", y: "Y" },
    mid: { back: "⧉", guide: "⌂", start: "START" } },
};
/* aspetti speciali: un pannello proprio al posto dei tasti standard. portrait = telefono in verticale,
   both = vanno bene tutte e due le posizioni (DS/3DS);
   motion = cosa fa il giroscopio (dsu: movimento agli emulatori, wheel: sterzo sulla levetta sinistra) */
const SPECIAL = {
  buzz: { portrait: true }, wii: { portrait: true, motion: "dsu" }, wiis: { motion: "dsu" }, nunchuk: { motion: "dsu" },
  wheel: { motion: "wheel" }, guitar: {}, arcade: {}, gun: {}, mic: { portrait: true },
  // DS/3DS: verticale (schermo sopra, comandi sotto) oppure orizzontale (comandi sopra lo schermo)
  ds: { both: true },
};
const WII = new Set(["wii", "wiis", "nunchuk"]);
// console o titolo del gioco in corso -> aspetto (se «automatico» e' attivo e l'aspetto scelto e' un controller normale)
const AUTO_LAYOUT = { psx: "ps", ps2: "ps", ps3: "ps", psp: "ps", psvita: "ps", switch: "switch", gc: "gc", wii: "wii" };
const AUTO_TITLE = [[/\bbuzz\b/i, "buzz"], [/guitar hero|rock band/i, "guitar"], [/singstar/i, "mic"]];
function effLayout() {
  const pid = (RUN.key || "").split("/")[0];
  const active = ["starting", "extracting", "running"].includes(RUN.status);
  if (active && RUN.dual) return "ds";  // DS/3DS: il PC ha avviato il gioco con lo schermo inferiore sul telefono
  if (!SET.autoLayout || !active || !LAYOUTS[SET.layout]) return SET.layout;
  const t = AUTO_TITLE.find(([re]) => re.test(RUN.title || ""));
  return (t && t[1]) || AUTO_LAYOUT[pid] || SET.layout;
}
const gyroMode = () => (SPECIAL[$("#pad").dataset.lay] || {}).motion || SET.gyro;
/* ---- disposizione personalizzata: ogni gruppo di tasti si sposta e si ridimensiona; salvata per aspetto (xbox/ps/switch),
   posizioni in % dello schermo cosi' valgono anche ruotando o cambiando dispositivo */
const MOVABLE = { l2: ".shoulder.l2", l1: ".shoulder.l1", r1: ".shoulder.r1", r2: ".shoulder.r2", ls: "#ls", rs: "#rs",
  dpad: ".dpad", face: ".face", mid: ".mid" };
const EDIT = { on: false, sel: null, drag: null };
function customPos(lay) { return store.get(`padpos_${lay}`, {}); }
function applyCustom(lay) {
  const saved = customPos(lay);
  for (const [k, sel] of Object.entries(MOVABLE)) {
    const el = $(sel, $("#pad"));
    for (const p of ["left", "top", "right", "bottom", "transform", "scale"]) el.style[p] = "";
    const c = saved[k];
    if (!c) continue;
    Object.assign(el.style, { left: c.x + "%", top: c.y + "%", right: "auto", bottom: "auto", transform: "none" });
    if (c.k && c.k !== 1) el.style.scale = c.k;
  }
}
function saveCustom(k, patch) {
  const lay = $("#pad").dataset.lay, all = customPos(lay);
  all[k] = { ...(all[k] || {}), ...patch };
  store.set(`padpos_${lay}`, all);
  pushPad();
}
function editKey(el) { return Object.keys(MOVABLE).find((k) => el.matches(MOVABLE[k])); }
function startEdit() {
  EDIT.on = true;
  $("#pad").classList.add("editing");
  $("#pad-edit").hidden = false;
  selectEdit(null);
}
function stopEdit() {
  EDIT.on = false; EDIT.drag = null;
  $("#pad").classList.remove("editing");
  $("#pad-edit").hidden = true;
  selectEdit(null);
}
function selectEdit(k) {
  EDIT.sel = k;
  document.querySelectorAll("#pad .edit-sel").forEach((e) => e.classList.remove("edit-sel"));
  $("#pe-size").disabled = !k;
  if (!k) { $("#pe-sel").textContent = "Trascina un tasto per spostarlo"; return; }
  const el = $(MOVABLE[k], $("#pad"));
  el.classList.add("edit-sel");
  $("#pe-size").value = customPos($("#pad").dataset.lay)[k]?.k || 1;
  $("#pe-sel").textContent = { l2: "Grilletto sinistro", l1: "Dorsale sinistro", r1: "Dorsale destro", r2: "Grilletto destro",
    ls: "Levetta sinistra", rs: "Levetta destra", dpad: "Croce", face: "Tasti azione", mid: "Tasti centrali" }[k];
}
// in modifica i tocchi spostano i tasti invece di premerli (ascoltatori in fase di cattura: arrivano prima del gioco)
function editDown(e) {
  if (!EDIT.on) return;
  e.preventDefault(); e.stopImmediatePropagation();
  const el = e.target.closest(Object.values(MOVABLE).join(","));
  if (!el) return selectEdit(null);
  const k = editKey(el);
  selectEdit(k);
  const r = el.getBoundingClientRect();
  EDIT.drag = { k, el, id: e.pointerId, dx: e.clientX - r.left, dy: e.clientY - r.top };
  try { $("#pad-area").setPointerCapture(e.pointerId); } catch { /* */ }
}
function editMove(e) {
  if (!EDIT.on) return;
  e.stopImmediatePropagation();
  const d = EDIT.drag;
  if (!d || d.id !== e.pointerId) return;
  const A = $("#pad-area").getBoundingClientRect();
  const x = Math.max(0, Math.min(95, (e.clientX - d.dx - A.left) / A.width * 100));
  const y = Math.max(0, Math.min(95, (e.clientY - d.dy - A.top) / A.height * 100));
  Object.assign(d.el.style, { left: x + "%", top: y + "%", right: "auto", bottom: "auto", transform: "none" });
  d.x = x; d.y = y;
}
function editUp(e) {
  if (!EDIT.on) return;
  e.stopImmediatePropagation();
  const d = EDIT.drag;
  if (d && d.id === e.pointerId && d.x != null) saveCustom(d.k, { x: +d.x.toFixed(2), y: +d.y.toFixed(2) });
  EDIT.drag = null;
}

/* orientamento: gli aspetti «verticale» si bloccano in verticale, gli altri in orizzontale; il DS/3DS resta libero
   (both) cosi' si puo' passare da verticale a orizzontale mentre si gioca */
function lockOrientation(sp) {
  const want = sp && sp.both ? null : sp && sp.portrait ? "portrait" : "landscape";
  try {
    if (want) screen.orientation?.lock?.(want).catch(() => {});
    else screen.orientation?.unlock?.();
  } catch { /* non supportato: si chiede di ruotare */ }
}

function applyLayout() {
  const lay = effLayout();
  const pad = $("#pad");
  const prev = pad.dataset.lay;
  pad.dataset.lay = lay;
  pad.className = pad.className.replace(/\blay-\S+/g, "").trim();
  pad.classList.add(`lay-${lay}`);
  const sp = SPECIAL[lay];
  pad.classList.toggle("special", !!sp);
  pad.classList.toggle("portrait", !!(sp && sp.portrait));
  pad.classList.toggle("free", !!(sp && sp.both));  // nessun blocco di rotazione: vanno bene verticale e orizzontale
  document.querySelectorAll("#pad .sp").forEach((e) => e.classList.toggle("on", e.id === `sp-${lay}`));
  $("#pad-rotate").textContent = sp && sp.portrait ? "Tieni il telefono in verticale 🔄" : "Ruota il telefono in orizzontale 🔄";
  if (prev !== lay) {
    releaseAll();
    showSource();
    if (!$("#pad").hidden) {
      lockOrientation(sp);
      if (sp && sp.motion) enableMotion();
      if (lay === "wheel") WHEEL.base = null;
      if (prev === "mic") micStop();
    }
    if (prev === "ds") dsStop();
    if (lay === "ds") dsStart();
  }
  if (lay === "gun") { gunFit(); $("#gun-click").textContent = `👆 Clic al tocco: ${GUN.click ? "sì" : "no"}`; }
  if (sp) return;
  const L = LAYOUTS[lay] || LAYOUTS.xbox;
  for (const [pos, label] of Object.entries(L.face)) {
    const el = $(`.fb.${pos}`, pad);
    el.textContent = label;
    // per lettera: il tasto con scritto «A» manda A anche se e' nella posizione del B Xbox
    el.dataset.btn = (lay === "switch" && SET.nlabel) || lay === "gc" ? label.toLowerCase() : pos;
  }
  for (const [btn, label] of Object.entries(L.shoulder)) $(`.shoulder[data-btn="${btn}"]`, pad).textContent = label;
  for (const [btn, label] of Object.entries(L.mid)) $(`.mid [data-btn="${btn}"]`, pad).textContent = label;
  applyCustom(lay);
}
// cambiando aspetto si rilascia tutto (un tasto premuto non deve restare premuto sul PC)
function releaseAll() {
  for (const p of PAD.pointers.values()) p.el && p.el.classList.remove("down");
  PAD.pointers.clear();
  document.querySelectorAll("#pad .down").forEach((e) => e.classList.remove("down"));
  PAD.buttons = 0; PAD.lt = PAD.rt = PAD.lx = PAD.ly = PAD.rx = PAD.ry = 0; PAD.dirty = true;
  if (GUN.down) gunSend(GUN.x, GUN.y, false);
}
const GYRO_LABEL = { off: "🌀 Off", dsu: "🌀 Mov", aim: "🎯 Mira" };
const SRC_LABEL = { auto: "🎮 Auto", touch: "👆 Dito", gamepad: "🎮 Pad" };

/* ---- controller vero collegato al telefono/iPad (Bluetooth): Gamepad API, schema "standard" */
const GP_MAP = [["a", 0], ["b", 1], ["x", 2], ["y", 3], ["lb", 4], ["rb", 5], ["back", 8], ["start", 9], ["ls", 10], ["rs", 11],
  ["up", 12], ["down", 13], ["left", 14], ["right", 15], ["guide", 16]];
function firstGamepad() {
  try { return [...(navigator.getGamepads?.() || [])].find((g) => g && g.connected) || null; } catch { return null; }
}
/** Stato del controller nel formato del PC (Y positivo = su, come le levette a schermo). */
function readGamepad(gp) {
  const pressed = (i) => !!gp.buttons[i] && (gp.buttons[i].pressed || gp.buttons[i].value > 0.5);
  let b = 0;
  for (const [n, i] of GP_MAP) if (pressed(i)) b |= BTN[n];
  const ax = (i) => { const v = gp.axes[i] || 0; return Math.abs(v) < 0.08 ? 0 : Math.max(-1, Math.min(1, v)); };
  return { b, lt: gp.buttons[6]?.value || 0, rt: gp.buttons[7]?.value || 0, lx: ax(0), ly: -ax(1), rx: ax(2), ry: -ax(3) };
}
// con un aspetto speciale (Buzz, volante, microfono...) in «Auto» restano i tasti a schermo anche con un controller collegato
function hwActive() { return (SET.src === "gamepad" || (SET.src === "auto" && !SPECIAL[$("#pad").dataset.lay])) && !!firstGamepad(); }
function showSource() {
  const gp = hwActive() ? firstGamepad() : null;
  $("#pad-src").textContent = SRC_LABEL[SET.src];
  $("#pad").classList.toggle("hw", !!gp);
  $("#pad-hw").hidden = !gp;
  if (gp) $("#pad-hw-name").textContent = gp.id.replace(/\s*\(.*?\)\s*/g, " ").trim() || "Controller collegato";
  else if (SET.src === "gamepad" && !$("#pad").hidden) $("#pad-info").textContent = "Collega un controller al telefono (Bluetooth) e premi un tasto";
}
window.addEventListener("gamepadconnected", (e) => { if (SET.src !== "touch") toast(`🎮 ${e.gamepad.id.split("(")[0].trim()} collegato`); showSource(); PAD.dirty = true; });
window.addEventListener("gamepaddisconnected", () => { showSource(); PAD.buttons = 0; PAD.lt = PAD.rt = PAD.lx = PAD.ly = PAD.rx = PAD.ry = 0; PAD.dirty = true; });

async function openPad() {
  $("#pad").hidden = false;
  document.body.classList.add("pad-on");  // il gioco non deve scorrere sotto il controller
  $("#pad").style.setProperty("--s", SET.scale);
  $("#pad-gyro").textContent = GYRO_LABEL[SET.gyro];
  try { await document.documentElement.requestFullscreen?.({ navigationUI: "hide" }); } catch { /* iOS non lo supporta */ }
  const sp = SPECIAL[effLayout()];
  lockOrientation(sp);
  try { PAD.wake = await navigator.wakeLock?.request("screen"); } catch { /* */ }
  if (SET.gyro !== "off" || (sp && sp.motion)) await enableMotion();
  applyLayout();
  if (effLayout() === "wheel") WHEEL.base = null;
  if (effLayout() === "ds") dsStart();  // riaprendo il controller: lo schermo torna sul telefono
  showSource();
  connectPad();
}
function closePad() {
  releaseAll();
  micStop();
  dsStop();  // lo schermo inferiore torna sul PC
  $("#pad").hidden = true;
  document.body.classList.remove("pad-on");
  $("#pad-lobby").hidden = true;
  $("#pad-settings").hidden = true;
  rumble(0, 0);
  if (PAD.ws) { PAD.ws.onclose = null; PAD.ws.close(); PAD.ws = null; }
  try { document.exitFullscreen?.(); } catch { /* */ }
  try { screen.orientation?.unlock?.(); } catch { /* */ }
  try { PAD.wake?.release(); } catch { /* */ }
  if (!$("#main").hidden) tab("home");  // si torna alla schermata Home
}
function connectPad() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/m/ws`);
  PAD.ws = ws;
  $("#pad-info").textContent = "Collegamento…";
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.t === "ok") {
      PAD.player = m.player; PAD.retry = 0;
      if (m.screen) { GUN.ratio = m.screen[0] / m.screen[1]; if ($("#pad").dataset.lay === "gun") gunFit(); }
      showPlayer();
      if (gyroMode() === "dsu" && !m.dsu) toast(m.dsu_error || "Giroscopio non disponibile sul PC", true);
      PAD.dirty = true;
    } else if (m.t === "players") {
      const moved = PAD.player && PAD.player !== m.me;
      PAD.player = m.me; PAD.slot = m.slot; PAD.list = m.list;
      showPlayer();
      if (moved) { toast(`Ora sei il giocatore ${m.me}`); vib(); }
      if (!$("#pad-lobby").hidden) renderLobby();
    } else if (m.t === "rumble") rumble(m.l || 0, m.s || 0);
    else if (m.t === "err") toast(m.msg, true);
  };
  ws.onclose = () => {
    if ($("#pad").hidden) return;
    $("#pad-info").textContent = "Scollegato: nuovo tentativo…";
    setTimeout(() => { if (!$("#pad").hidden) connectPad(); }, Math.min(5000, 500 * ++PAD.retry));
  };
}
function send(obj) { if (PAD.ws && PAD.ws.readyState === 1) PAD.ws.send(JSON.stringify(obj)); }

/* ---- impostazioni del pad salvate sul PC (non nel browser): valgono per tutti i telefoni e finiscono nei backup.
   Restano solo in locale le cose legate a QUESTO telefono (dimensione dei tasti, vibrazione alla pressione,
   sorgente dei comandi), che dipendono dal suo schermo e dai suoi accessori. */
const PADKEYS = ["gyro", "sens", "invx", "invy", "rumble", "layout", "nlabel", "autoLayout"];
let padPushT = 0;
function pushPad() {
  clearTimeout(padPushT);
  padPushT = setTimeout(() => {
    const pos = {};
    for (const lay of [...Object.keys(LAYOUTS), ...Object.keys(SPECIAL)]) {
      const p = store.get(`padpos_${lay}`, {});
      if (p && Object.keys(p).length) pos[lay] = p;
    }
    const body = { pos };
    for (const k of PADKEYS) body[k] = SET[k];
    api("/m/api/pad", { body }).catch(() => { /* senza rete resta la copia locale */ });
  }, 700);
}
async function pullPad() {
  let d;
  try { d = await api("/m/api/pad"); } catch { return; }  // non abbinato o offline: si usano i valori locali
  for (const k of PADKEYS) if (d[k] !== undefined) SET[k] = d[k];
  for (const k of PADKEYS) store.set(k, SET[k]);
  for (const [lay, pos] of Object.entries(d.pos || {})) store.set(`padpos_${lay}`, pos);
}

/* ---- sala giocatori: numero e colore del giocatore (come i LED dei controller), scambio dei posti */
const PCOLOR = ["#4aa3ff", "#ff5f5f", "#3ddc84", "#f5c518"];
function showPlayer() {
  const p = PAD.player;
  $("#pad-info").innerHTML = (p ? `<b class="pl" style="background:${PCOLOR[(p - 1) % 4]}">G${p}</b> ` : "")
    + esc(["starting", "extracting", "running"].includes(RUN.status) ? RUN.title : "");
}
function renderLobby() {
  const list = PAD.list || [];
  $("#lobby-list").innerHTML = [0, 1, 2, 3].map((slot) => {
    const p = list.find((x) => x.slot === slot);
    const me = slot === PAD.slot;
    const n = p ? p.player : slot + 1;
    return `<button data-slot="${slot}" class="${me ? "me" : ""}" style="border-color:${PCOLOR[(n - 1) % 4]}">`
      + `<b style="color:${PCOLOR[(n - 1) % 4]}">Giocatore ${n}</b><br>${p ? esc(p.name) + (me ? " (tu)" : "") : `<span class="muted">libero</span>`}</button>`;
  }).join("");
}
$("#pad-players").onclick = () => { renderLobby(); $("#pad-lobby").hidden = false; };
$("#lobby-close").onclick = () => { $("#pad-lobby").hidden = true; };
$("#lobby-list").onclick = (e) => {
  const b = e.target.closest("[data-slot]");
  if (b && +b.dataset.slot !== PAD.slot) send({ t: "swap", slot: +b.dataset.slot });
};

/* ---- volante: inclinando il telefono (rotazione sullo schermo) si muove la levetta sinistra; «Centra» = posizione dritta */
const WHEEL = { base: null, max: 45 };
function wheelSteer(gx, gy) {
  const a = Math.atan2(gx, gy) * 180 / Math.PI;  // il segno dei sensori (iOS e Android opposti) si annulla con la base
  if (WHEEL.base === null) WHEEL.base = a;
  let d = a - WHEEL.base;
  d = ((d + 540) % 360) - 180;
  const v = Math.max(-1, Math.min(1, (SET.invx ? d : -d) / (WHEEL.max / SET.sens)));
  PAD.lx = Math.abs(v) < 0.03 ? 0 : v; PAD.dirty = true;
  $("#wh-rim").style.transform = `rotate(${PAD.lx * WHEEL.max}deg)`;
}
$("#wh-center").onclick = () => { WHEEL.base = null; toast("Volante centrato"); };

/* ---- cursori (pedali del volante, leva della chitarra): piu' in alto si tocca, piu' e' premuto */
function sliderMove(p, y) {
  const r = p.el.getBoundingClientRect();
  const min = +(p.el.dataset.min || 0);
  const v = Math.max(min, Math.min(1, min + (1 - min) * (1 - (y - r.top) / r.height)));
  PAD[p.el.dataset.axis] = v; PAD.dirty = true;
}

/* ---- DS/3DS: lo schermo inferiore arriva dal PC (un JPEG per fotogramma), il dito fa da pennino */
const DSS = { ws: null, url: null, last: 0, x: 0, y: 0 };
function dsStart() {
  if (DSS.ws) return;
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/m/screen`);
  ws.binaryType = "blob";
  DSS.ws = ws;
  ws.onmessage = (e) => {
    if (typeof e.data === "string") {
      const m = JSON.parse(e.data);
      if (m.t === "screen" && !m.on) { $("#ds-hint").hidden = false; $("#ds-img").removeAttribute("src"); }
      return;
    }
    const u = URL.createObjectURL(e.data);
    $("#ds-img").src = u;
    if (DSS.url) URL.revokeObjectURL(DSS.url);
    DSS.url = u;
    $("#ds-hint").hidden = true;
  };
  ws.onclose = () => {  // rete o PC: si riprova finche' si e' sull'aspetto DS
    DSS.ws = null;
    if ($("#pad").dataset.lay === "ds" && !$("#pad").hidden) setTimeout(dsStart, 1500);
  };
}
function dsStop() {
  if (DSS.ws) { DSS.ws.onclose = null; DSS.ws.close(); DSS.ws = null; }
}
/* riquadro dove si vede davvero lo schermo: in orizzontale l'immagine sta dentro l'area con le bande nere ai lati,
   quindi il tocco si misura sull'immagine (object-fit: contain) e non sull'area intera */
function dsImageRect() {
  const area = $("#ds-area").getBoundingClientRect();
  const img = $("#ds-img");
  const nw = img.naturalWidth, nh = img.naturalHeight;
  if (!nw || !nh) return area;
  if (getComputedStyle(img).objectFit !== "contain") return area;
  const k = Math.min(area.width / nw, area.height / nh);
  const w = nw * k, h = nh * k;
  return { left: area.left + (area.width - w) / 2, top: area.top + (area.height - h) / 2, width: w, height: h };
}
function dsTouch(e, down, force = false) {
  const r = dsImageRect();
  if (e) {
    DSS.x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    DSS.y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
  }
  const now = performance.now();
  if (!force && now - DSS.last < 16) return;  // al massimo ~60 al secondo
  DSS.last = now;
  if (DSS.ws && DSS.ws.readyState === 1) DSS.ws.send(JSON.stringify({ t: "touch", x: +DSS.x.toFixed(4), y: +DSS.y.toFixed(4), d: down ? 1 : 0 }));
}

/* ---- pistola ottica / touchpad: posizione assoluta sullo schermo del PC, il dito tiene premuto il tasto sinistro */
const GUN = { ratio: 16 / 9, click: store.get("gunclick", true), down: false, right: false, x: 0.5, y: 0.5, last: 0 };
function gunFit() {  // area con le proporzioni dello schermo del PC, centrata nello spazio a sinistra dei tasti
  const W = innerWidth * 0.75, H = innerHeight * 0.84;
  const w = Math.min(W, H * GUN.ratio), h = w / GUN.ratio;
  Object.assign($("#gun-area").style, { width: w + "px", height: h + "px", right: "auto", bottom: "auto",
    left: innerWidth * 0.03 + (W - w) / 2 + "px", top: innerHeight * 0.12 + (H - h) / 2 + "px" });
}
addEventListener("resize", () => { if ($("#pad").dataset.lay === "gun") gunFit(); });
function gunSend(x, y, down, force = false) {
  GUN.x = x; GUN.y = y; GUN.down = down;
  const now = performance.now();
  if (!force && down === GUN.sentDown && now - GUN.last < 12) return;  // al massimo ~80 al secondo
  GUN.last = now; GUN.sentDown = down;
  send({ t: "mouse", x: +x.toFixed(4), y: +y.toFixed(4), l: down && GUN.click ? 1 : 0, r: GUN.right ? 1 : 0 });
}
function gunPos(e) {
  const r = $("#gun-area").getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
  const dot = $("#gun-dot");
  dot.style.left = x * 100 + "%"; dot.style.top = y * 100 + "%";
  return [x, y];
}
$("#gun-click").onclick = () => {
  GUN.click = !GUN.click; store.set("gunclick", GUN.click);
  $("#gun-click").textContent = `👆 Clic al tocco: ${GUN.click ? "sì" : "no"}`;
};

/* ---- microfono (SingStar): PCM 16 bit mono a 16 kHz verso il PC, che lo suona nel cavo audio virtuale */
const MIC = { ws: null, stream: null, ctx: null, node: null };
async function micStart() {
  if (!navigator.mediaDevices?.getUserMedia) return toast("Questo browser non dà accesso al microfono (serve HTTPS)", true);
  try {
    MIC.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  } catch (e) { return toast("Permesso del microfono negato", true); }
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/m/mic`);
  ws.binaryType = "arraybuffer";
  MIC.ws = ws;
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.t === "err") { toast(m.msg, true); micStop(); }
    else if (m.t === "ok") $("#mic-info").textContent = `🎤 Il PC suona la tua voce su «${m.device}». In PCSX2 scegli come microfono l'altro capo del cavo.`;
  };
  ws.onclose = () => { if (MIC.ws === ws) micStop(); };
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  MIC.ctx = ctx;
  const src = ctx.createMediaStreamSource(MIC.stream);
  // shortcut: ScriptProcessor e' deprecato ma funziona ovunque (anche Safari); AudioWorklet se un giorno sparisce
  const node = ctx.createScriptProcessor(1024, 1, 1);
  const step = ctx.sampleRate / 16000;
  let pos = 0;
  node.onaudioprocess = (ev) => {
    const inp = ev.inputBuffer.getChannelData(0);
    const out = new Int16Array(Math.floor((inp.length - pos) / step) + 1);
    let n = 0, peak = 0;
    for (; pos < inp.length; pos += step) {
      const v = Math.max(-1, Math.min(1, inp[Math.floor(pos)]));
      peak = Math.max(peak, Math.abs(v));
      out[n++] = v * 32767;
    }
    pos -= inp.length;
    $("#mic-level").style.width = Math.min(100, peak * 140) + "%";
    if (ws.readyState === 1 && ws.bufferedAmount < 16000) ws.send(out.buffer.slice(0, n * 2));  // rete lenta: si salta
  };
  src.connect(node);
  node.connect(ctx.destination);  // serve perche' il nodo lavori; l'uscita resta muta (non si scrive nell'output)
  MIC.node = node;
  $("#mic-btn").classList.add("on");
  $("#mic-btn").innerHTML = "⏹<br>Ferma";
}
function micStop() {
  const ws = MIC.ws;
  MIC.ws = null;
  try { ws && ws.close(); } catch { /* */ }
  try { MIC.node && MIC.node.disconnect(); } catch { /* */ }
  try { MIC.ctx && MIC.ctx.close(); } catch { /* */ }
  MIC.stream && MIC.stream.getTracks().forEach((t) => t.stop());
  MIC.stream = MIC.ctx = MIC.node = null;
  $("#mic-btn").classList.remove("on");
  $("#mic-btn").innerHTML = "🎤<br>Inizia a cantare";
  $("#mic-level").style.width = "0";
}
$("#mic-btn").onclick = () => (MIC.ws ? micStop() : micStart());

// invio dello stato: subito quando cambia (al massimo ~120 volte al secondo), altrimenti ogni 100 ms
function padLoop(ts) {
  if (!$("#pad").hidden && hwActive()) { // controller vero: si inoltra il suo stato (stesso ritmo dei tasti a schermo)
    const st = readGamepad(firstGamepad());
    const sig = JSON.stringify(st);
    if ((sig !== PAD.hwSig || ts - PAD.lastSend > 100) && ts - PAD.lastSend > 8) {
      send({ t: "pad", ...st });
      PAD.hwSig = sig; PAD.lastSend = ts;
    }
  } else if (!$("#pad").hidden) {
    const aimOn = gyroMode() === "aim";
    if (PAD.dirty || ts - PAD.lastSend > 100 || (aimOn && (PAD.aim.x || PAD.aim.y))) {
      if (ts - PAD.lastSend > 8) {
        let rx = PAD.rx, ry = PAD.ry;
        if (aimOn) { rx = Math.max(-1, Math.min(1, rx + PAD.aim.x)); ry = Math.max(-1, Math.min(1, ry + PAD.aim.y)); }
        send({ t: "pad", b: PAD.buttons, lt: PAD.lt, rt: PAD.rt, lx: PAD.lx, ly: PAD.ly, rx, ry });
        PAD.dirty = false; PAD.lastSend = ts;
      }
    }
  }
  requestAnimationFrame(padLoop);
}
requestAnimationFrame(padLoop);

function vib() { if (SET.vib) try { navigator.vibrate?.(12); } catch { /* */ } }

function setBtn(name, on) {
  if (name === "lt") PAD.lt = on ? 1 : 0;
  else if (name === "rt") PAD.rt = on ? 1 : 0;
  else if (on) PAD.buttons |= BTN[name]; else PAD.buttons &= ~BTN[name];
  PAD.dirty = true;
}

// d-pad: direzione dal punto toccato (anche diagonali)
function dpadDirs(el, x, y) {
  const r = el.getBoundingClientRect();
  const dx = (x - (r.left + r.width / 2)) / (r.width / 2), dy = (y - (r.top + r.height / 2)) / (r.height / 2);
  const dirs = [];
  if (Math.hypot(dx, dy) < 0.2) return dirs;
  const a = Math.atan2(-dy, dx) * 180 / Math.PI;  // 0 = destra, 90 = su
  if (a > -67.5 && a < 67.5) dirs.push("right");
  if (a > 22.5 && a < 157.5) dirs.push("up");
  if (a > 112.5 || a < -112.5) dirs.push("left");
  if (a < -22.5 && a > -157.5) dirs.push("down");
  return dirs;
}
function setDpad(dirs, el) {
  for (const d of ["up", "down", "left", "right"]) {
    const on = dirs.includes(d);
    if (on) PAD.buttons |= BTN[d]; else PAD.buttons &= ~BTN[d];
    $(`.d${d[0]}`, el).classList.toggle("down", on);
  }
  PAD.dirty = true;
}

function stickMove(st, x, y) {
  const el = st.el, r = el.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2, max = r.width / 2;
  let dx = (x - cx) / max, dy = (y - cy) / max;
  const d = Math.hypot(dx, dy);
  if (d > 1) { dx /= d; dy /= d; }
  const dead = 0.08;
  const mag = Math.hypot(dx, dy);
  const k = mag < dead ? 0 : (mag - dead) / (1 - dead) / mag;
  const vx = dx * k, vy = -dy * k;  // Y positivo = su
  if (st.side === "l") { PAD.lx = vx; PAD.ly = vy; } else { PAD.rx = vx; PAD.ry = vy; }
  el.querySelector(".knob").style.transform = `translate(${dx * 70}%, ${dy * 70}%)`;
  PAD.dirty = true;
}
function stickEnd(st) {
  if (st.side === "l") { PAD.lx = 0; PAD.ly = 0; } else { PAD.rx = 0; PAD.ry = 0; }
  st.el.querySelector(".knob").style.transform = "";
  st.el.classList.remove("active");
  PAD.dirty = true;
}

const area = $("#pad-area");
area.addEventListener("pointerdown", editDown, { capture: true, passive: false });
area.addEventListener("pointermove", editMove, { capture: true });
for (const ev of ["pointerup", "pointercancel"]) area.addEventListener(ev, editUp, { capture: true });
area.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  const t = e.target.closest("[data-btn], .stick, .dpad, .slider, .gun-area, .gun-r, .ds-area");
  if (!t) return;
  try { area.setPointerCapture(e.pointerId); } catch { /* non indispensabile: i movimenti arrivano comunque all'area */ }
  if (t.classList.contains("ds-area")) {
    PAD.pointers.set(e.pointerId, { kind: "ds", el: t });
    dsTouch(e, true, true);
  } else if (t.classList.contains("gun-area")) {
    PAD.pointers.set(e.pointerId, { kind: "gun", el: t });
    $("#gun-dot").classList.add("on"); $("#gun-hint").hidden = true;
    gunSend(...gunPos(e), true, true);
  } else if (t.classList.contains("gun-r")) {
    PAD.pointers.set(e.pointerId, { kind: "gunr", el: t });
    t.classList.add("down"); GUN.right = true; gunSend(GUN.x, GUN.y, GUN.down, true); vib();
  } else if (t.classList.contains("slider")) {
    const p = { kind: "slider", el: t };
    PAD.pointers.set(e.pointerId, p);
    t.classList.add("down"); sliderMove(p, e.clientY); vib();
  } else if (t.classList.contains("stick")) {
    const st = { kind: "stick", el: t, side: t.dataset.stick };
    // doppio tocco sulla levetta = L3/R3
    const now = performance.now();
    if (now - (t._last || 0) < 280) { st.click = t.dataset.stick === "l" ? "ls" : "rs"; setBtn(st.click, true); vib(); }
    t._last = now;
    t.classList.add("active");
    PAD.pointers.set(e.pointerId, st);
    stickMove(st, e.clientX, e.clientY);
  } else if (t.classList.contains("dpad")) {
    PAD.pointers.set(e.pointerId, { kind: "dpad", el: t });
    setDpad(dpadDirs(t, e.clientX, e.clientY), t); vib();
  } else {
    PAD.pointers.set(e.pointerId, { kind: "btn", el: t, name: t.dataset.btn });
    t.classList.add("down"); setBtn(t.dataset.btn, true); vib();
  }
}, { passive: false });
area.addEventListener("pointermove", (e) => {
  const p = PAD.pointers.get(e.pointerId);
  if (!p) return;
  if (p.kind === "stick") stickMove(p, e.clientX, e.clientY);
  else if (p.kind === "dpad") setDpad(dpadDirs(p.el, e.clientX, e.clientY), p.el);
  else if (p.kind === "slider") sliderMove(p, e.clientY);
  else if (p.kind === "gun") gunSend(...gunPos(e), true);
  else if (p.kind === "ds") dsTouch(e, true);
  else if (p.kind === "gunr") { /* tenuto */ }
  else if (p.el.matches(".fb, .gt-frets > div, .gt-strum > div, .ar-btns > div")) {
    // scivolando tra i tasti ABXY (o i tasti della chitarra) si passa da uno all'altro
    const over = document.elementFromPoint(e.clientX, e.clientY)?.closest(".fb, .gt-frets > div, .gt-strum > div, .ar-btns > div");
    if (over && over !== p.el) {
      p.el.classList.remove("down"); setBtn(p.name, false);
      p.el = over; p.name = over.dataset.btn; over.classList.add("down"); setBtn(p.name, true); vib();
    }
  }
});
function release(e) {
  const p = PAD.pointers.get(e.pointerId);
  if (!p) return;
  PAD.pointers.delete(e.pointerId);
  if (p.kind === "stick") { stickEnd(p); if (p.click) setBtn(p.click, false); }
  else if (p.kind === "dpad") setDpad([], p.el);
  else if (p.kind === "slider") { p.el.classList.remove("down"); PAD[p.el.dataset.axis] = 0; PAD.dirty = true; }
  else if (p.kind === "gun") { $("#gun-dot").classList.remove("on"); gunSend(GUN.x, GUN.y, false, true); }
  else if (p.kind === "ds") dsTouch(e, false, true);
  else if (p.kind === "gunr") { p.el.classList.remove("down"); GUN.right = false; gunSend(GUN.x, GUN.y, GUN.down, true); }
  else { p.el.classList.remove("down"); setBtn(p.name, false); }
}
area.addEventListener("pointerup", release);
area.addEventListener("pointercancel", release);
area.addEventListener("contextmenu", (e) => e.preventDefault());

/* ---- giroscopio: assi del telefono -> assi di un controller (X destra, Y su dalla faccia, Z verso il giocatore) */
let motionOn = false;
async function enableMotion() {
  if (typeof DeviceMotionEvent === "undefined") { toast("Questo telefono non ha sensori di movimento", true); return false; }
  if (typeof DeviceMotionEvent.requestPermission === "function") {  // iOS: serve il permesso esplicito
    try {
      if ((await DeviceMotionEvent.requestPermission()) !== "granted") { toast("Permesso dei sensori negato", true); return false; }
    } catch (e) { toast("Tocca di nuovo il pulsante del giroscopio per dare il permesso", true); return false; }
  }
  if (!window.isSecureContext) { toast("Il giroscopio richiede la connessione HTTPS", true); return false; }
  if (!motionOn) { window.addEventListener("devicemotion", onMotion); motionOn = true; }
  return true;
}
function onMotion(e) {
  const mode = gyroMode();
  if ($("#pad").hidden || mode === "off") { PAD.aim.x = PAD.aim.y = 0; return; }
  const r = e.rotationRate || {}, g = e.accelerationIncludingGravity || {};
  if (mode === "wheel") return wheelSteer(g.x || 0, g.y || 0);
  const al = r.alpha || 0, be = r.beta || 0, ga = r.gamma || 0;         // gradi/s attorno a z, x, y del telefono
  const ax = (g.x || 0) / 9.81, ay = (g.y || 0) / 9.81, az = (g.z || 0) / 9.81;
  // Wii Remote: assi del telefono sempre come in verticale, cosi' tenerlo in orizzontale = Wii Remote di lato (come sul Wii)
  const ang = WII.has($("#pad").dataset.lay) ? 0 : (screen.orientation && screen.orientation.angle) || window.orientation || 0;
  let acc, gyro;  // acc [x, y, z] in g, gyro [pitch, yaw, roll] in gradi/s
  if (ang === 90) { acc = [-ay, az, -ax]; gyro = [-ga, al, -be]; }          // orizzontale, parte alta a sinistra
  else if (ang === 270 || ang === -90) { acc = [ay, az, ax]; gyro = [ga, al, be]; }
  else { acc = [ax, az, -ay]; gyro = [be, al, -ga]; }                         // verticale
  if (SET.invx) { gyro[1] = -gyro[1]; acc[0] = -acc[0]; }
  if (SET.invy) { gyro[0] = -gyro[0]; acc[2] = -acc[2]; }
  if (mode === "aim") {
    // mira: la velocita' di rotazione muove la levetta destra (per i giochi senza giroscopio)
    const k = SET.sens / 180;
    PAD.aim.x = Math.max(-1, Math.min(1, -gyro[1] * k));
    PAD.aim.y = Math.max(-1, Math.min(1, gyro[0] * k));
    if (Math.abs(PAD.aim.x) < 0.02) PAD.aim.x = 0;
    if (Math.abs(PAD.aim.y) < 0.02) PAD.aim.y = 0;
    PAD.dirty = true;
  } else {
    const now = performance.now();
    if (now - PAD.lastMotion >= 8) {
      PAD.lastMotion = now;
      send({ t: "m", a: acc.map((v) => +v.toFixed(4)), g: gyro.map((v) => +v.toFixed(3)) });
    }
  }
}

$("#pad-exit").onclick = closePad;
$("#pad-src").onclick = () => {
  SET.src = { auto: "touch", touch: "gamepad", gamepad: "auto" }[SET.src];
  store.set("src", SET.src);
  toast({ auto: "Controller collegato se c'è, altrimenti tasti a schermo", touch: "Solo tasti a schermo",
    gamepad: "Solo il controller collegato al telefono" }[SET.src]);
  showSource(); PAD.dirty = true;
};
$("#pad-hw-touch").onclick = () => { SET.src = "touch"; store.set("src", "touch"); showSource(); PAD.dirty = true; };
$("#pad-shot").onclick = async () => { try { await api("/api/running/screenshot", { body: {} }); toast("📷 Schermata salvata sul PC"); } catch (e) { toast(e.message, true); } };
$("#pad-gyro").onclick = async () => {
  const order = ["off", "dsu", "aim"];
  SET.gyro = order[(order.indexOf(SET.gyro) + 1) % order.length];
  if (SET.gyro !== "off" && !(await enableMotion())) SET.gyro = "off";
  PAD.aim.x = PAD.aim.y = 0;
  store.set("gyro", SET.gyro);
  $("#pad-gyro").textContent = GYRO_LABEL[SET.gyro];
  toast({ off: "Giroscopio spento", dsu: "Movimento inviato agli emulatori (DSU)", aim: "Mira con il giroscopio (levetta destra)" }[SET.gyro]);
};
$("#pad-set").onclick = () => {
  $("#ps-gyro").value = SET.gyro; $("#ps-sens").value = SET.sens; $("#ps-invx").checked = SET.invx; $("#ps-invy").checked = SET.invy;
  $("#ps-vib").checked = SET.vib; $("#ps-scale").value = SET.scale;
  $("#ps-layout").value = SET.layout; $("#ps-nlabel").checked = SET.nlabel;
  $("#ps-auto").checked = SET.autoLayout; $("#ps-rumble").checked = SET.rumble;
  $("#ps-nlabel-row").hidden = SET.layout !== "switch";
  layoutHelp();
  $("#pad-settings").hidden = false;
};
const LAYOUT_HELP = {
  buzz: "Buzz!: ogni telefono è un giocatore. Sul PC: Impostazioni › Telefono e streaming › «Configura PCSX2 per Buzz!» (una volta, con PCSX2 chiuso).",
  wii: "Wii Remote: tasti, inclinazione, scuotimento e puntatore (giroscopio) arrivano a Dolphin dal server DSU. Sul PC: «Configura Dolphin per il Wii Remote».",
  wiis: "Wii Remote di lato (come un NES): tienilo in orizzontale, 1 e 2 a destra. Sul PC: «Configura Dolphin per il Wii Remote».",
  nunchuk: "Nunchuk: levetta, C e Z a sinistra; Wii Remote a destra. Inclinazione e scuotimento col giroscopio. Sul PC: «Configura Dolphin per il Wii Remote».",
  wheel: "Volante: inclina il telefono come un volante (levetta sinistra), pedali a pressione: più in alto tocchi, più premi. «Centra» se lo sterzo tira da una parte. La sensibilità regola quanto girare.",
  guitar: "Chitarra: tieni i tasti colorati e muovi il plettro; ⭐ = Star Power. Sul PC: «Configura PCSX2 per la chitarra».",
  arcade: "Arcade: 6 tasti come uno stick da sala (LP/MP/HP sopra, LK/MK/HK sotto).",
  gun: "Pistola / touchpad: muove il mouse del PC (va permesso sul PC in Impostazioni › Telefono e streaming). In DuckStation/PCSX2 scegli la GunCon con il mouse; in melonDS il pennino è il mouse.",
  mic: "Microfono: serve un cavo audio virtuale sul PC (es. VB-Cable). In PCSX2 aggiungi il microfono USB SingStar e scegli «CABLE Output».",
  gc: "GameCube: i tasti mandano la loro lettera (A = A, B = B…): in Dolphin il controller XInput si associa senza sorprese.",
};
function layoutHelp() {
  const lay = $("#ps-layout").value;
  $("#ps-help").innerHTML = esc(LAYOUT_HELP[lay] || "") + (LAYOUT_HELP[lay] ? "<br>" : "")
    + "Movimento: nell'emulatore scegli come sorgente del movimento <b>DSU / cemuhook</b>, server 127.0.0.1, porta 26760, "
    + `slot ${Math.max(0, (PAD.slot ?? PAD.player - 1))} (Cemu, Ryujinx, Dolphin, Azahar). Mira: per i giochi senza giroscopio.`;
}
$("#ps-close").onclick = async () => {
  SET.gyro = $("#ps-gyro").value; SET.sens = +$("#ps-sens").value; SET.invx = $("#ps-invx").checked; SET.invy = $("#ps-invy").checked;
  SET.vib = $("#ps-vib").checked; SET.scale = +$("#ps-scale").value;
  SET.layout = $("#ps-layout").value; SET.nlabel = $("#ps-nlabel").checked;
  SET.autoLayout = $("#ps-auto").checked; SET.rumble = $("#ps-rumble").checked;
  if (!SET.rumble) rumble(0, 0);
  for (const k of Object.keys(SET)) store.set(k, SET[k]);
  pushPad();  // aspetto, sensibilità e inversione valgono anche sugli altri telefoni
  if (SET.gyro !== "off" && !(await enableMotion())) SET.gyro = "off";
  $("#pad-gyro").textContent = GYRO_LABEL[SET.gyro];
  $("#pad").style.setProperty("--s", SET.scale);
  applyLayout();
  $("#pad-settings").hidden = true;
};
$("#ps-edit").onclick = async () => {
  await $("#ps-close").onclick();
  if (SPECIAL[$("#pad").dataset.lay]) return toast("Si possono spostare i tasti di Xbox, PlayStation, Switch e GameCube", true);
  startEdit();
};
$("#pe-done").onclick = stopEdit;
$("#pe-size").oninput = (e) => {
  if (!EDIT.sel) return;
  const k = +e.target.value;
  $(MOVABLE[EDIT.sel], $("#pad")).style.scale = k;
  saveCustom(EDIT.sel, { k });
};
$("#pe-reset").onclick = () => {
  if (!confirm("Rimettere tutti i tasti nella posizione e dimensione originale per questo aspetto?")) return;
  store.set(`padpos_${$("#pad").dataset.lay}`, {});
  applyCustom($("#pad").dataset.lay);
  pushPad();
  selectEdit(null);
};
$("#ps-layout").onchange = (e) => { $("#ps-nlabel-row").hidden = e.target.value !== "switch"; layoutHelp(); };

/* ------------------------------------------------------------------ attività: lavori del PC e comandi */
let actT = 0;
const ACT_STATUS = { running: "⏳", done: "✓", error: "✕", idle: "·" };
const ACT_ICON = { scan: "🔍", stores: "🛒", catalogs: "🌐", netmeta: "🖼", hltb: "⏱", top: "🏆", collart: "🖌", bulk: "💾", build: "▦" };

function actJob(j) {
  const pct = j.progress != null ? Math.round(j.progress * 100) : null;
  return `<div class="act-row"><span class="act-ic">${ACT_ICON[j.id] || "•"}</span>
    <div class="act-txt"><b>${esc(j.name)}</b><span class="muted small">${esc(j.phase || j.status)}</span>
      ${pct != null ? `<div class="act-bar"><i style="width:${pct}%"></i></div>` : ""}</div>
    <span class="act-st">${ACT_STATUS[j.status] || "·"}</span></div>`;
}

function renderActivity(d) {
  const run = d.running || {};
  const live = ["starting", "extracting", "running"].includes(run.status);
  $("#act-run").innerHTML = live
    ? `<div class="act-run"><div class="grow"><b>▶ ${esc(run.title || "Gioco")}</b>
        <span class="muted small">${esc(run.emulator || "")}${run.message ? " · " + esc(run.message) : ""}</span></div>
        <button class="btn" data-act="stop-game">⏹ Chiudi</button></div>`
    : `<p class="muted small">Nessun gioco in esecuzione sul PC.</p>`;
  const jobs = (d.jobs || []).filter((j) => j.status !== "idle");
  $("#act-jobs").innerHTML = jobs.length ? jobs.map(actJob).join("")
    : `<p class="muted small">Nessuna operazione in corso (store, copertine, libreria…).</p>`;
  const scanning = (d.jobs || []).some((j) => j.id === "scan" && j.status === "running");
  $("#act-btns").innerHTML = `
    <button class="btn" data-act="stores">🌐 Aggiorna store</button>
    <button class="btn" data-act="scan">🔍 Cerca nuovi giochi</button>
    <button class="btn" data-act="rebuild">▦ Ricostruisci libreria</button>
    ${scanning ? `<button class="btn" data-act="stop-scan">■ Interrompi ricerca</button>` : ""}`;
  const c = d.counts || {};
  $("#act-info").textContent = `${(c.groups || 0).toLocaleString("it")} giochi unici · ${(c.online || 0).toLocaleString("it")} dagli store`;
}

async function loadActivity() {
  clearTimeout(actT);
  let d;
  try { d = await api("/api/mobile/state"); } catch (e) { return toast(e.message, true); }
  renderActivity(d);
  const busy = (d.jobs || []).some((j) => j.status === "running") || ["starting", "running"].includes((d.running || {}).status);
  actT = setTimeout(() => { if (!$("#v-act").hidden) loadActivity(); }, busy ? 2000 : 8000);
}

$("#v-act").onclick = async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const action = b.dataset.act;
  if (action === "stop-game" && !confirm("Chiudere il gioco sul PC?")) return;
  b.disabled = true;
  try {
    const body = { action };
    if (action === "key") body.key = b.dataset.key;  // telecomando: volume, play/pausa, Esc, Invio, Alt+Tab
    renderActivity((await api("/api/mobile/action", { body })).state);
  } catch (err) { toast(err.message, true); }
  b.disabled = false;
  loadActivity();
};


/* ------------------------------------------------------------------ navigazione */
function tab(name) {
  if (name === "pad") return openPad();
  document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  $("#v-home").hidden = name !== "home";
  $("#v-lib").hidden = name !== "lib";
  $("#v-act").hidden = name !== "act";
  if (name === "lib" && !$("#grid").children.length) filterLib();
  if (name === "act") loadActivity();
}
document.querySelector(".tabbar").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) tab(b.dataset.tab); };
document.addEventListener("click", (e) => {
  const c = e.target.closest(".card");
  if (c && !$("#main").hidden) openGame(+c.dataset.id);
});
let qT = 0;
$("#q").oninput = (e) => { clearTimeout(qT); qT = setTimeout(() => { L.q = e.target.value; filterLib(); }, 200); };
$("#plat").onchange = (e) => { L.plat = e.target.value; store.set("plat", L.plat); filterLib(); };
$("#sort").onchange = (e) => { L.sort = e.target.value; store.set("sort", L.sort); filterLib(); };
$("#lib-chips").onclick = (e) => {
  const b = e.target.closest("[data-chip]");
  if (!b) return;
  L.chip = b.dataset.chip; store.set("chip", L.chip); renderLibTools(); filterLib();
};

/** Scheletro della Home: qualcosa sullo schermo nel frattempo, invece di una pagina vuota. */
function skeleton() {
  return `<h2 class="sec">&nbsp;</h2><div class="row-scroll">${`<div class="card sk"><div class="cv"></div><div class="t">&nbsp;</div></div>`.repeat(6)}</div>`;
}

/** Dati freschi in sottofondo: se la libreria e' cambiata sul PC, la Home si ridisegna da sola. */
async function refreshLib() {
  let d;
  try { d = await api("/api/library?part=owned&fresh=1", { cache: "no-store" }); } catch { return; }  // offline: resta la copia
  if (d.built_at === L.built_at && L.games.length) return;  // niente di nuovo
  decode(d);
  shownLibrary();
}

function shownLibrary() {
  $("#grid").innerHTML = "";  // libreria ricaricata: la Libreria si ridisegna alla prossima apertura
  L.shown = 0;
  renderHome(); renderLibTools();
  if (!$("#v-lib").hidden) filterLib();
}

async function start() {
  $("#pair").hidden = true; $("#main").hidden = false;
  const snap = store.get("home", []);
  if (snap.length) {  // Home subito dall'ultima copia locale: la libreria completa arriva dopo e la sostituisce
    L.games = snap; L.byId = new Map(snap.map((g) => [g.id, g]));
    renderHome();
  } else $("#v-home").innerHTML = skeleton();
  tab("home");
  try { decode(await api("/api/library?part=owned")); }
  catch (e) {  // senza rete e senza cache: la Home lo dice, invece di restare vuota
    if (!$("#pair").hidden) return;
    $("#v-home").innerHTML = `<p class="empty">Libreria non disponibile: ${esc(e.message)}</p>`;
    toast(e.message, true); return;
  }
  shownLibrary();
  // impostazioni del pad dal PC e aggiornamento in sottofondo: non devono far aspettare la Home
  pullPad().then(() => { if (!$("#pad").hidden) { $("#pad").style.setProperty("--s", SET.scale); applyLayout(); } });
  refreshLib();
  pollRun();
}

/* ---- installazione dell'app: il browser la propone solo con un certificato fidato (vedi il certificato del PC) */
const INSTALL = { ev: null };
function isStandalone() {
  return matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}
function isIOS() {
  // iPadOS 13+ si presenta come Mac: lo si riconosce dal touch
  return /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}
function installUI() {
  // Chrome/Android avvisa quando l'app si puo' installare; Safari su iPhone non ha un prompt: si spiega dal menu
  $("#install-btn").hidden = isStandalone() || !(INSTALL.ev || isIOS());
}
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); INSTALL.ev = e; installUI(); });
window.addEventListener("appinstalled", () => { INSTALL.ev = null; $("#install-btn").hidden = true; toast("App installata 🎉"); });
$("#install-btn").onclick = async () => {
  if (INSTALL.ev) {
    INSTALL.ev.prompt();
    try { await INSTALL.ev.userChoice; } catch { /* annullato */ }
    INSTALL.ev = null;
    return;
  }
  showSheet(`<h2>⬇ Installa Pozzo Launcher</h2>
    <ol class="steps">
      <li>Tocca <b>Condividi</b> (il quadrato con la freccia) in basso in Safari.</li>
      <li>Scegli <b>Aggiungi alla schermata Home</b>.</li>
      <li>Apri l'icona <b>Pozzo Launcher</b>: parte a tutto schermo, senza la barra del browser.</li>
    </ol>
    <p class="muted small">Se l'app non si installa o compare ancora l'avviso «non privata», manca il certificato del PC:
      sul PC, Impostazioni › 📱 Telefono e streaming › 🔒 Certificato del PC.</p>
    <button class="btn primary big" id="in-ok">Ok</button>`);
  $("#in-ok").onclick = closeSheet;
};

/** Service worker: serve per installare l'app e per aprirla subito le volte successive (non blocca questa apertura). */
async function registerSW() {
  if (!("serviceWorker" in navigator)) return;
  try {
    await navigator.serviceWorker.register("/m/sw.js", { scope: "/m/" });
  } catch {
    if (!store.get("swWarned", false)) {  // una volta sola: poi diventa fastidioso
      store.set("swWarned", true);
      setTimeout(() => toast("Per installare l'app serve il certificato del PC: Impostazioni › Telefono e streaming", true), 3000);
    }
  }
}

(async function main() {
  registerSW();  // senza await: la pagina non aspetta il service worker
  installUI();
  if (!window.isSecureContext) {  // pagina aperta in chiaro: niente service worker, niente installazione
    setTimeout(() => toast("Apri l'indirizzo https del PC: senza HTTPS l'app non si installa e il giroscopio non funziona", true), 1500);
  }
  if (store.get("paired", false) && !/code=\d{6}/.test(location.hash)) {
    // gia' abbinato: si parte subito, senza aspettare la rete; l'esito dell'abbinamento si verifica dopo
    start();
    fetch("/m/api/hello", { credentials: "same-origin" }).then((r) => r.json()).then((h) => {
      if (h.guest) startGuest();
      else if (!h.paired) { store.set("paired", false); showPair(); }
    }).catch(() => {});
    return;
  }
  let h = null;
  try { h = await (await fetch("/m/api/hello", { credentials: "same-origin" })).json(); } catch { h = null; }
  if (h && !h.guest) store.set("paired", !!h.paired);            // ricordato: senza rete il telefono resta abbinato
  else if (!h) h = { paired: store.get("paired", false) };
  if (h.guest) return startGuest();
  if (!h.paired || /code=\d{6}/.test(location.hash)) showPair(); else start();
})();

/* l'ospite a distanza ha solo il controller: libreria, schermate e scambio dei posti restano al PC */
function startGuest() {
  GUEST.on = true;
  $("#pair").hidden = true;
  document.querySelector(".tabbar").hidden = true;
  $("#pad-players").hidden = true;
  $("#pad-shot").hidden = true;
  $("#pad-exit").title = "Istruzioni per il video";
  $("#pad-exit").onclick = () => { closePad(); location.href = "/g/"; };
  openPad();
}
