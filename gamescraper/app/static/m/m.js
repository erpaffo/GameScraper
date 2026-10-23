"use strict";
/* GameScraper - app per il telefono: libreria, avvio dei giochi sul PC, controller con giroscopio. */

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const hue = (s) => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; };
const store = {
  get(k, d) { try { const v = localStorage.getItem("gsm." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("gsm." + k, JSON.stringify(v)); } catch { /* niente */ } },
};
const SHORT = { megadrive: "MD", mastersystem: "SMS", gamegear: "GG", psx: "PS1", ps2: "PS2", ps3: "PS3", psp: "PSP", psvita: "VITA",
  gc: "GC", wiiu: "WII U", switch: "SWITCH", xbox360: "X360", dreamcast: "DC", saturn: "SAT", nds: "DS", "3ds": "3DS", n64: "N64", windows: "PC" };
const STORE_SHORT = { steam: "STEAM", epic: "EPIC", gog: "GOG", amazon: "AMAZON", battlenet: "B.NET", xbox: "XBOX", minecraft: "MINECRAFT", manual: "PC" };

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "Content-Type": "application/json" }, credentials: "same-origin" };
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.method = opts.method || "POST"; }
  const r = await fetch(path, init);
  if (r.status === 401) { showPair(); throw new Error("Telefono non abbinato"); }
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
  $("#pair").hidden = false; $("#main").hidden = true; $("#pad").hidden = true;
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
const L = { games: [], byId: new Map(), plats: new Map(), q: "", plat: store.get("plat", ""), chip: store.get("chip", "all"), shown: 0, items: [] };

function decode(lib) {
  const gc = Object.fromEntries(lib.games.cols.map((c, i) => [c, i]));
  const rc = Object.fromEntries(lib.groups.cols.map((c, i) => [c, i]));
  const groups = lib.groups.rows;
  L.plats = new Map(lib.platforms.map((p) => [p.id, p.name]));
  L.games = lib.games.rows.map((r) => {
    const g = groups[r[gc.group]];
    return { id: r[gc.id], title: r[gc.title], sort: r[gc.sort], platform: r[gc.platform], year: r[gc.year], cover: r[gc.cover],
      store: r[gc.store] || null, installed: r[gc.installed], primary: g[rc.primary] === r[gc.id], fav: g[rc.favorite],
      hidden: g[rc.hidden], last: g[rc.last_played], playtime: g[rc.playtime], status: g[rc.status] || "",
      q: fold(r[gc.title]) };
  }).filter((g) => g.primary && !g.hidden && g.store !== "online");
  L.byId = new Map(L.games.map((g) => [g.id, g]));
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
  $("#v-home").innerHTML = row("▶ In corso", playing) + row("◷ Giocati di recente", recent) + row("★ Preferiti", favs)
    + row("🖥 Giochi PC installati", pc)
    || `<p class="empty">Libreria vuota: aggiungi i giochi dall'app sul PC.</p>`;
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
  it = it.slice().sort(L.chip === "recent" ? (a, b) => b.last - a.last : (a, b) => a.sort.localeCompare(b.sort));
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
  $("#plat").value = L.plat;
  const chips = [["all", "Tutti"], ["recent", "Recenti"], ["fav", "★ Preferiti"], ["playing", "▶ In corso"], ["backlog", "📋 Da giocare"]];
  $("#lib-chips").innerHTML = chips.map(([k, l]) => `<button data-chip="${k}" class="${L.chip === k ? "on" : ""}">${l}</button>`).join("");
}

/* ------------------------------------------------------------------ scheda del gioco */
async function openGame(id) {
  const g = L.byId.get(id);
  const box = $(".sheet-box");
  $("#sheet").hidden = false;
  box.innerHTML = `<div class="g-head"><img src="${g && g.cover ? `/img/${id}/cover?w=300` : ""}" alt=""><div><h2>${esc(g ? g.title : "")}</h2>
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
    : `<p class="muted">${pc ? "Non installato sul PC" : "Nessun emulatore configurato per questa console"}</p>`;
  $("#g-btns").insertAdjacentHTML("beforeend", `<button class="btn" data-act="fav">${d.user.favorite ? "★ Nei preferiti" : "☆ Aggiungi ai preferiti"}</button>`);
  $("#g-desc").textContent = d.description || "";
  $("#g-btns").onclick = async (e) => {
    const a = e.target.dataset.act;
    if (a === "play" || a === "playpad") {
      try { await api(`/api/game/${id}/play`, { body: {} }); } catch (err) { return toast(err.message, true); }
      toast(`Avvio di «${d.title}» sul PC…`);
      closeSheet();
      pollRun(true);
      if (a === "playpad") openPad();
    } else if (a === "fav") {
      await api(`/api/game/${id}/flag`, { body: { field: "favorite", value: !d.user.favorite } });
      if (g) g.fav = !d.user.favorite;
      openGame(id);
    }
  };
}
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
    $("#pad-info").textContent = `${PAD.player ? `Giocatore ${PAD.player} · ` : ""}${RUN.title}`;
  }
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
};
const GYRO_LABEL = { off: "🌀 Spento", dsu: "🌀 Movimento", aim: "🎯 Mira" };

async function openPad() {
  $("#pad").hidden = false;
  $("#pad").style.setProperty("--s", SET.scale);
  $("#pad-gyro").textContent = GYRO_LABEL[SET.gyro];
  try { await document.documentElement.requestFullscreen?.({ navigationUI: "hide" }); } catch { /* iOS non lo supporta */ }
  try { await screen.orientation?.lock?.("landscape"); } catch { /* non supportato: si chiede di ruotare */ }
  try { PAD.wake = await navigator.wakeLock?.request("screen"); } catch { /* */ }
  if (SET.gyro !== "off") await enableMotion();
  connectPad();
}
function closePad() {
  $("#pad").hidden = true;
  if (PAD.ws) { PAD.ws.onclose = null; PAD.ws.close(); PAD.ws = null; }
  try { document.exitFullscreen?.(); } catch { /* */ }
  try { screen.orientation?.unlock?.(); } catch { /* */ }
  try { PAD.wake?.release(); } catch { /* */ }
}
function connectPad() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/m/ws`);
  PAD.ws = ws;
  $("#pad-info").textContent = "Collegamento…";
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.t === "ok") {
      PAD.player = m.player; PAD.retry = 0;
      $("#pad-info").textContent = `Giocatore ${m.player}${RUN.title ? " · " + RUN.title : ""}`;
      if (SET.gyro === "dsu" && !m.dsu) toast(m.dsu_error || "Giroscopio non disponibile sul PC", true);
      PAD.dirty = true;
    } else if (m.t === "err") toast(m.msg, true);
  };
  ws.onclose = () => {
    if ($("#pad").hidden) return;
    $("#pad-info").textContent = "Scollegato: nuovo tentativo…";
    setTimeout(() => { if (!$("#pad").hidden) connectPad(); }, Math.min(5000, 500 * ++PAD.retry));
  };
}
function send(obj) { if (PAD.ws && PAD.ws.readyState === 1) PAD.ws.send(JSON.stringify(obj)); }

// invio dello stato: subito quando cambia (al massimo ~120 volte al secondo), altrimenti ogni 100 ms
function padLoop(ts) {
  if (!$("#pad").hidden) {
    const aimOn = SET.gyro === "aim";
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
function setDpad(dirs) {
  for (const d of ["up", "down", "left", "right"]) {
    const on = dirs.includes(d);
    if (on) PAD.buttons |= BTN[d]; else PAD.buttons &= ~BTN[d];
    $(`.dpad .d${d[0]}`).classList.toggle("down", on);
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
area.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  const t = e.target.closest("[data-btn], .stick, .dpad");
  if (!t) return;
  try { area.setPointerCapture(e.pointerId); } catch { /* non indispensabile: i movimenti arrivano comunque all'area */ }
  if (t.classList.contains("stick")) {
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
    setDpad(dpadDirs(t, e.clientX, e.clientY)); vib();
  } else {
    PAD.pointers.set(e.pointerId, { kind: "btn", el: t, name: t.dataset.btn });
    t.classList.add("down"); setBtn(t.dataset.btn, true); vib();
  }
}, { passive: false });
area.addEventListener("pointermove", (e) => {
  const p = PAD.pointers.get(e.pointerId);
  if (!p) return;
  if (p.kind === "stick") stickMove(p, e.clientX, e.clientY);
  else if (p.kind === "dpad") setDpad(dpadDirs(p.el, e.clientX, e.clientY));
  else if (p.el.classList.contains("fb")) {
    // scivolando tra i tasti ABXY si passa da uno all'altro
    const over = document.elementFromPoint(e.clientX, e.clientY)?.closest(".fb");
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
  else if (p.kind === "dpad") setDpad([]);
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
  if ($("#pad").hidden || SET.gyro === "off") { PAD.aim.x = PAD.aim.y = 0; return; }
  const r = e.rotationRate || {}, g = e.accelerationIncludingGravity || {};
  const al = r.alpha || 0, be = r.beta || 0, ga = r.gamma || 0;         // gradi/s attorno a z, x, y del telefono
  const ax = (g.x || 0) / 9.81, ay = (g.y || 0) / 9.81, az = (g.z || 0) / 9.81;
  const ang = (screen.orientation && screen.orientation.angle) || window.orientation || 0;
  let acc, gyro;  // acc [x, y, z] in g, gyro [pitch, yaw, roll] in gradi/s
  if (ang === 90) { acc = [-ay, az, -ax]; gyro = [-ga, al, -be]; }          // orizzontale, parte alta a sinistra
  else if (ang === 270 || ang === -90) { acc = [ay, az, ax]; gyro = [ga, al, be]; }
  else { acc = [ax, az, -ay]; gyro = [be, al, -ga]; }                         // verticale
  if (SET.invx) { gyro[1] = -gyro[1]; acc[0] = -acc[0]; }
  if (SET.invy) { gyro[0] = -gyro[0]; acc[2] = -acc[2]; }
  if (SET.gyro === "aim") {
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
  $("#ps-help").innerHTML = "Movimento: nell'emulatore scegli come sorgente del movimento <b>DSU / cemuhook</b>, server 127.0.0.1, porta 26760, "
    + `slot ${Math.max(0, PAD.player - 1)} (Cemu, Ryujinx, Dolphin, Azahar). Mira: per i giochi senza giroscopio.`;
  $("#pad-settings").hidden = false;
};
$("#ps-close").onclick = async () => {
  SET.gyro = $("#ps-gyro").value; SET.sens = +$("#ps-sens").value; SET.invx = $("#ps-invx").checked; SET.invy = $("#ps-invy").checked;
  SET.vib = $("#ps-vib").checked; SET.scale = +$("#ps-scale").value;
  for (const k of Object.keys(SET)) store.set(k, SET[k]);
  if (SET.gyro !== "off" && !(await enableMotion())) SET.gyro = "off";
  $("#pad-gyro").textContent = GYRO_LABEL[SET.gyro];
  $("#pad").style.setProperty("--s", SET.scale);
  $("#pad-settings").hidden = true;
};

/* ------------------------------------------------------------------ navigazione */
function tab(name) {
  if (name === "pad") return openPad();
  document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  $("#v-home").hidden = name !== "home";
  $("#v-lib").hidden = name !== "lib";
  if (name === "lib" && !$("#grid").children.length) filterLib();
}
document.querySelector(".tabbar").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) tab(b.dataset.tab); };
document.addEventListener("click", (e) => {
  const c = e.target.closest(".card");
  if (c && !$("#main").hidden) openGame(+c.dataset.id);
});
let qT = 0;
$("#q").oninput = (e) => { clearTimeout(qT); qT = setTimeout(() => { L.q = e.target.value; filterLib(); }, 200); };
$("#plat").onchange = (e) => { L.plat = e.target.value; store.set("plat", L.plat); filterLib(); };
$("#lib-chips").onclick = (e) => {
  const b = e.target.closest("[data-chip]");
  if (!b) return;
  L.chip = b.dataset.chip; store.set("chip", L.chip); renderLibTools(); filterLib();
};

async function start() {
  $("#pair").hidden = true; $("#main").hidden = false;
  try { decode(await api("/api/library")); } catch (e) { if (!$("#pair").hidden) return; toast(e.message, true); return; }
  renderHome(); renderLibTools();
  pollRun();
}

(async function main() {
  let h;
  try { h = await (await fetch("/m/api/hello", { credentials: "same-origin" })).json(); } catch { h = { paired: false }; }
  if (!h.paired || /code=\d{6}/.test(location.hash)) showPair(); else start();
})();
