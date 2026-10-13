"use strict";
/* GameScraper - frontend.
 * Tutta la libreria arriva in un'unica risposta compatta: ricerca, filtri, ordinamenti e
 * raggruppamenti sono calcolati qui in memoria (istantanei anche con 15.000 giochi).
 * La griglia e' virtualizzata: esistono nel DOM solo le copertine visibili (+ un margine). */

// utilita' condivise (api, esc, fold, store, SHORT...) in core.js

const S = {
  games: [], groups: [], byId: new Map(), plats: new Map(), colls: [], collBySlug: new Map(),
  view: store.get("view", { type: "all" }),
  search: "", sort: store.get("sort", "title"), group: store.get("group", ""),
  oneVersion: store.get("oneVersion", true), zoom: store.get("zoom", 170),
  collSort: store.get("collSort", "name"), collFilter: "",
  items: [], rows: [], total: 0, cols: 1, tw: 170, th: 260, gap: 18, pad: 22,
  nodes: new Map(), selected: null, detail: null, running: null,
  instFilter: store.get("instFilter", "all"), stores: [],
  disc: store.get("disc", { preset: "", genre: "", decade: "" }),
  shopF: store.get("shopF", { src: "", plat: "", size: "" }), shop: [], shopIds: new Set(),
};

async function loadLibrary() {
  const lib = await api("/api/library");
  Object.assign(S, decodeLibrary(lib));
  S.skipDup = lib.skip_duplicates !== false;
  S.catalogs = lib.catalogs || {};
  S.iconsCredit = lib.icons_credit || "";
  RECO.setPlatName(platName);
  S.smart = S.smart || [];
  S.shop = shopGames(S.games);
  renderDiscTools();
  S.shopIds = new Set(S.shop.map((g) => g.id));
  S.hasOnline = new Set(S.games.filter((g) => g.store === "online").map((g) => g.platform));
  GS.emit("libraryLoaded", S);
  return lib.games.rows.length > 0;
}

/* ------------------------------------------------------------------ filtri e ordinamento */
const platName = (id) => S.plats.get(id)?.name || id;

function inView(g, v) {
  const grp = g.grp;
  if (v.type === "shop") return S.shopIds.has(g.id);
  if (g.store === "online" && v.type !== "platform") return false;
  if (v.type === "dups") return g.id !== grp.primary && !grp.hidden;
  if (S.oneVersion && g.id !== grp.primary && !(v.type === "store" && v.id !== "all")) return false;
  if (v.type !== "hidden" && grp.hidden) return false;
  switch (v.type) {
    case "favorites": return !!grp.favorite;
    case "recent": return grp.last > 0;
    case "multi": return g.players >= 2;
    case "status": return (grp.status || "") === v.id;
    case "smart": { const sc = S.smart.find((x) => x.id === v.id); return !!sc && g.id === grp.primary && smartMatch(g, sc.rules); }
    case "platform": if (g.platform !== v.id) return false; break;
    case "store": if (g.platform !== "windows" || (v.id !== "all" && g.store !== v.id)) return false; break;
    case "collection": { const c = S.collBySlug.get(v.id); return !!c && grp.colls.includes(c.idx); }
    case "missing": return !g.found;
    case "hidden": return !!grp.hidden;
    default: break;
  }
  if ((g.platform === "windows" || S.hasOnline?.has(g.platform)) && S.instFilter !== "all" && (v.type === "store" || v.type === "platform")) {
    return S.instFilter === "installed" ? !!g.installed : !g.installed;
  }
  return true;
}

const SORTS = {
  title: (a, b) => collator.compare(a.sort, b.sort),
  year: (a, b) => (a.year || 9999) - (b.year || 9999) || collator.compare(a.sort, b.sort),
  "-year": (a, b) => (b.year || 0) - (a.year || 0) || collator.compare(a.sort, b.sort),
  platform: (a, b) => collator.compare(platName(a.platform), platName(b.platform)) || collator.compare(a.sort, b.sort),
  "-last": (a, b) => b.grp.last - a.grp.last || collator.compare(a.sort, b.sort),
  "-playtime": (a, b) => b.grp.playtime - a.grp.playtime || collator.compare(a.sort, b.sort),
  "-rating": (a, b) => (b.rating ?? -1) - (a.rating ?? -1) || collator.compare(a.sort, b.sort),
  "-size": (a, b) => (b.size || 0) - (a.size || 0) || collator.compare(a.sort, b.sort),
  hltb: (a, b) => (a.hltb ?? 9999) - (b.hltb ?? 9999) || collator.compare(a.sort, b.sort),
  "-hltb": (a, b) => (b.hltb ?? -1) - (a.hltb ?? -1) || collator.compare(a.sort, b.sort),
  size: (a, b) => (a.size || Infinity) - (b.size || Infinity) || collator.compare(a.sort, b.sort),
  store: (a, b) => collator.compare(shopSource(a), shopSource(b)) || collator.compare(a.sort, b.sort),
};

function computeItems() {
  let it = S.games.filter((g) => inView(g, S.view));
  if (S.view.type === "shop") it = shopFilter(it, S.shopF);
  const d = discover(it, S.disc);
  it = d.items;
  S.discScore = d.preset.score || null;
  if (S.search) {
    const toks = fold(S.search).split(/\s+/).filter(Boolean);
    it = it.filter((g) => toks.every((t) => g.q.includes(t)));
  }
  it.sort(d.cmp && S.view.type !== "shop" ? d.cmp : SORTS[S.sort] || SORTS.title);
  $("#sort").disabled = !!d.cmp;
  $("#sort").title = d.cmp ? "Ordine deciso dal filtro Scopri" : "Ordinamento";
  S.items = it;
}

function sections(items) {
  if (!S.group) return [{ label: null, items }];
  const keyOf = {
    platform: (g) => platName(g.platform),
    year: (g) => (g.year ? String(g.year) : "Anno sconosciuto"),
    letter: (g) => { const c = (g.sort || "#")[0].toUpperCase(); return /[A-Z]/.test(c) ? c : "#"; },
  }[S.group];
  const m = new Map();
  for (const g of items) { const k = keyOf(g); if (!m.has(k)) m.set(k, []); m.get(k).push(g); }
  const keys = [...m.keys()].sort((a, b) => collator.compare(a, b));
  return keys.map((k) => ({ label: k, items: m.get(k) }));
}

/* ------------------------------------------------------------------ griglia virtualizzata */
const scroller = $("#grid-scroll"), layer = $("#grid-layer"), sizer = $("#grid-sizer");

function layout() {
  const W = scroller.clientWidth - S.pad * 2;
  S.cols = Math.max(1, Math.floor((W + S.gap) / (S.zoom + S.gap)));
  S.tw = Math.floor((W - S.gap * (S.cols - 1)) / S.cols);
  S.th = Math.round(S.tw * 4 / 3) + 46;
  layer.style.setProperty("--tile", S.tw + "px");
  const rows = [];
  let y = 16;
  for (const sec of sections(S.items)) {
    if (sec.label !== null) { rows.push({ type: "h", y, h: 44, label: sec.label, count: sec.items.length }); y += 56; }
    for (let i = 0; i < sec.items.length; i += S.cols) {
      rows.push({ type: "t", y, h: S.th, items: sec.items.slice(i, i + S.cols) });
      y += S.th + S.gap;
    }
  }
  S.rows = rows;
  S.total = y + 60;
  sizer.style.height = S.total + "px";
  $("#empty").hidden = S.items.length > 0;
  $("#empty").innerHTML = S.search ? esc(`Nessun gioco trovato per «${S.search}».`)
    : !S.games.length ? `<div class="empty-lib"><b>La libreria è vuota.</b><p class="muted">Scegli la cartella delle ROM o importa i giochi degli store.</p>
        <button class="btn primary" onclick="SETUP.open(true).then(() => reload(true))">🧭 Configurazione guidata</button></div>`
    : "Nessun gioco in questa vista.";
}

function firstRow(top) {
  let lo = 0, hi = S.rows.length - 1, ans = S.rows.length;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (S.rows[mid].y + S.rows[mid].h >= top) { ans = mid; hi = mid - 1; } else lo = mid + 1;
  }
  return ans;
}

let raf = 0;
function scheduleRender() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; render(); }); }

function render() {
  const top = scroller.scrollTop, h = scroller.clientHeight, buf = h * 0.8;
  const keep = new Set();
  for (let i = firstRow(top - buf); i < S.rows.length && S.rows[i].y < top + h + buf; i++) {
    const row = S.rows[i];
    if (row.type === "h") {
      const k = "h:" + row.y + ":" + row.label;
      keep.add(k);
      let el = S.nodes.get(k);
      if (!el) {
        el = document.createElement("div");
        el.className = "grid-header";
        el.innerHTML = `${esc(row.label)}<span>${row.count}</span>`;
        layer.appendChild(el); S.nodes.set(k, el);
      }
      el.style.transform = `translateY(${row.y}px)`;
      continue;
    }
    row.items.forEach((g, j) => {
      const k = g.id;
      keep.add(k);
      let el = S.nodes.get(k);
      if (!el) { el = tileEl(g); layer.appendChild(el); S.nodes.set(k, el); }
      el.style.transform = `translate(${S.pad + j * (S.tw + S.gap)}px, ${row.y}px)`;
    });
  }
  for (const [k, el] of S.nodes) if (!keep.has(k)) { el.remove(); S.nodes.delete(k); }
}

function platIcon(pid) {
  // immagine della console (cache locale); se manca, il pallino colorato di prima
  const dot = `<span class="plat-dot" style="background:hsl(${hue(pid)} 60% 55%)"></span>`;
  return `<img class="pico" src="/icons/platform/${encodeURIComponent(pid)}" alt="" loading="lazy" onerror="this.outerHTML='${dot.replace(/"/g, "&quot;")}'">`;
}

function hue(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }

function tileEl(g) {
  const el = document.createElement("div");
  el.className = "tile" + (S.selected === g.id ? " selected" : "") + (g.store && !g.installed ? " notinst" : "");
  el.dataset.id = g.id;
  const pn = platName(g.platform);
  const showPlat = S.view.type !== "platform";
  const sub = (showPlat ? [pn, g.year] : [g.year, g.dev]).concat((S.sort === "-size" || S.sort === "size" || S.view.type === "shop") && g.size ? [fmtSize(g.size)] : [])
    .concat(S.discScore ? [scoreText(g, S.discScore)] : [])
    .concat((S.sort === "hltb" || S.sort === "-hltb") && g.hltb != null && S.discScore !== "hltb" ? [`⏱ ${fmtHours(g.hltb)}`] : []).filter(Boolean).join(" · ");
  const badges = (g.grp.favorite ? `<span class="badge fav">★</span>` : "")
    + (g.grp.status ? `<span class="badge st st-${g.grp.status}" title="${esc(STATUS[g.grp.status]?.label || "")}">${STATUS[g.grp.status]?.icon || ""}</span>` : "")
    + (S.oneVersion && g.grp.versions > 1 ? `<span class="badge ver">${g.grp.versions} ${g.store ? "store" : "versioni"}</span>` : "")
    + (g.store ? `<span class="badge plat">${esc(STORE_SHORT[g.store] || g.store.toUpperCase())}</span>`
      : showPlat ? `<span class="badge plat">${esc(SHORT[g.platform] || g.platform.toUpperCase())}</span>` : "")
    + (g.store === "online" ? `<span class="badge inst">🌐 Scarica${g.size ? " · " + fmtSize(g.size) : ""}</span>`
      : g.store && !g.installed ? `<span class="badge inst">⬇ Installa</span>` : "")
    + GS.ui.tileBadges.map((b) => { try { const t = b.render(g); return t ? `<span class="badge ext">${esc(t)}</span>` : ""; } catch { return ""; } }).join("");
  el.innerHTML = `<div class="cover" style="--h:${hue(g.title)}"><div class="ph">${esc(g.title)}<small>${esc(pn)}</small></div>`
    + (g.cover ? `<img alt="" decoding="async">` : "") + badges + `</div>`
    + `<div class="t" title="${esc(g.title)}">${esc(g.title)}</div><div class="s">${esc(sub)}</div>`;
  if (g.cover) {
    const img = el.querySelector("img");
    img.onload = () => img.classList.add("ok");
    img.onerror = () => img.remove();
    img.src = `/img/${g.id}/cover?w=${Math.round(S.tw * (window.devicePixelRatio || 1))}`;
  }
  return el;
}

function refreshGrid(keepScroll = false) {
  const st = scroller.scrollTop;
  computeItems();
  for (const el of S.nodes.values()) el.remove();
  S.nodes.clear();
  layout();
  scroller.scrollTop = keepScroll ? Math.min(st, S.total) : 0;
  render();
  updateHeader();
}

/* ------------------------------------------------------------------ sidebar e intestazione */
function counts() {
  const c = { all: 0, fav: 0, recent: 0, missing: 0, hidden: 0, plat: new Map(), coll: new Map(), store: new Map() };
  for (const g of S.games) {
    const grp = g.grp;
    if (S.oneVersion && g.id !== grp.primary) continue;
    if (g.store === "online") continue; // disponibili online: non sono giochi posseduti
    if (grp.hidden) { c.hidden++; continue; }
    c.all++;
    if (grp.favorite) c.fav++;
    if (grp.last) c.recent++;
    if (!g.found) c.missing++;
    if (g.store) {
      const x = c.store.get(g.store) || { total: 0, inst: 0 };
      x.total++; if (g.installed) x.inst++;
      c.store.set(g.store, x);
    }
    c.plat.set(g.platform, (c.plat.get(g.platform) || 0) + 1);
    for (const i of grp.colls) c.coll.set(i, (c.coll.get(i) || 0) + 1);
  }
  return c;
}

function renderSidebar() {
  const c = counts();
  $("#cnt-all").textContent = c.all.toLocaleString("it");
  $("#cnt-fav").textContent = c.fav || "";
  $("#cnt-recent").textContent = c.recent || "";
  renderStatusList();
  renderSmartList();
  $("#cnt-multi").textContent = S.games.filter((g) => g.id === g.grp.primary && !g.grp.hidden && g.players >= 2 && g.store !== "online").length || "";
  $("#cnt-shop").textContent = new Set(S.shop.map((g) => g.gi)).size || "";
  $("#cnt-missing").textContent = c.missing || "";
  $("#cnt-hidden").textContent = c.hidden || "";
  $("#cnt-dups").textContent = S.games.filter((g) => g.store !== "online" && g.id !== g.grp.primary).length || "";
  const plats = [...S.plats.values()].filter((p) => c.plat.get(p.id) && p.id !== "windows").sort((a, b) => collator.compare(a.name, b.name));
  $("#platform-list").innerHTML = plats.map((p) =>
    `<a class="nav-item" data-view="platform" data-id="${esc(p.id)}"><span class="ico">${platIcon(p.id)}</span>`
    + `<span class="lbl">${esc(p.name)}</span><span class="cnt">${(c.plat.get(p.id) || 0).toLocaleString("it")}</span></a>`).join("");
  S.collCounts = c.coll;
  // PC: tutti + uno per store (installati / posseduti)
  const st = c.store;
  const pcTotal = [...st.values()].reduce((a, x) => a + x.total, 0);
  const pcInst = [...st.values()].reduce((a, x) => a + x.inst, 0);
  $("#store-list").innerHTML = (pcTotal ? `<a class="nav-item" data-view="store" data-id="all"><span class="ico">${platIcon("windows")}</span>`
    + `<span class="lbl">Tutti i giochi PC</span><span class="cnt" title="installati / totali">${pcInst} / ${pcTotal}</span></a>` : "")
    + S.stores.filter((x) => st.get(x.id)?.total).map((x) => `<a class="nav-item" data-view="store" data-id="${esc(x.id)}">`
      + `<span class="ico"><img class="pico" src="/icons/store/${esc(x.id)}" alt="" loading="lazy" onerror="this.replaceWith(document.createTextNode('${STORE_ICON[x.id] || "•"}'))"></span><span class="lbl">${esc(x.name)}</span>`
      + `<span class="cnt" title="installati / totali">${st.get(x.id).inst} / ${st.get(x.id).total}</span></a>`).join("")
    || `<div class="muted small" style="padding:6px 8px">Nessuno store collegato: vedi Impostazioni › Account</div>`;
  $("#plugin-nav").innerHTML = GS.ui.sidebarItems.map((x) => `<a class="nav-item" data-plugin-item="${esc(x.id)}"`
    + (x.view ? ` data-view="plugin" data-id="${esc(x.view)}"` : "") + `><span class="ico">${esc(x.icon || "✦")}</span>`
    + `<span class="lbl">${esc(x.label)}</span></a>`).join("");
  $("#plugin-nav-section").hidden = !GS.ui.sidebarItems.length;
  renderCollList();
  markActive();
}

function renderStatusList() {
  const n = {};
  for (const g of S.games) if (g.id === g.grp.primary && g.grp.status && !g.grp.hidden) n[g.grp.status] = (n[g.grp.status] || 0) + 1;
  $("#status-list").innerHTML = Object.entries(STATUS).map(([id, s]) =>
    `<a class="nav-item" data-view="status" data-id="${id}"><span class="ico">${s.icon}</span><span class="lbl">${s.label}</span>`
    + `<span class="cnt">${n[id] || ""}</span></a>`).join("");
}

function renderSmartList() {
  $("#smart-list").innerHTML = S.smart.map((x) => `<a class="nav-item" data-view="smart" data-id="${esc(x.id)}">`
    + `<span class="ico">${esc(x.icon || "⚡")}</span><span class="lbl" title="${esc(x.name)}">${esc(x.name)}</span>`
    + `<span class="cnt">${smartGames(S.games, x.rules).length}</span></a>`).join("")
    || `<div class="muted" style="padding:2px 8px 6px;font-size:12px">Regole salvate che si aggiornano da sole</div>`;
  markActive();
}

/* ---- editor delle collezioni intelligenti */
let smEdit = null;
function openSmart(id = null) {
  const sc = S.smart.find((x) => x.id === id) || { id: "", name: "", icon: "⚡", rules: {} };
  smEdit = JSON.parse(JSON.stringify(sc));
  const r = smEdit.rules;
  $("#sm-title").textContent = id ? "Modifica collezione intelligente" : "Nuova collezione intelligente";
  $("#sm-name").value = sc.name;
  $("#sm-icon").value = sc.icon || "⚡";
  $("#sm-delete").hidden = !id;
  const chips = (el, list, key) => {
    $(el).innerHTML = list.map((o) => `<span class="sm-chip${(r[key] || []).includes(o.id) ? " on" : ""}" data-k="${key}" data-v="${esc(o.id)}">${esc(o.label)}</span>`).join("");
  };
  const plats = [...S.plats.values()].filter((p) => p.id !== "windows").sort((a, b) => collator.compare(a.name, b.name))
    .map((p) => ({ id: p.id, label: p.name }));
  chips("#sm-plats", plats.concat([{ id: "windows", label: "PC (tutti gli store)" }]), "platforms");
  chips("#sm-genres", Object.entries(RECO.GENRE_LABEL).map(([id, label]) => ({ id, label }))
    .sort((a, b) => collator.compare(a.label, b.label)), "genres");
  chips("#sm-status", Object.entries(STATUS).map(([k, s]) => ({ id: k, label: `${s.icon} ${s.label}` })).concat([{ id: "none", label: "Senza stato" }]), "status");
  for (const k of ["minRating", "minCritics", "minPlayers", "yearFrom", "yearTo", "maxHours", "minHours"]) $(`#sm-${k}`).value = r[k] ?? "";
  for (const k of ["installed", "favorites", "unplayed"]) $(`#sm-${k}`).checked = !!r[k];
  smCount();
  $("#smart-dlg").showModal();
}
function smRead() {
  const r = smEdit.rules;
  for (const k of ["minRating", "minCritics", "minPlayers", "yearFrom", "yearTo", "maxHours", "minHours"]) {
    const v = $(`#sm-${k}`).value;
    if (v === "") delete r[k]; else r[k] = +v;
  }
  for (const k of ["installed", "favorites", "unplayed"]) { if ($(`#sm-${k}`).checked) r[k] = true; else delete r[k]; }
  return r;
}
function smCount() { $("#sm-count").textContent = `${smartGames(S.games, smRead()).length.toLocaleString("it")} giochi`; }
async function saveSmart(remove = false) {
  smRead();
  smEdit.name = $("#sm-name").value.trim();
  smEdit.icon = $("#sm-icon").value.trim() || "⚡";
  if (!remove && !smEdit.name) return toast("Dai un nome alla collezione", true);
  let items = S.smart.filter((x) => x.id !== smEdit.id);
  if (!remove) {
    if (!smEdit.id) smEdit.id = "s" + Date.now().toString(36);
    items = S.smart.some((x) => x.id === smEdit.id) ? S.smart.map((x) => (x.id === smEdit.id ? smEdit : x)) : S.smart.concat([smEdit]);
  }
  try { S.smart = (await api("/api/smart", { body: { items } })).items; } catch (e) { return toast(e.message, true); }
  $("#smart-dlg").close();
  renderSmartList();
  if (remove) setView({ type: "all" }); else setView({ type: "smart", id: smEdit.id });
}

function collIcon(slug) {
  // icona della collezione (logo da Wikipedia o dei suoi giochi); se manca, il simbolo di prima
  return `<img class="pico coll-ico" src="/coll-img/${encodeURIComponent(slug)}/icon?w=64" alt="" loading="lazy" onerror="this.outerHTML='❖'">`;
}

function renderCollBanner() {
  const b = $("#coll-banner");
  const c = S.view.type === "collection" ? S.collBySlug.get(S.view.id) : null;
  b.hidden = !c;
  if (!c) return;
  const slug = encodeURIComponent(c.slug);
  b.innerHTML = `<div class="cb-bg" style="background-image:url('/coll-img/${slug}/cover?w=1600')"></div>
    <div class="cb-content"><img class="cb-icon" src="/coll-img/${slug}/icon?w=256" alt="" onerror="this.remove()">
      <div><div class="cb-kicker">${c.custom ? "La tua collezione" : "Collezione"}</div><h2>${esc(c.name)}</h2>
      <div class="muted">${S.items.length.toLocaleString("it")} ${S.items.length === 1 ? "gioco" : "giochi"}</div></div></div>`;
}

function renderCollList() {
  const f = fold(S.collFilter);
  let list = S.colls.filter((x) => !x.hidden && (S.collCounts.get(x.idx) || x.custom) && (!f || fold(x.name).includes(f)));
  if (S.collSort === "count") list.sort((a, b) => (S.collCounts.get(b.idx) || 0) - (S.collCounts.get(a.idx) || 0) || collator.compare(a.name, b.name));
  else list.sort((a, b) => collator.compare(a.name, b.name));
  $("#coll-list").innerHTML = list.map((x) =>
    `<a class="nav-item" data-view="collection" data-id="${esc(x.slug)}"><span class="ico">${collIcon(x.slug)}</span><span class="lbl" title="${esc(x.name)}">${esc(x.name)}</span>`
    + (x.custom ? `<span class="tag">mia</span>` : "") + `<span class="cnt">${S.collCounts.get(x.idx) || 0}</span></a>`).join("")
    || `<div class="muted" style="padding:6px 8px;font-size:12px">Nessuna collezione</div>`;
  markActive();
}

function markActive() {
  document.querySelectorAll("#nav .nav-item").forEach((a) => {
    const on = a.dataset.view === S.view.type && (a.dataset.id || null) === (S.view.id || null);
    a.classList.toggle("active", on);
  });
}

function renderDiscTools() {
  const f = S.disc, opt = (v, l, sel, t = "") => `<option value="${esc(v)}"${String(v) === String(sel) ? " selected" : ""}${t ? ` title="${esc(t)}"` : ""}>${esc(l)}</option>`;
  const nCrit = S.games.filter((g) => g.critics != null).length;
  $("#disc-preset").innerHTML = PRESETS.map((p) => opt(p.id, p.id === "critics" ? `${p.label} (${nCrit} con voto)` : p.label, f.preset, p.hint)).join("");
  $("#disc-genre").innerHTML = opt("", "Tutti i generi", f.genre) + genreList(S.games).map((x) => opt(x.id, x.label, f.genre)).join("");
  $("#disc-decade").innerHTML = opt("", "Tutti gli anni", f.decade) + decadeList(S.games).map((d) => opt(d, `Anni ${String(d).slice(2)} (${d})`, f.decade)).join("");
  for (const k of ["preset", "genre", "decade"]) $(`#disc-${k}`).classList.toggle("on", !!f[k]);
}

function renderShopTools() {
  const on = S.view.type === "shop";
  $("#shop-tools").hidden = !on;
  $("#group").hidden = $("#one-version").parentElement.hidden = on;
  if (!on) return;
  const f = S.shopF, opt = (v, l, sel) => `<option value="${esc(v)}"${v === sel ? " selected" : ""}>${esc(l)}</option>`;
  $("#shop-src").innerHTML = opt("", "Tutti gli store", f.src)
    + shopSources(S.shop, S.stores, platName).map((s) => opt(s.id, `${s.label} (${s.n})`, f.src)).join("");
  const pl = [...new Set(S.shop.map((g) => g.platform))].sort((a, b) => collator.compare(platName(a), platName(b)));
  $("#shop-plat").innerHTML = opt("", "Tutte le piattaforme", f.plat) + pl.map((p) => opt(p, platName(p), f.plat)).join("");
  $("#shop-size").innerHTML = SHOP_SIZES.map((s) => opt(s.id, s.label, f.size)).join("");
}

function viewTitle() {
  const v = S.view;
  return { shots: "Schermate", free: "Giochi gratis", wishlist: "Lista desideri", stats: "Statistiche",
    status: `${STATUS[v.id]?.icon || ""} ${STATUS[v.id]?.label || ""}`,
    smart: `${S.smart.find((x) => x.id === v.id)?.icon || "⚡"} ${S.smart.find((x) => x.id === v.id)?.name || "Collezione"}`,
    foryou: "Per te", multi: "Multigiocatore in locale", shop: "Store · da installare", all: "Tutti i giochi", favorites: "Preferiti", recent: "Giocati di recente", missing: "Senza dati",
    hidden: "Nascosti", dups: "Duplicati", platform: platName(v.id), collection: S.collBySlug.get(v.id)?.name || "Collezione",
    store: v.id === "all" ? "Giochi PC" : `PC · ${S.stores.find((x) => x.id === v.id)?.name || v.id}`,
    plugin: GS.ui.views.get(v.id)?.title || "Estensione" }[v.type] || "Giochi";
}

function updateHeader() {
  $("#view-title").textContent = viewTitle();
  const n = S.items.length;
  let extra = "";
  if (S.view.type === "collection") {
    const plats = new Set(S.items.map((g) => g.platform)).size;
    extra = ` su ${plats} ${plats === 1 ? "console" : "console"}`;
  }
  $("#view-count").textContent = S.view.type === "plugin" ? "" : `${n.toLocaleString("it")} ${n === 1 ? "gioco" : "giochi"}${extra}`;
  renderShopTools();
  $("#inst-filter").hidden = !(S.view.type === "store" || (S.view.type === "platform" && (S.view.id === "windows" || S.hasOnline.has(S.view.id))));
  const c = S.view.type === "collection" ? S.collBySlug.get(S.view.id) : null;
  $("#view-actions").innerHTML = c
    ? `<button class="icon-btn" id="coll-rename" title="Rinomina">✎ Rinomina</button>`
      + `<button class="icon-btn" id="coll-delete" title="${c.custom ? "Elimina" : "Nascondi"}">${c.custom ? "🗑 Elimina" : "⊘ Nascondi"}</button>`
    : S.view.type === "dups"
      ? `<button class="icon-btn" id="dup-toggle" title="Le versioni duplicate prendono i metadati dalla versione principale">`
        + `${S.skipDup ? "✓ Esclusi dallo scraping" : "✗ Inclusi nello scraping"} (clic per cambiare)</button>`
    : S.view.type === "smart"
      ? `<button class="icon-btn" id="smart-edit" title="Modifica le regole">✎ Regole</button>`
    : S.view.type === "shop"
      ? `<button class="btn smeta-btn" id="smeta-open" title="Cerca descrizioni, generi, giocatori e valutazioni dei giochi degli store SOLO nei database offline (LaunchBox, TheGamesDB): nessuna richiesta online">📚 Scraping metadati store <span class="pill">offline</span></button>`
    : S.view.type === "platform" && S.view.id !== "windows"
      ? `<button class="icon-btn" id="cat-open" title="Collega pagine web con i giochi di questa console">`
        + ((S.catalogs[S.view.id] || []).map((u) => `<img class="sico" src="/icons/site?url=${encodeURIComponent(u)}" alt="">`).join("") || "🌐")
        + ` Store online${(S.catalogs[S.view.id] || []).length ? ` (${S.catalogs[S.view.id].length})` : ""}</button>` : "";
}

function setView(v) {
  S.view = v;
  store.set("view", v);
  GS.emit("viewChanged", v);
  const pv = $("#plugin-view");
  document.body.classList.toggle("foryou-mode", ["foryou", "free", "wishlist", "stats", "shots"].includes(v.type));
  if (v.type !== "collection") $("#coll-banner").hidden = true;
  if (["free", "wishlist", "stats", "shots"].includes(v.type)) {
    $("#grid-scroll").hidden = true;
    pv.hidden = false;
    markActive();
    updateHeader();
    $("#view-count").textContent = "";
    ({ free: renderFree, wishlist: renderWishlist, stats: renderStats, shots: renderShots })[v.type](pv);
    return;
  }
  if (v.type === "foryou") {
    $("#grid-scroll").hidden = true;
    pv.hidden = false;
    markActive();
    updateHeader();
    $("#view-count").textContent = "";
    renderForYou(pv);
    return;
  }
  if (v.type === "plugin") {
    const view = GS.ui.views.get(v.id);
    $("#grid-scroll").hidden = true;
    pv.hidden = false;
    pv.innerHTML = "";
    markActive();
    updateHeader();
    Promise.resolve(view ? view.render(pv, GS) : (pv.textContent = "Vista non disponibile"))
      .catch((e) => { pv.textContent = "Errore nella vista: " + e.message; });
    return;
  }
  $("#grid-scroll").hidden = false;
  pv.hidden = true;
  const def = { recent: "-last", collection: "year", shop: "title" }[v.type];
  setTimeout(renderCollBanner, 0);
  if (def) { S.sort = def; $("#sort").value = def; } else { S.sort = store.get("sort", "title"); $("#sort").value = S.sort; }
  markActive();
  refreshGrid();
}

/* ------------------------------------------------------------------ vista "Per te" (stile Netflix) */
function nfCard(g, why = "") {
  const tag = g.store ? (STORE_SHORT[g.store] || "PC") : (SHORT[g.platform] || g.platform.toUpperCase());
  const sc = g.critics != null ? `Critica ${Math.round(g.critics * 100)}` : g.rating != null && g.votes ? `★ ${Math.round(g.rating * 100)}%` : "";
  return `<div class="nf-card${g.store && !g.installed ? " notinst" : ""}" data-id="${g.id}" title="${esc(g.title)}">`
    + `<div class="nf-cv" style="--h:${hue(g.title)}"><span class="nf-ph">${esc(g.title)}</span>`
    + (g.cover ? `<img loading="lazy" src="/img/${g.id}/cover?w=300" alt="" onerror="this.remove()">` : "")
    + `<span class="nf-tag">${esc(tag)}</span>${g.players >= 2 ? `<span class="nf-pl">👥 ${g.players}</span>` : ""}</div>`
    + `<div class="nf-info"><b>${esc(g.title)}</b><span>${esc([g.year, why || sc].filter(Boolean).join(" · "))}</span></div></div>`;
}

async function renderForYou(pv) {
  const rows = RECO.rows(S.games, RECO.tastes());
  const crows = RECO.collectionRows(S.games, S.colls, RECO.tastes());
  const heroG = rows.find((r) => r.id === "foryou")?.items[0] || rows[0]?.items[0];
  pv.innerHTML = `<div class="nf">
    <div class="nf-hero" id="nf-hero"><div class="nf-hero-txt">
      <div class="nf-kicker">✨ Scelto per te</div><h1 id="nf-h-title"></h1><p id="nf-h-meta" class="muted"></p><p id="nf-h-desc"></p>
      <div class="nf-hero-btns"><button class="btn primary" id="nf-quiz">🎲 A cosa vuoi giocare?</button>
        <button class="btn" id="nf-h-open">Dettagli</button></div></div></div>
    ${rows.length ? "" : `<p class="muted" style="padding:20px">Gioca qualche partita o rispondi al questionario per ricevere consigli.</p>`}
    ${rows.map((r) => `<section class="nf-row"><h2>${esc(r.title)}${r.why ? ` <small>${esc(r.why)}</small>` : ""}</h2>
      <div class="nf-wrap"><button class="nf-arr l" aria-label="Indietro">‹</button><div class="nf-scroll">${r.items.map((g) => nfCard(g)).join("")}</div>
      <button class="nf-arr r" aria-label="Avanti">›</button></div></section>`).join("")}
    ${crows.length ? `<div class="nf-section"><h2 class="nf-sec-title">🗂 Collezioni</h2><p class="muted small">Saghe e franchise della tua libreria</p></div>` : ""}
    ${crows.map((r) => `<section class="nf-row"><h2>${esc(r.title)}</h2>
      <div class="nf-wrap"><button class="nf-arr l" aria-label="Indietro">‹</button><div class="nf-scroll">${r.items.map((x) => collCard(x)).join("")}</div>
      <button class="nf-arr r" aria-label="Avanti">›</button></div></section>`).join("")}</div>`;
  pv.querySelectorAll(".nf-wrap").forEach((w) => {
    const sc = w.querySelector(".nf-scroll");
    w.querySelector(".l").onclick = () => sc.scrollBy({ left: -sc.clientWidth * 0.85, behavior: "smooth" });
    w.querySelector(".r").onclick = () => sc.scrollBy({ left: sc.clientWidth * 0.85, behavior: "smooth" });
  });
  pv.onclick = (e) => {
    const k = e.target.closest(".nf-coll");
    if (k) { setView({ type: "collection", id: k.dataset.slug }); return; }
    const c = e.target.closest(".nf-card");
    if (c) openDetail(+c.dataset.id);
  };
  pv.onmouseover = (e) => { const c = e.target.closest(".nf-card"); if (c && c.dataset.id !== pv.dataset.hero) setHero(+c.dataset.id); };
  $("#nf-quiz").onclick = openQuiz;
  $("#nf-h-open").onclick = () => pv.dataset.hero && openDetail(+pv.dataset.hero);
  let tmr = 0;
  async function setHero(id) {
    clearTimeout(tmr);
    tmr = setTimeout(async () => {
      pv.dataset.hero = id;
      const g = S.byId.get(id);
      if (!g) return;
      $("#nf-h-title").textContent = g.title;
      $("#nf-h-meta").textContent = [g.store ? STORE_SHORT[g.store] : platName(g.platform), g.year, g.dev, g.genres].filter(Boolean).join(" · ");
      let d;
      try { d = await api(`/api/game/${id}`); } catch { return; }
      if (+pv.dataset.hero !== id) return;
      const bg = d.media.hero || d.media.fanart || d.media.screenshot || d.media.header || d.media.cover;
      $("#nf-hero").style.backgroundImage = bg ? `url("${bg}?w=1280")` : "none";
      $("#nf-h-desc").textContent = (d.description || "").slice(0, 260) + ((d.description || "").length > 260 ? "…" : "");
      if (d.players_local) $("#nf-h-meta").textContent += " · " + d.players_local;
    }, 120);
  }
  if (heroG) setHero(heroG.id);
}

function collCard(x) {
  const slug = encodeURIComponent(x.coll.slug);
  return `<div class="nf-coll" data-slug="${esc(x.coll.slug)}" title="${esc(x.coll.name)}">
    <div class="nfc-bg" style="--h:${hue(x.coll.name)};background-image:url('/coll-img/${slug}/cover?w=640')"></div>
    <img class="nfc-icon" src="/coll-img/${slug}/icon?w=256" alt="" loading="lazy" onerror="this.remove()">
    <div class="nfc-txt"><b>${esc(x.coll.name)}</b><span>${esc(x.why || "")}</span></div>
    ${x.played ? `<div class="nfc-prog"><i style="width:${Math.round((100 * x.played) / x.games.length)}%"></i></div>` : ""}</div>`;
}

/* ---- questionario "A cosa vuoi giocare?" */
const QZ = { step: 0, a: {} };
function openQuiz() {
  const t = RECO.tastes() || {};
  QZ.step = 0;
  QZ.a = { genres: t.genres || [], moods: [] };
  $("#quiz").showModal();
  renderQuiz();
}
function renderQuiz() {
  const Q = RECO.QUESTIONS, box = $("#quiz-body");
  if (QZ.step >= Q.length) return renderQuizResults();
  const q = Q[QZ.step], val = QZ.a[q.id];
  const on = (id) => (q.multi ? (val || []).includes(id) : val === id);
  box.innerHTML = `<div class="qz-head"><span class="muted">Domanda ${QZ.step + 1} di ${Q.length}</span>
      <div class="qz-dots">${Q.map((_, i) => `<i class="${i <= QZ.step ? "on" : ""}"></i>`).join("")}</div>
      <button class="icon-btn" id="qz-x" title="Chiudi">✕</button></div>
    <h2>${esc(q.title)}</h2>${q.sub ? `<p class="muted">${esc(q.sub)}</p>` : ""}
    <div class="qz-opts">${q.options.map((o) => `<button class="qz-opt${on(o.id) ? " on" : ""}" data-o="${esc(o.id)}">${esc(o.label)}</button>`).join("")}</div>
    <div class="row end"><button class="btn" id="qz-back"${QZ.step ? "" : " disabled"}>‹ Indietro</button>
      <button class="btn" id="qz-skip">Salta</button>
      ${q.multi ? `<button class="btn primary" id="qz-next">Avanti ›</button>` : ""}</div>`;
  box.onclick = (e) => {
    const o = e.target.closest(".qz-opt");
    if (o) {
      if (q.multi) {
        const s = new Set(QZ.a[q.id] || []);
        s.has(o.dataset.o) ? s.delete(o.dataset.o) : s.add(o.dataset.o);
        QZ.a[q.id] = [...s];
        o.classList.toggle("on");
      } else { QZ.a[q.id] = o.dataset.o; QZ.step++; renderQuiz(); }
      return;
    }
    const id = e.target.id;
    if (id === "qz-x") $("#quiz").close();
    else if (id === "qz-back") { QZ.step--; renderQuiz(); }
    else if (id === "qz-skip") { if (!q.multi) delete QZ.a[q.id]; QZ.step++; renderQuiz(); }
    else if (id === "qz-next") { QZ.step++; renderQuiz(); }
  };
}
function renderQuizResults() {
  RECO.saveTastes(QZ.a);
  const res = RECO.suggest(S.games, QZ.a, 12);
  const box = $("#quiz-body");
  box.innerHTML = `<div class="qz-head"><b>🎮 Da giocare adesso</b><span class="muted">${res.length} suggerimenti</span>
      <button class="icon-btn" id="qz-x" title="Chiudi">✕</button></div>
    ${res.length ? `<div class="qz-res">${res.map((x) => nfCard(x.g, x.why)).join("")}</div>`
      : `<p class="muted">Nessun gioco corrisponde a tutte le risposte: prova a cambiarne qualcuna.</p>`}
    <div class="row end"><button class="btn" id="qz-again">↻ Altri suggerimenti</button>
      <button class="btn" id="qz-restart">Rifai il questionario</button></div>`;
  box.onclick = (e) => {
    const c = e.target.closest(".nf-card");
    if (c) { $("#quiz").close(); openDetail(+c.dataset.id); return; }
    if (e.target.id === "qz-x") $("#quiz").close();
    if (e.target.id === "qz-again") renderQuizResults();
    if (e.target.id === "qz-restart") { QZ.step = 0; renderQuiz(); }
  };
  if (S.view.type === "foryou") renderForYou($("#plugin-view")); // i gusti aggiornati cambiano le righe
}

/* ------------------------------------------------------------------ dettaglio */
function fmtTime(sec) {
  if (!sec) return "—";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}
function fmtDate(ts) { return ts ? new Date(ts * 1000).toLocaleString("it", { dateStyle: "medium", timeStyle: "short" }) : "—"; }

async function openDetail(id) {
  S.selected = id;
  document.querySelectorAll(".tile.selected").forEach((t) => t.classList.remove("selected"));
  S.nodes.get(id)?.classList.add("selected");
  let d;
  try { d = await api(`/api/game/${id}`); } catch (e) { return toast(e.message, true); }
  S.detail = d;
  const hero = d.media.hero || d.media.screenshot || d.media.title || d.media.header || d.media.cover;
  $("#detail .hero-bg").style.backgroundImage = hero ? `url("${hero}?w=1280")` : "none";
  const u = d.user;
  const dl = [
    ["Sviluppatore", d.developer], ["Editore", d.publisher], ["Uscita", d.release_date || d.year],
    ["Generi", (d.genres || []).join(", ")], ["Giocatori", d.players_local],
    ["Regione", d.region], ["Dimensione", fmtSize(d.size)],
    ["Tempo di gioco", u.playtime ? fmtTime(u.playtime) : ""], ["Ultima partita", u.last_played ? fmtDate(u.last_played) : ""],
    ["Avvii", u.play_count || ""],
    ["Tempo su " + (d.store_name || "store"), d.store_playtime ? fmtTime(d.store_playtime) : ""],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");
  const shots = ["screenshot", "title", "wheel", "fanart", "marquee", "header"].filter((k) => d.media[k]);
  const pc = !!d.store;
  const online = d.store === "online";
  const mainBtn = online
    ? `<button class="btn primary" id="d-install" title="Apre la pagina di download nel browser">⬇ Scarica (browser)</button>`
      + (d.magnet ? `<button class="btn" id="d-torrent" title="Apre il link nel client torrent">🧲 Torrent</button>` : "")
    : pc && !d.installed
    ? `<button class="btn primary" id="d-install" ${d.can_install ? "" : "disabled"}>⬇ Installa</button>`
    : `<button class="btn primary" id="d-play" ${d.emulator ? "" : "disabled"}>▶ Gioca</button>`;
  const actions = GS.ui.gameActions.filter((a) => { try { return !a.when || a.when(d); } catch { return false; } });
  $("#detail-body").innerHTML = `
    <div class="d-top">
      <div class="d-cover">${d.media.cover ? `<img src="${d.media.cover}?w=450" alt="">` : ""}</div>
      <div class="d-title"><h2>${esc(d.title)}</h2><div class="muted">${esc(d.platform_name)}${d.year ? " · " + d.year : ""}</div></div>
    </div>
    <div class="d-actions">
      ${mainBtn}
      <button class="btn ${u.favorite ? "on" : ""}" id="d-fav">${u.favorite ? "★ Preferito" : "☆ Preferito"}</button>
      ${pc && d.page ? `<button class="btn" id="d-page">🛒 Pagina store</button>` : ""}
      ${!pc || (d.installed && d.files.length) ? `<button class="btn" id="d-folder">📁 Cartella</button>` : ""}
      <button class="btn" id="d-hide">${u.hidden ? "Mostra" : "Nascondi"}</button>
      ${d.store === "manual" ? `<button class="btn" id="d-manual-del">🗑 Rimuovi</button>` : ""}
      ${actions.map((a) => `<button class="btn" data-ga="${esc(a.id)}">${esc((a.icon ? a.icon + " " : "") + a.label)}</button>`).join("")}
    </div>
    ${d.missing && d.missing.length ? `<div class="d-missing"><span>Mancano: ${esc(d.missing.join(", "))}</span>`
      + `<button class="btn" id="d-scrape" title="Riprova tutte le fonti per questo gioco (tutte le sue versioni)">⟳ Cerca dati mancanti</button></div>` : ""}
    ${online && d.magnet && !/dn=[^&]*\./.test(d.magnet) ? `<div class="d-note">Il link magnet scarica l'intero set:
      nel client torrent seleziona solo il file «${esc(d.file_name || d.title)}».</div>` : ""}
    <div class="d-emu muted">${online ? `${d.page_url ? `<img class="sico" src="/icons/site?url=${encodeURIComponent(d.page_url)}" alt="">` : "🌐"} Disponibile online · ${esc(d.file_name || "")}`
      : pc ? `${esc(d.store_name)} · ${d.installed ? "installato" : "non installato"}`
      : d.emulator ? "Emulatore: " + esc(d.emulator) + (d.profile ? ` <span class="tag" title="Profilo di avvio di questo gioco">profilo</span>` : "")
      : `Nessun emulatore configurato per ${esc(d.platform_name)} — <a href="#" id="d-emu-cfg">impostazioni</a>`}
      ${!pc && !online ? `<a href="#" id="d-profile" title="Emulatore, core, eseguibile e argomenti solo per questo gioco">⚙ Profilo di avvio</a>` : ""}</div>
    ${ratingBlock(d)}
    ${online ? "" : `<div class="d-status">${Object.entries(STATUS).map(([k, s]) =>
      `<button class="btn st-btn${u.status === k ? " on st-" + k : ""}" data-status="${k}">${s.icon} ${s.label}</button>`).join("")}</div>`}
    ${d.hltb ? `<div class="d-hltb" title="Tempi medi dei giocatori su HowLongToBeat">
      ${[["Storia", d.hltb.main], ["Storia + extra", d.hltb.extra], ["Completista", d.hltb.complete]].filter(([, v]) => v)
        .map(([k, v]) => `<div><span>${k}</span><b>${fmtHours(v)}</b></div>`).join("")}
      ${d.hltb.url ? `<a href="#" data-open="${esc(d.hltb.url)}" class="muted small">HowLongToBeat ↗</a>` : ""}</div>` : ""}
    <div class="d-h">Collezioni</div>
    <div class="chips">${d.collections.map((c) => `<span class="chip" data-slug="${esc(c.slug)}">${esc(c.name)}<button class="x" title="Togli da questa collezione">✕</button></span>`).join("")}
      <span class="chip add" id="d-add-coll">＋ Aggiungi</span></div>
    <dl class="d-grid">${dl.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    ${d.description ? `<div class="d-h">Descrizione</div><div class="d-desc">${esc(d.description)}</div>` : ""}
    ${d.shots ? `<div class="d-h">📷 Le tue schermate (${d.shots}) <a href="#" id="d-shots-all" class="muted small">vedi tutte</a></div>
      <div class="my-shots" id="d-myshots"></div>` : ""}
    ${shots.length ? `<div class="d-h">Immagini</div><div class="shots">${shots.map((k) => `<img loading="lazy" src="${d.media[k]}?w=700" alt="${k}">`).join("")}</div>` : ""}
    ${d.versions.length > 1 ? `<div class="d-h">Versioni (${d.versions.length})</div><div class="versions">${d.versions.map((v) =>
      `<div class="version ${v.primary ? "primary" : ""}"><span class="f" title="${esc(v.file)}">${esc(v.file)}</span>`
      + (v.store ? `<span class="muted">${v.installed ? "installato" : "non installato"}</span>` : "")
      + (v.primary ? `<span class="muted">principale</span>` : `<button class="btn" data-pref="${v.id}">Usa questa</button>`)
      + (v.store && !v.installed ? `<button class="btn" data-install="${v.id}">⬇</button>`
        : `<button class="btn" data-play="${v.id}" ${d.emulator ? "" : "disabled"}>▶</button>`) + `</div>`).join("")}</div>` : ""}
    <div id="d-ext"></div>
    <div class="d-h">File</div>
    <div class="files">${d.files.map(esc).join("<br>")}${d.crc ? `<br>CRC ${esc(d.crc)}` : ""}
      ${Object.keys(d.sources).length ? `<br>Fonti: ${esc([...new Set(Object.values(d.sources))].join(", "))}` : ""}</div>`;
  $("#detail").classList.add("open");
  $("#detail").setAttribute("aria-hidden", "false");
  $("#detail").scrollTop = 0;
  renderDetailSections(d);
  if (d.shots) loadDetailShots(d.id);
  GS.emit("gameOpened", d);
}

function ratingBlock(d) {
  if (d.rating == null && d.rating_critics == null) return "";
  const stars = d.rating != null ? "★".repeat(Math.round(d.rating * 5)) + "☆".repeat(5 - Math.round(d.rating * 5)) : "";
  const parts = (d.ratings || []).map((r) => `${esc(r.name)} ${(r.rating * 5).toFixed(1)}${r.count ? ` (${r.count.toLocaleString("it")})` : ""}`);
  return `<div class="d-rating">${d.rating != null ? `<span class="stars">${stars}</span><span class="big">${(d.rating * 5).toFixed(1)}</span>`
    + `<span class="muted">${d.rating_count ? d.rating_count.toLocaleString("it") + " voti" : ""}</span>` : ""}
    ${d.rating_critics != null ? `<span class="crit" title="Voto della critica (Metacritic / IGDB)">Critica ${Math.round(d.rating_critics * 100)}/100</span>` : ""}
    ${parts.length > 1 ? `<span class="muted small">${parts.join(" · ")}</span>` : ""}</div>`;
}

async function renderDetailSections(d) {
  const host = $("#d-ext");
  for (const sec of GS.ui.detailSections) {
    if (sec.when && !sec.when(d)) continue;
    const box = document.createElement("div");
    box.innerHTML = `<div class="d-h">${esc(sec.title || "")}</div><div class="d-ext-body muted">…</div>`;
    host.appendChild(box);
    try {
      const out = await sec.render(d, GS);
      const body = box.querySelector(".d-ext-body");
      body.classList.remove("muted");
      if (out instanceof Element) { body.innerHTML = ""; body.appendChild(out); } else body.innerHTML = out ?? "";
    } catch (e) {
      box.querySelector(".d-ext-body").textContent = "Errore: " + e.message;
    }
    if (S.detail !== d) return; // l'utente ha aperto un altro gioco
  }
}

function closeDetail() {
  $("#detail").classList.remove("open");
  $("#detail").setAttribute("aria-hidden", "true");
  S.detail = null;
}

async function setFlag(field, value) {
  const d = S.detail;
  try { await api(`/api/game/${d.id}/flag`, { body: { field, value } }); } catch (e) { return toast(e.message, true); }
  const g = S.byId.get(d.id);
  g.grp[field] = field === "status" ? value || "" : value ? 1 : 0;
  renderSidebar();
  refreshGrid(true);
  openDetail(d.id);
}

/* ------------------------------------------------------------------ popover (collezioni / testo) */
function closePopover() { $("#popover").hidden = true; $("#popover").innerHTML = ""; }

// un <dialog> modale rende inattivo tutto il resto: popover e notifiche vanno spostati dentro
function overlayHost() { return document.querySelector("dialog[open]") || $("#app"); }

function placePopover(anchor) {
  const p = $("#popover"), r = anchor.getBoundingClientRect();
  if (p.parentElement !== overlayHost()) overlayHost().appendChild(p);
  p.hidden = false;
  const left = Math.min(r.left, window.innerWidth - 316), top = Math.min(r.bottom + 6, window.innerHeight - p.offsetHeight - 10);
  p.style.left = Math.max(8, left) + "px";
  p.style.top = Math.max(8, top) + "px";
}

function askText(anchor, placeholder, initial = "") {
  return new Promise((resolve) => {
    const p = $("#popover");
    p.innerHTML = `<input placeholder="${esc(placeholder)}" value="${esc(initial)}"><div style="display:flex;gap:6px;justify-content:flex-end">
      <button class="btn" data-a="cancel">Annulla</button><button class="btn primary" data-a="ok">OK</button></div>`;
    placePopover(anchor);
    const inp = $("input", p);
    inp.focus(); inp.select();
    const done = (v) => { closePopover(); resolve(v); };
    inp.onkeydown = (e) => { if (e.key === "Enter") done(inp.value.trim() || null); if (e.key === "Escape") done(null); };
    p.onclick = (e) => { const a = e.target.dataset.a; if (a === "ok") done(inp.value.trim() || null); if (a === "cancel") done(null); };
  });
}

function pickCollection(anchor, gameId, current) {
  const p = $("#popover");
  p.innerHTML = `<input placeholder="Cerca o crea collezione…"><div class="opts"></div>`;
  placePopover(anchor);
  const inp = $("input", p), opts = $(".opts", p);
  const cur = new Set(current);
  const draw = () => {
    const f = fold(inp.value.trim());
    const list = S.colls.filter((c) => !cur.has(c.slug) && !c.hidden && (!f || fold(c.name).includes(f)))
      .sort((a, b) => (S.collCounts.get(b.idx) || 0) - (S.collCounts.get(a.idx) || 0)).slice(0, 60);
    const exact = S.colls.some((c) => fold(c.name) === f);
    opts.innerHTML = (f && !exact ? `<div class="opt hl" data-new="1">＋ Nuova collezione «${esc(inp.value.trim())}»</div>` : "")
      + list.map((c) => `<div class="opt" data-slug="${esc(c.slug)}"><span>${esc(c.name)}</span><span class="muted">${S.collCounts.get(c.idx) || 0}</span></div>`).join("");
  };
  draw();
  inp.focus();
  inp.oninput = draw;
  inp.onkeydown = (e) => { if (e.key === "Enter") opts.querySelector(".opt")?.click(); if (e.key === "Escape") closePopover(); };
  opts.onclick = async (e) => {
    const o = e.target.closest(".opt");
    if (!o) return;
    try {
      if (o.dataset.new) await api("/api/collections", { body: { name: inp.value.trim(), game: gameId } });
      else await api(`/api/collections/${encodeURIComponent(o.dataset.slug)}/members`, { body: { game: gameId, action: "add" } });
    } catch (err) { return toast(err.message, true); }
    closePopover();
    await reload(true);
    openDetail(gameId);
  };
}

/* ------------------------------------------------------------------ avvio giochi */
async function play(id) {
  try { await api(`/api/game/${id}/play`, { body: {} }); } catch (e) { return toast(e.message, true); }
  pollRunning();
}

let pollTimer = 0;
async function pollRunning() {
  clearTimeout(pollTimer);
  let r;
  try { r = await api("/api/running"); } catch { r = {}; }
  const bar = $("#running");
  const active = ["starting", "extracting", "running"].includes(r.status);
  if (active) {
    bar.hidden = false;
    bar.className = r.status === "extracting" ? "extracting" : "";
    const what = r.status === "extracting" ? (r.message || "Estrazione…") : r.status === "starting" ? "Avvio…" : `In esecuzione con ${esc(r.emulator)}`;
    bar.innerHTML = `<span class="dot"></span><b>${esc(r.title)}</b><span class="muted">${esc(what)}</span>`
      + (r.status === "running" ? `<button class="btn" id="run-shot" title="Schermata del gioco (${esc(SHOTS.hotkey)} · controller: Select+R1)">📷${r.shots ? " " + r.shots : ""}</button>` : "");
    if (S.running && r.status === "running" && (r.shots || 0) > (S.running.shots || 0)) toast("📷 Schermata salvata");
    pollTimer = setTimeout(pollRunning, 1500);
  } else {
    bar.hidden = true;
    if (S.running && S.running.started === r.started) {
      if (r.status === "error") toast(r.message, true);
      if (r.status === "finished") {
        toast(`Sessione terminata: ${fmtTime(r.played)}${r.shots ? ` · ${r.shots} ${r.shots === 1 ? "schermata" : "schermate"}` : ""}`);
        reload(true);
        if (S.detail) openDetail(S.detail.id);
      }
    }
  }
  S.running = r;
}


/* ------------------------------------------------------------------ giochi PC: installa, aggiungi, store */
async function installGame(id) {
  try { await api(`/api/game/${id}/install`, { body: {} }); }
  catch (e) { return toast(e.message, true); }
  toast("Apro lo store per l'installazione. Al termine premi «↻ Aggiorna store» in Impostazioni › Account.");
}

function openAddGame() {
  const dlg = $("#add-game");
  $("#ag-exe").value = ""; $("#ag-title").value = ""; $("#ag-args").value = "";
  dlg.showModal();
  $("#ag-exe").focus();
}

async function saveAddGame() {
  const exe = $("#ag-exe").value.trim();
  if (!exe) return toast("Scegli il file .exe del gioco", true);
  try {
    await api("/api/manual", { body: { exe, title: $("#ag-title").value.trim(), args: $("#ag-args").value } });
  } catch (e) { return toast(e.message, true); }
  $("#add-game").close();
  toast("Gioco aggiunto: cerco copertina e immagini…");
  pollStores(true);
}

/* ---- immagini delle collezioni */
let caTimer = 0;
async function pollCollart(started = false) {
  clearTimeout(caTimer);
  let js;
  try { js = await api("/api/collart"); } catch { return; }
  if (started && js.status !== "running") js.status = "running";
  $("#collart-info").textContent = `${js.with_icon} collezioni su ${js.total} con un'icona · ${js.with_wiki} da Wikipedia`;
  renderJobInto($("#collart-progress"), js);
  const run = js.status === "running";
  $("#collart-missing").disabled = $("#collart-all").disabled = run;
  $("#collart-stop").hidden = !run;
  if (run && $("#settings").open) caTimer = setTimeout(pollCollart, 2000);
}
async function startCollart(mode) {
  try { await api("/api/collart", { body: { mode } }); } catch (e) { return toast(e.message, true); }
  pollCollart(true);
}

/* ---- HowLongToBeat */
let hltbTimer = 0, hltbRunning = false;
async function pollHltb(started = false) {
  clearTimeout(hltbTimer);
  let js;
  try { js = await api("/api/hltb"); } catch { return; }
  if (started && js.status !== "running") js.status = "running";
  $("#hltb-info").textContent = `${(js.with_hltb || 0).toLocaleString("it")} giochi con la durata · ${(js.known || 0).toLocaleString("it")} già cercati`;
  renderJobInto($("#hltb-progress"), js);
  const run = js.status === "running";
  $("#hltb-missing").disabled = $("#hltb-all").disabled = run;
  $("#hltb-stop").hidden = !run;
  if (run) { hltbRunning = true; hltbTimer = setTimeout(pollHltb, 1500); return; }
  if (hltbRunning) { hltbRunning = false; if (js.status === "done") { await reload(true); toast(js.phase); } else if (js.status === "error") toast(js.phase, true); }
}
async function startHltb(mode) {
  try { await api("/api/hltb", { body: { mode } }); } catch (e) { return toast(e.message, true); }
  pollHltb(true);
}

/* ---- lista dei desideri: badge degli avvisi */
async function pollWishBadge() {
  try {
    const w = await api("/api/wishlist");
    $("#cnt-wish").textContent = w.unseen ? `🔔 ${w.unseen}` : w.items.length || "";
    $("#cnt-wish").classList.toggle("alert", !!w.unseen);
    if (w.unseen && !pollWishBadge.told) {
      pollWishBadge.told = true;
      toast(`♡ Lista desideri: ${w.unseen} ${w.unseen === 1 ? "offerta" : "offerte"} da vedere`);
    }
  } catch { /* server non pronto */ }
  setTimeout(pollWishBadge, 10 * 60 * 1000);
}

/* ---- pagina: giochi gratis */
const FREE_F = { console: store.get("freeConsole", ""), kind: "" };
const remoteImg = (u) => (u ? `/remote-img?u=${encodeURIComponent(u)}` : "");
async function renderFree(pv, force = false) {
  pv.innerHTML = `<div class="page"><p class="muted">Cerco i giochi gratis…</p></div>`;
  let d;
  try { d = await api(`/api/free${force ? "?force=1" : ""}`); } catch (e) { pv.innerHTML = `<div class="page"><p>${esc(e.message)}</p></div>`; return; }
  $("#cnt-free").textContent = d.items.filter((i) => !i.upcoming).length || "";
  const draw = () => {
    const items = d.items.filter((i) => (!FREE_F.console || i.console === FREE_F.console) && (!FREE_F.kind || i.kind === FREE_F.kind));
    const kinds = [...new Set(d.items.map((i) => i.kind))];
    const cons = [...new Set(d.items.map((i) => i.console))];
    const chip = (k, v, l) => `<button class="pchip${FREE_F[k] === v ? " on" : ""}" data-f="${k}" data-v="${esc(v)}">${esc(l)}</button>`;
    pv.innerHTML = `<div class="page">
      <div class="page-head"><div><h2>🎁 Giochi gratis adesso</h2>
        <p class="muted small">Da tenere, inclusi negli abbonamenti, weekend gratuiti e DLC gratis.
          Fonti: <a href="#" data-open="https://www.allkeyshop.com/blog/daily-game-deals/">allkeyshop.com</a> ·
          <a href="#" data-open="https://www.gamerpower.com/">GamerPower</a>${d.time ? ` · aggiornato ${new Date(d.time * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" })}` : ""}</p>
        ${d.errors.length ? `<p class="warn small">⚠ ${d.errors.map((e) => esc(e.split(":")[0])).join(", ")} non ha risposto: ${d.stale ? "elenco dell'ultima volta" : "elenco parziale"}.</p>` : ""}</div>
        <button class="btn" id="free-refresh">↻ Aggiorna</button></div>
      <div class="pchips">${chip("console", "", "Tutte")}${cons.map((c) => chip("console", c, ({ pc: "🖥 PC", playstation: "PlayStation", xbox: "Xbox", switch: "Switch" })[c] || c)).join("")}
        <span class="sep"></span>${chip("kind", "", "Ogni offerta")}${kinds.map((k) => chip("kind", k, FREE_KIND[k] || k)).join("")}</div>
      <div class="free-grid">${items.map((i, n) => `<div class="free-card${i.owned ? " owned" : ""}" data-n="${d.items.indexOf(i)}">
        <div class="fc-cover">${i.cover ? `<img loading="lazy" src="${remoteImg(i.cover)}" alt="" onerror="this.remove()">` : ""}
          <span class="fc-kind k-${fold(i.kind).replace(/[^a-z]+/g, "")}">${esc(FREE_KIND[i.kind] || i.kind)}</span>
          ${i.drm && i.drm !== "none" ? `<img class="fc-drm" src="/icons/store/${esc(({ amazon: "amazon", epic: "epic", steam: "steam", gog: "gog", battlenet: "battlenet" })[i.drm] || "manual")}" alt="" onerror="this.remove()">` : ""}
          ${i.owned ? `<span class="fc-owned">✓ Già tuo</span>` : ""}</div>
        <div class="fc-info"><b title="${esc(i.full_title || i.title)}">${esc(i.title)}</b>
          <span class="muted small">${esc([({ pc: "PC", playstation: "PlayStation", xbox: "Xbox" })[i.console] || i.console,
            i.end ? "fino al " + new Date(i.end.replace(" ", "T")).toLocaleDateString("it") : "", i.worth ? "valore " + i.worth : ""].filter(Boolean).join(" · "))}</span>
          <button class="btn primary fc-open" data-n="${d.items.indexOf(i)}">Apri ↗</button></div></div>`).join("")
        || `<p class="muted">Nessun gioco gratis con questi filtri.</p>`}</div></div>`;
  };
  draw();
  pv.onclick = (e) => {
    const f = e.target.closest("[data-f]");
    if (f) { FREE_F[f.dataset.f] = f.dataset.v; if (f.dataset.f === "console") store.set("freeConsole", f.dataset.v); draw(); return; }
    if (e.target.id === "free-refresh") { renderFree(pv, true); return; }
    const o = e.target.closest("[data-open]");
    if (o) { e.preventDefault(); api("/api/open", { body: { url: o.dataset.open } }); return; }
    const c = e.target.closest(".fc-open, .free-card");
    if (c) api("/api/open", { body: { url: d.items[+c.dataset.n].url } }).then(() => toast("Apro la pagina nel browser"));
  };
}
const FREE_KIND = { "Free to keep": "Da tenere", "Free with Prime": "Con Prime", "Free with PS+": "Con PS Plus", Gamepass: "Game Pass",
  "Free DLC": "DLC gratis", "Free weekend": "Weekend gratis", "Free to play": "Free to play", "In arrivo": "In arrivo" };

/* ---- pagina: lista dei desideri */
async function renderWishlist(pv) {
  let d;
  try { d = await api("/api/wishlist"); } catch (e) { pv.innerHTML = `<div class="page"><p>${esc(e.message)}</p></div>`; return; }
  const money = (v, cur = "EUR") => (v == null ? "—" : v === 0 ? "Gratis" : v.toLocaleString("it", { style: "currency", currency: cur || "EUR" }));
  const steamImg = (w) => (w.steam_appid ? remoteImg(`https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/${w.steam_appid}/capsule_184x69.jpg`) : "");
  const s = d.settings;
  pv.innerHTML = `<div class="page">
    <div class="page-head"><div><h2>♡ Lista desideri</h2>
      <p class="muted small">Prezzi degli store ufficiali${s.has_key ? " (IsThereAnyDeal: Steam, GOG, Epic, Humble e altri)" : " (Steam; aggiungi la chiave IsThereAnyDeal per confrontare tutti gli store)"}.
        Avviso quando lo sconto supera il ${s.threshold}%, quando il prezzo scende sotto quello che indichi o tocca il minimo storico.</p></div>
      <div class="row"><button class="btn" id="wl-import">⬇ Importa da Steam</button><button class="btn" id="wl-check">↻ Controlla prezzi</button>
        <button class="btn" id="wl-settings">⚙</button></div></div>
    <div class="wl-settings" hidden>
      <label>Chiave IsThereAnyDeal <input class="field" id="wl-key" placeholder="${s.has_key ? "già inserita (lascia vuoto per non cambiarla)" : "facoltativa"}">
        <a href="#" data-open="https://isthereanydeal.com/apps/my/">ottieni la chiave gratuita</a></label>
      <label>Paese <input class="field" id="wl-country" value="${esc(s.country)}" maxlength="2" style="width:60px"></label>
      <label>Avvisa da uno sconto del <input class="field" id="wl-thr" type="number" min="5" max="95" value="${s.threshold}" style="width:70px">%</label>
      <button class="btn primary" id="wl-save">Salva</button></div>
    <div class="wl-add"><input class="field grow" id="wl-q" placeholder="Cerca un gioco da aggiungere…" autocomplete="off"><div id="wl-res"></div></div>
    ${d.checking ? `<p class="muted small">Controllo dei prezzi in corso…</p>` : ""}
    <div class="wl-list">${d.items.map((w) => `<div class="wl-row${w.alert ? " has-alert" : ""}${w.alert && !w.seen ? " new" : ""}">
      <div class="wl-img">${steamImg(w) ? `<img src="${steamImg(w)}" alt="" onerror="this.remove()">` : ""}</div>
      <div class="wl-t"><b>${esc(w.title)}</b>${w.owned ? ` <span class="tag">già in libreria</span>` : ""}
        <span class="muted small">${w.checked ? `${esc(w.shop || "")} · controllato ${new Date(w.checked * 1000).toLocaleDateString("it")}` : "prezzo non ancora controllato"}
          ${w.low != null ? ` · minimo storico ${money(w.low, w.currency)}` : ""}</span></div>
      <div class="wl-price">${w.cut ? `<span class="cut">-${w.cut}%</span><s class="muted">${money(w.regular, w.currency)}</s>` : ""}<b>${money(w.price, w.currency)}</b></div>
      <div class="wl-alert">${w.alert ? `🔔 ${esc(w.alert)}` : ""}</div>
      <label class="wl-target" title="Avvisami quando costa meno di…">≤ <input class="field" type="number" step="0.5" min="0" data-target="${esc(w.id)}" value="${w.target ?? ""}" placeholder="€"></label>
      ${w.url ? `<button class="btn" data-open="${esc(w.url)}">Apri ↗</button>` : ""}
      <button class="icon-btn" data-del="${esc(w.id)}" title="Togli dalla lista">✕</button></div>`).join("")
      || `<p class="muted">La lista è vuota: cerca un gioco qui sopra o importa la tua lista desideri di Steam.</p>`}</div></div>`;
  if (d.unseen) { api("/api/wishlist/seen", { body: {} }).then(() => pollWishBadge()); }
  let tmr = 0;
  $("#wl-q").oninput = (e) => {
    clearTimeout(tmr);
    const q = e.target.value.trim();
    if (q.length < 2) { $("#wl-res").innerHTML = ""; return; }
    tmr = setTimeout(async () => {
      let r;
      try { r = await api("/api/wishlist/search", { body: { q } }); } catch (err) { return toast(err.message, true); }
      $("#wl-res").innerHTML = r.results.map((x, i) => `<div class="wl-hit" data-i="${i}">${x.cover ? `<img src="${remoteImg(x.cover)}" alt="" onerror="this.remove()">` : ""}<span>${esc(x.title)}</span><b>＋</b></div>`).join("")
        || `<div class="muted small" style="padding:8px">Nessun risultato</div>`;
      $("#wl-res").onclick = async (ev) => {
        const h = ev.target.closest(".wl-hit");
        if (!h) return;
        const x = r.results[+h.dataset.i];
        try { await api("/api/wishlist/add", { body: x }); } catch (err) { return toast(err.message, true); }
        toast(`«${x.title}» aggiunto alla lista desideri`);
        renderWishlist(pv);
        setTimeout(() => S.view.type === "wishlist" && renderWishlist(pv), 4000);
      };
    }, 350);
  };
  pv.onclick = async (e) => {
    const t = e.target;
    const o = t.closest("[data-open]");
    if (o) { e.preventDefault(); api("/api/open", { body: { url: o.dataset.open } }); return; }
    if (t.dataset.del) { await api(`/api/wishlist/item/${encodeURIComponent(t.dataset.del)}`, { method: "DELETE" }); renderWishlist(pv); pollWishBadge(); }
    else if (t.id === "wl-settings") pv.querySelector(".wl-settings").hidden = !pv.querySelector(".wl-settings").hidden;
    else if (t.id === "wl-save") {
      const body = { country: $("#wl-country").value, threshold: $("#wl-thr").value };
      if ($("#wl-key").value.trim()) body.itad_key = $("#wl-key").value.trim();
      try { await api("/api/wishlist/settings", { body }); } catch (err) { return toast(err.message, true); }
      toast("Impostazioni salvate"); renderWishlist(pv);
    } else if (t.id === "wl-check") {
      await api("/api/wishlist/check", { body: { force: true } });
      toast("Controllo dei prezzi avviato…");
      setTimeout(() => S.view.type === "wishlist" && renderWishlist(pv), 5000);
    } else if (t.id === "wl-import") {
      t.disabled = true;
      try { const r = await api("/api/wishlist/import-steam", { body: {} }); toast(`Importati ${r.imported} giochi dalla lista desideri di Steam`); }
      catch (err) { toast(err.message, true); }
      renderWishlist(pv);
      setTimeout(() => S.view.type === "wishlist" && renderWishlist(pv), 6000);
    }
  };
  pv.onchange = async (e) => {
    const t = e.target;
    if (t.dataset.target === undefined) return;
    try { await api(`/api/wishlist/item/${encodeURIComponent(t.dataset.target)}`, { body: { target: t.value } }); } catch (err) { return toast(err.message, true); }
    toast("Prezzo desiderato salvato");
  };
}

/* ---- pagina: statistiche */
const STATS_Y = { y: new Date().getFullYear() };
async function renderStats(pv) {
  let d;
  try { d = await api(`/api/stats?year=${STATS_Y.y}`); } catch (e) { pv.innerHTML = `<div class="page"><p>${esc(e.message)}</p></div>`; return; }
  const prim = S.games.filter((g) => g.id === g.grp.primary && !g.grp.hidden && g.store !== "online");
  const secs = (g) => (g.grp.playtime || 0) + (g.splay || 0) * 60;
  const played = prim.filter((g) => secs(g) > 0 || g.grp.last);
  const total = played.reduce((a, g) => a + secs(g), 0);
  const st = {};
  for (const g of prim) if (g.grp.status) st[g.grp.status] = (st[g.grp.status] || 0) + 1;
  const backlogH = prim.filter((g) => g.grp.status === "backlog" && g.hltb).reduce((a, g) => a + g.hltb, 0);
  const doneN = st.done || 0, startedN = doneN + (st.playing || 0) + (st.dropped || 0);
  const sum = (key) => {
    const m = new Map();
    for (const g of played) for (const k of key(g)) m.set(k, (m.get(k) || 0) + secs(g) / Math.max(1, key(g).length));
    return [...m.entries()].filter(([, v]) => v >= 60).sort((a, b) => b[1] - a[1]);
  };
  const perPlat = sum((g) => [g.store ? STORE_SHORT[g.store] || g.store : platName(g.platform)]).slice(0, 10);
  const perGenre = sum((g) => RECO.canonGenres(g.genres).map((x) => RECO.GENRE_LABEL[x])).slice(0, 8);
  const top = played.slice().sort((a, b) => secs(b) - secs(a)).slice(0, 10);
  const h = (s) => fmtTime(s);
  const bars = (rows) => {
    const max = Math.max(1, ...rows.map((r) => r[1]));
    return `<div class="sbars">${rows.map(([k, v]) => `<div class="sbar" title="${esc(k)}: ${h(v)}"><span class="k">${esc(k)}</span>
      <span class="trk"><span class="fill" style="width:${Math.max(1, (100 * v) / max)}%"></span></span><span class="v">${h(v)}</span></div>`).join("")}</div>`;
  };
  const mMax = Math.max(1, ...d.months);
  const MONTHS = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"];
  const years = Object.keys(d.years).map(Number);
  if (!years.includes(STATS_Y.y)) years.push(STATS_Y.y);
  pv.innerHTML = `<div class="page stats">
    <div class="tiles">
      <div class="tile-s"><b>${prim.length.toLocaleString("it")}</b><span>giochi in libreria</span></div>
      <div class="tile-s"><b>${played.length.toLocaleString("it")}</b><span>giocati almeno una volta</span></div>
      <div class="tile-s"><b>${h(total) || "0 min"}</b><span>tempo di gioco totale<br><small>app + store</small></span></div>
      <div class="tile-s"><b>${doneN}</b><span>finiti${startedN ? ` · ${Math.round((100 * doneN) / startedN)}% di quelli iniziati` : ""}</span></div>
      <div class="tile-s"><b>${st.playing || 0}</b><span>in corso</span></div>
      <div class="tile-s"><b>${st.backlog || 0}</b><span>da giocare${backlogH ? `<br><small>servono ~${Math.round(backlogH)} h per finirli</small>` : ""}</span></div>
    </div>
    <div class="scard"><div class="scard-h"><h3>🏆 Il tuo ${d.year}</h3>
      <select class="field" id="st-year">${years.sort().map((y) => `<option${y === STATS_Y.y ? " selected" : ""}>${y}</option>`).join("")}</select></div>
      <p class="muted small">Ore giocate per mese (partite avviate dall'app)</p>
      <div class="months">${d.months.map((v, i) => `<div class="mcol" title="${MONTHS[i]} ${d.year}: ${h(v) || "nessuna partita"}">
        <span class="mbar" style="height:${v ? Math.max(3, (100 * v) / mMax) : 0}%"></span><span class="ml">${MONTHS[i]}</span></div>`).join("")}</div>
      ${d.top_year.length ? `<h4>Classifica dell'anno</h4><ol class="toplist">${d.top_year.map((t) => `<li data-id="${t.id}"><b>${esc(t.title)}</b>
        <span class="muted">${esc(STORE_SHORT[t.platform] || platName(t.platform))} · ${h(t.seconds)}</span></li>`).join("")}</ol>`
        : `<p class="muted small">Nessuna partita registrata nel ${d.year}: la classifica si riempie avviando i giochi dall'app.</p>`}</div>
    <div class="scols">
      <div class="scard"><h3>Tempo per console e store</h3>${perPlat.length ? bars(perPlat) : `<p class="muted small">Ancora nessun dato.</p>`}</div>
      <div class="scard"><h3>Tempo per genere</h3>${perGenre.length ? bars(perGenre) : `<p class="muted small">Ancora nessun dato.</p>`}</div>
    </div>
    <div class="scard"><h3>I più giocati di sempre</h3>${top.length ? `<ol class="toplist">${top.map((g) => `<li data-id="${g.id}"><b>${esc(g.title)}</b>
      <span class="muted">${esc(g.store ? STORE_SHORT[g.store] : platName(g.platform))} · ${h(secs(g))}${g.grp.status ? ` · ${STATUS[g.grp.status].icon} ${STATUS[g.grp.status].label}` : ""}</span></li>`).join("")}</ol>`
      : `<p class="muted small">Ancora nessuna partita registrata.</p>`}</div></div>`;
  $("#st-year").onchange = (e) => { STATS_Y.y = +e.target.value; renderStats(pv); };
  pv.onclick = (e) => { const li = e.target.closest("li[data-id]"); if (li) openDetail(+li.dataset.id); };
}

/* ---- schermate dei giochi */
const SHOTS = { detail: [], page: [], hotkey: "Ctrl+Shift+S" };

async function loadDetailShots(id) {
  let r;
  try { r = await api(`/api/game/${id}/shots`); } catch { return; }
  if (!S.detail || S.detail.id !== id) return;
  SHOTS.detail = r.shots;
  const box = $("#d-myshots");
  if (box) box.innerHTML = r.shots.slice(0, 12).map((s, i) => `<img loading="lazy" src="${s.url}?w=360" data-i="${i}" alt="" title="${esc(fmtDate(s.taken))} · ${esc(s.source)}">`).join("");
}

function openLightbox(list, i) {
  if (!list.length) return;
  const lb = $("#lightbox");
  const draw = () => {
    const s = list[i];
    lb.innerHTML = `<div class="lb-top"><b>${esc(s.title || "")}</b><span class="muted">${esc(fmtDate(s.taken))} · ${esc(s.source)} · ${i + 1}/${list.length}</span>
      <span class="grow"></span><button class="btn" data-lb="folder">📁 Mostra nella cartella</button><button class="btn" data-lb="del">🗑 Elimina</button>
      <button class="icon-btn big" data-lb="close">✕</button></div>
      <button class="lb-arr l" data-lb="prev"${i ? "" : " disabled"}>‹</button><img class="lb-img" src="${s.url}" alt="">
      <button class="lb-arr r" data-lb="next"${i < list.length - 1 ? "" : " disabled"}>›</button>`;
  };
  const close = () => { lb.hidden = true; document.removeEventListener("keydown", keys, true); };
  const keys = (e) => {
    if (e.key === "Escape") { e.stopPropagation(); close(); }
    else if (e.key === "ArrowLeft" && i > 0) { i--; draw(); }
    else if (e.key === "ArrowRight" && i < list.length - 1) { i++; draw(); }
  };
  lb.onclick = async (e) => {
    const a = e.target.closest("[data-lb]")?.dataset.lb;
    if (!a && e.target === lb) return close();
    if (a === "close") close();
    else if (a === "prev" && i > 0) { i--; draw(); }
    else if (a === "next" && i < list.length - 1) { i++; draw(); }
    else if (a === "folder") api("/api/shots/open", { body: { id: list[i].id } });
    else if (a === "del") {
      if (!confirm("Eliminare questa schermata dall'app? (l'originale dell'emulatore, se c'e', non viene toccato)")) return;
      await api(`/api/shots/${list[i].id}`, { method: "DELETE" });
      list.splice(i, 1);
      if (!list.length) { close(); } else { i = Math.min(i, list.length - 1); draw(); }
      if (S.view.type === "shots") renderShots($("#plugin-view"));
      if (S.detail) openDetail(S.detail.id);
    }
  };
  document.addEventListener("keydown", keys, true);
  lb.hidden = false;
  draw();
}

async function renderShots(pv) {
  let d;
  try { d = await api("/api/shots"); } catch (e) { pv.innerHTML = `<div class="page"><p>${esc(e.message)}</p></div>`; return; }
  SHOTS.page = d.shots;
  const st = d.settings;
  SHOTS.hotkey = st.hotkey ? st.hotkey.split("+").map((k) => k[0].toUpperCase() + k.slice(1)).join("+") : "—";
  $("#cnt-shots").textContent = d.shots.length || "";
  const groups = new Map();
  d.shots.forEach((s, i) => { const k = s.game ?? s.key; if (!groups.has(k)) groups.set(k, { s, items: [] }); groups.get(k).items.push(i); });
  pv.innerHTML = `<div class="page">
    <div class="page-head"><div><h2>📷 Le tue schermate</h2>
      <p class="muted small">Mentre giochi premi <kbd>${esc(SHOTS.hotkey)}</kbd> o <kbd>Select</kbd>+<kbd>R1</kbd> sul controller (View+RB su Xbox):
        l'app salva la finestra del gioco. Le schermate fatte con il tasto dell'emulatore vengono importate a fine partita.</p></div>
      <div class="row"><button class="btn" id="sh-steam">⬇ Importa da Steam</button><button class="btn" id="sh-folder">📁 Cartella</button>
        <button class="btn" id="sh-set">⚙</button></div></div>
    <div class="wl-settings" id="sh-settings" hidden>
      <label>Scorciatoia da tastiera <input class="field" id="sh-hotkey" value="${esc(st.hotkey || "")}" placeholder="es. ctrl+shift+s, f10"></label>
      <label class="chk"><input type="checkbox" id="sh-pad"${st.pad ? " checked" : ""}> Select + R1 sul controller</label>
      <label class="chk"><input type="checkbox" id="sh-emu"${st.import_emulator ? " checked" : ""}> Importa le schermate dell'emulatore a fine partita</label>
      <button class="btn primary" id="sh-save">Salva</button></div>
    ${d.shots.length ? [...groups.values()].map((g) => `<section class="sh-group"><h3>${g.s.game ? `<a href="#" data-game="${g.s.game}">${esc(g.s.title)}</a>` : esc(g.s.title)}
        <span class="muted small">${esc(STORE_SHORT[g.s.platform] || platName(g.s.platform) || "")} · ${g.items.length}</span></h3>
        <div class="sh-grid">${g.items.map((i) => `<img loading="lazy" src="${d.shots[i].url}?w=480" data-i="${i}" alt="" title="${esc(fmtDate(d.shots[i].taken))} · ${esc(d.shots[i].source)}">`).join("")}</div></section>`).join("")
      : `<p class="muted">Ancora nessuna schermata. Avvia un gioco dall'app e premi <kbd>${esc(SHOTS.hotkey)}</kbd>.</p>`}</div>`;
  pv.onclick = async (e) => {
    const t = e.target;
    if (t.matches(".sh-grid img")) return openLightbox(SHOTS.page, +t.dataset.i);
    if (t.dataset.game) { e.preventDefault(); return openDetail(+t.dataset.game); }
    if (t.id === "sh-folder") api("/api/shots/open", { body: {} });
    else if (t.id === "sh-set") $("#sh-settings").hidden = !$("#sh-settings").hidden;
    else if (t.id === "sh-steam") {
      t.disabled = true;
      try { const r = await api("/api/shots/import-steam", { body: {} }); toast(`Importate ${r.imported} schermate da Steam`); renderShots(pv); }
      catch (err) { toast(err.message, true); t.disabled = false; }
    } else if (t.id === "sh-save") {
      try {
        await api("/api/shots/settings", { body: { hotkey: $("#sh-hotkey").value, pad: $("#sh-pad").checked, import_emulator: $("#sh-emu").checked } });
        toast("Impostazioni delle schermate salvate (valgono dalla prossima partita)");
        renderShots(pv);
      } catch (err) { toast(err.message, true); }
    }
  };
}

/* ---- profilo di avvio per gioco */
let PF = null;
async function openProfile(d) {
  try { PF = await api(`/api/game/${d.id}/profile`); } catch (e) { return toast(e.message, true); }
  PF.id = d.id;
  const p = PF.profile, def = PF.default;
  $("#pf-game").textContent = `— ${d.title} (${PF.platform})`;
  const emus = PF.emulators.filter((e) => e.configured || e.id === p.emulator).sort((a, b) => (b.preset - a.preset) || a.id.localeCompare(b.id));
  $("#pf-emu").innerHTML = `<option value="">Predefinito della console${def ? ` (${esc(def.emulator)}${def.core ? " · " + esc(def.core) : ""})` : ""}</option>`
    + emus.map((e) => `<option value="${esc(e.id)}"${p.emulator === e.id ? " selected" : ""}>${esc(e.id)}${e.preset ? "" : " (non previsto per questa console)"}</option>`).join("");
  $("#pf-core").innerHTML = `<option value="">Predefinito</option>` + PF.cores.map((c) => `<option${p.core === c ? " selected" : ""}>${esc(c)}</option>`).join("");
  $("#pf-exe").value = p.exe || "";
  $("#pf-args").value = p.args || "";
  $("#pf-extra").value = p.extra || "";
  pfRefresh();
  $("#prof-dlg").showModal();
}
function pfRefresh() {
  const emu = $("#pf-emu").value || PF.default?.emulator || "";
  $("#pf-core-row").hidden = emu !== "retroarch";
  const sel = PF.emulators.find((e) => e.id === $("#pf-emu").value);
  const cur = PF.current && !PF.current.error && PF.current.emulator === (sel ? sel.id : PF.default?.emulator) ? PF.current : null;
  const base = sel ? (cur || { exe: sel.exe, args: sel.args }) : (PF.default || cur);
  $("#pf-args").placeholder = base ? base.args.join(" ") : "{rom}";
  const exe = $("#pf-exe").value || (base ? base.exe : "");
  const args = ($("#pf-args").value || (base ? base.args.join(" ") : "{rom}"));
  const extra = $("#pf-extra").value;
  $("#pf-cmd").textContent = `"${exe}" ${extra && args.includes("{rom}") ? args.replace("{rom}", extra + " {rom}") : args + (extra ? " " + extra : "")}`;
}
async function saveProfile(reset = false) {
  try {
    PF = Object.assign(PF, reset ? await api(`/api/game/${PF.id}/profile`, { method: "DELETE" })
      : await api(`/api/game/${PF.id}/profile`, { body: { emulator: $("#pf-emu").value, core: $("#pf-emu").value === "retroarch" || !$("#pf-emu").value ? $("#pf-core").value : "",
        exe: $("#pf-exe").value, args: $("#pf-args").value, extra: $("#pf-extra").value } }));
  } catch (e) { return toast(e.message, true); }
  $("#prof-dlg").close();
  toast(reset ? "Profilo rimosso: si usano le impostazioni della console" : "Profilo di avvio salvato");
  openDetail(PF.id);
}

/* ---- metadati degli store (solo database offline) */
let smetaTimer = 0, smetaRunning = false;
async function pollStoreMeta(started = false) {
  clearTimeout(smetaTimer);
  let js;
  try { js = await api("/api/stores/metadata"); } catch { return; }
  if (started && js.status !== "running") js.status = "running";
  renderJobInto($("#smeta-progress"), js);
  const run = js.status === "running";
  $("#smeta-all").disabled = $("#smeta-missing").disabled = run;
  $("#smeta-stop").hidden = !run;
  if (run) { smetaRunning = true; smetaTimer = setTimeout(pollStoreMeta, 700); return; }
  if (smetaRunning) {
    smetaRunning = false;
    if (js.status === "done") { await reload(true); toast(js.phase); } else if (js.status === "error") toast(js.phase, true);
  }
}
async function startStoreMeta(missing) {
  try { await api("/api/stores/metadata", { body: { missing } }); } catch (e) { return toast(e.message, true); }
  pollStoreMeta(true);
}

let storeTimer = 0;
async function pollStores(started = false) {
  clearTimeout(storeTimer);
  let js;
  try { js = await api("/api/stores/refresh"); } catch { return; }
  if (started && js.status !== "running") js.status = "running"; // appena avviato
  const box = $("#acc-progress");
  if (box && $("#settings").open && SET.tab === "acc") renderJobInto(box, js);
  if (js.status === "running") { storeTimer = setTimeout(pollStores, 1000); SET.storesRunning = true; return; }
  if (SET.storesRunning) {
    SET.storesRunning = false;
    if (js.status === "done") { await reload(true); toast("Store aggiornati: libreria aggiornata"); }
    else if (js.status === "error") toast(js.phase, true);
    if ($("#settings").open && SET.tab === "acc") loadAccounts();
  }
}

function renderJobInto(box, js) {
  const bars = (js.tasks || []).filter((t) => t.total > 0).map((t) => {
    const pct = Math.min(100, Math.round((100 * t.done) / t.total));
    return `<div class="bar"><span>${esc(t.name)}</span><div class="track"><div class="fill" style="width:${pct}%"></div></div>`
      + `<span class="n">${Math.round(t.done).toLocaleString("it")} / ${Math.round(t.total).toLocaleString("it")}</span></div>`;
  }).join("");
  box.innerHTML = js.status === "idle" ? "" : `<div class="phase">${esc(js.phase || "")}</div><div class="bars">${bars}</div>`
    + `<pre class="log">${esc((js.log || []).join("\n"))}</pre>`;
}


/* ---- impostazioni › fonti dei dati */
async function loadSources() {
  let d;
  try { d = await api("/api/sources"); } catch (e) { return toast(e.message, true); }
  SET.sources = d;
  renderSources(d.sources);
  $("#bulk-list").innerHTML = d.bulk.map((b) => `<div class="bulk-row"><div class="grow"><b>${esc(b.name)}</b>
      <div class="muted small">${esc(b.description)}</div>
      <div class="muted small">${b.ready ? `${b.games.toLocaleString("it")} giochi · ${b.size_mb} MB · aggiornato ${new Date(b.updated * 1000).toLocaleDateString("it")}`
        : "non ancora scaricato (avverrà al primo scraping)"} · ogni ${b.refresh_days} giorni</div></div>
      <button class="btn" data-bulk="${esc(b.id)}">↻ Aggiorna ora</button></div>`).join("");
  const sch = d.schedule || {};
  $("#sch-on").checked = !!sch.enabled;
  $("#sch-time").value = sch.time || "03:00";
  $("#sch-mode").value = sch.mode || "missing";
  $("#sch-last").textContent = d.schedule_last ? `ultimo: ${d.schedule_last}` : "";
  $("#rawg-key").placeholder = d.sources.find((x) => x.id === "rawg")?.has_key ? "chiave salvata (••••) — incolla per cambiarla" : "incolla la chiave";
  if (d.bulk_job.status === "running") pollBulk();
}

function renderSources(rows) {
  SET.srcRows = rows;
  $("#src-table").innerHTML = rows.map((x, i) => {
    const on = x.kind === "online";
    const num = (k, v) => on ? `<input class="field" data-lim="${k}" value="${v || ""}" placeholder="${k === "rate" ? "" : "nessuno"}">` : `<span class="muted">—</span>`;
    return `<tr data-src="${esc(x.id)}" class="${x.enabled ? "" : "src-off"}">
      <td><span class="src-move"><button class="icon-btn" data-mv="-1" title="Su">▲</button><button class="icon-btn" data-mv="1" title="Giù">▼</button></span></td>
      <td><label class="chk"><input type="checkbox" data-en ${x.enabled ? "checked" : ""}> <b>${esc(x.id)}</b></label>
        <span class="src-kind ${x.kind}">${x.kind}</span>${x.needs_key && !x.has_key ? ` <span class="status-bad">manca la chiave</span>` : ""}
        <div class="muted small">${esc(x.description)}</div></td>
      <td class="num">${on ? `${(x.usage.today || 0).toLocaleString("it")} / ${(x.usage.month || 0).toLocaleString("it")}` : "—"}</td>
      <td>${num("rate", x.policy.rate)}</td><td>${num("daily", x.policy.daily || "")}</td><td>${num("monthly", x.policy.monthly || "")}</td></tr>`;
  }).join("");
}

function collectSources() {
  const order = [], limits = {};
  document.querySelectorAll("#src-table tr[data-src]").forEach((tr) => {
    const id = tr.dataset.src;
    if ($("[data-en]", tr).checked) order.push(id);
    const lim = {};
    tr.querySelectorAll("[data-lim]").forEach((i) => { lim[i.dataset.lim] = i.value.trim(); });
    if (Object.keys(lim).length) limits[id] = lim;
  });
  const body = { order, limits, schedule: { enabled: $("#sch-on").checked, time: $("#sch-time").value, mode: $("#sch-mode").value } };
  if ($("#rawg-key").value.trim()) body.rawg_key = $("#rawg-key").value.trim();
  return body;
}

let bulkTimer = 0;
async function pollBulk() {
  clearTimeout(bulkTimer);
  const d = await api("/api/sources").catch(() => null);
  if (!d) return;
  $("#bulk-log").textContent = (d.bulk_job.log || []).slice(-8).join("\n");
  if (d.bulk_job.status === "running") bulkTimer = setTimeout(pollBulk, 1500);
  else if (SET.tab === "src") { loadSources(); if (d.bulk_job.status === "done") toast("Database aggiornato"); }
}

async function showPlan() {
  const box = $("#scan-plan-box");
  box.innerHTML = `<p class="muted">Calcolo della stima…</p>`;
  const sel = [...new Set([...document.querySelectorAll(".scan-sel:checked")].map((c) => c.dataset.p))];
  let d;
  try { d = await api("/api/scan/plan", { body: { platforms: sel, mode: $("#scan-mode").value } }); }
  catch (e) { box.innerHTML = ""; return toast(e.message, true); }
  const dur = (s) => (s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
  box.innerHTML = `<table class="emu plan"><tr><th>Fonte</th><th class="num">Giochi</th><th class="num">Richieste</th><th class="num">Tempo</th><th>Note</th></tr>`
    + d.plan.map((p) => `<tr><td>${esc(p.source)} ${p.offline ? `<span class="src-kind offline">offline</span>` : ""}</td>
      <td class="num">${p.games.toLocaleString("it")}</td><td class="num">${p.offline ? "0" : p.requests.toLocaleString("it")}</td>
      <td class="num">${p.offline ? "pochi secondi" : p.requests ? dur(p.seconds) : "—"}</td>
      <td class="muted small">${p.days > 1 ? `circa ${p.days} giorni (${esc(p.note || "limite giornaliero")})` : ""}
        ${p.month_left != null ? `restano ${p.month_left.toLocaleString("it")} richieste questo mese` : ""}</td></tr>`).join("")
    + `</table><p class="muted small">Valori massimi: in cascata le prime fonti riempiono i dati e le successive interrogano meno giochi.</p>`;
}


/* ---- store online per console */
async function openCatalog(pid) {
  SET.catPid = pid;
  $("#cat-title").textContent = `Store online · ${platName(pid)}`;
  const d = await api(`/api/catalogs/${pid}`);
  $("#cat-urls").value = d.pages.map((p) => p.url).join("\n");
  renderCatalog(d);
  $("#catalog-dlg").showModal();
  if (d.job.status === "running") pollCatalog();
}

function renderCatalog(d) {
  $("#cat-pages").innerHTML = d.pages.map((p) => `<div class="muted">${esc(p.url)} — ${p.games.toLocaleString("it")} giochi`
    + (p.time ? ` · letta ${new Date(p.time * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" })}` : " · mai letta") + `</div>`).join("");
  $("#cat-log").textContent = (d.job.log || []).slice(-10).join("\n");
}

let catTimer = 0;
async function pollCatalog() {
  clearTimeout(catTimer);
  const d = await api(`/api/catalogs/${SET.catPid}`).catch(() => null);
  if (!d) return;
  renderCatalog(d);
  if (d.job.status === "running") { catTimer = setTimeout(pollCatalog, 1200); return; }
  if (d.job.status === "done") { await reload(true); toast("Store online aggiornato"); }
  else if (d.job.status === "error") toast("Lettura non riuscita: vedi il registro", true);
}

/* ---- console personalizzate */
async function renderCustomConsoles() {
  const list = (await api("/api/platforms")).filter((p) => p.custom);
  $("#custom-consoles").innerHTML = list.length ? `<h3>Console aggiunte da te</h3>` + list.map((p) => `<div class="cust-row">
      <b>${esc(p.name)}</b><span class="muted grow">${esc(p.id)} · ${esc(p.exts)}${p.aliases.length ? " · cartelle: " + esc(p.aliases.join(", ")) : ""}</span>
      <button class="btn" data-cdel="${esc(p.id)}">Rimuovi</button></div>`).join("") : "";
}

/* ---- impostazioni › account degli store */
async function loadAccounts() {
  let d;
  try { d = await api("/api/accounts"); } catch (e) { return toast(e.message, true); }
  SET.accounts = d;
  const when = d.refreshed ? new Date(d.refreshed * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" }) : "mai";
  $("#acc-info").textContent = `Ultimo aggiornamento: ${when}`;
  $("#acc-table").innerHTML = d.stores.filter((x) => x.id !== "manual").map((x) => {
    const a = x.account || {};
    let ctrl = "";
    if (x.id === "steam") {
      const accs = (a.accounts || []).map((u) => `<option value="${esc(u.steamid)}" ${u.steamid === a.steamid ? "selected" : ""}>${esc(u.persona || u.steamid)}</option>`).join("");
      ctrl = `<div class="acc-steam">
        <select class="field" id="steam-id" title="Account Steam">${accs || `<option value="">nessun account trovato</option>`}</select>
        <input class="field grow" id="steam-key" placeholder="${a.has_key ? "chiave Web API salvata (••••)" : "chiave Web API, per vedere anche i giochi non installati"}">
        <button class="btn" data-acc-save="steam">Salva</button>
        <a href="#" data-open="https://steamcommunity.com/dev/apikey">Ottieni la chiave</a></div>`;
    } else if (x.login === "oauth") {
      ctrl = a.connected
        ? `<span class="status-ok">✓ collegato${a.user ? " come " + esc(a.user) : ""}</span> <button class="btn" data-acc-logout="${x.id}">Scollega</button>`
        : `<button class="btn primary" data-acc-login="${x.id}">Collega account</button>
           <div class="acc-manual" data-for="${x.id}" hidden>
             <ol class="small">${x.id === "epic"
               ? `<li>Nel browser che si è aperto accedi a Epic (va bene anche «Accedi con Google»).</li>
                  <li>Alla fine compare una pagina con del testo che contiene <code>"authorizationCode"</code>.</li>
                  <li>Copia <b>tutto il testo della pagina</b> (Ctrl+A, Ctrl+C) e incollalo qui sotto. Il codice scade in pochi minuti.</li>`
               : `<li>Nel browser che si è aperto accedi a GOG (va bene anche «Accedi con Google»).</li>
                  <li>Alla fine arrivi su una pagina quasi vuota: copia <b>l'indirizzo dalla barra del browser</b>
                      (contiene <code>code=</code>) e incollalo qui sotto.</li>`}</ol>
             <span class="row tight"><input class="field grow" placeholder="incolla qui">
             <button class="btn primary" data-acc-code="${x.id}">Conferma</button>
             <a href="#" data-acc-login="${x.id}">riapri la pagina di accesso</a></span></div>`;
    } else if (a.note) ctrl = `<span class="muted small">${esc(a.note)}</span>`;
    const st = x.detected ? `<span class="status-ok">✓ rilevato</span>` : `<span class="muted">non trovato</span>`;
    return `<tr><td><label class="chk"><input type="checkbox" data-acc-enable="${x.id}" ${x.enabled ? "checked" : ""}> <b>${esc(x.name)}</b></label>
      ${x.note ? `<div class="muted small">${esc(x.note)}</div>` : ""}</td><td>${st}</td>
      <td class="num">${x.installed} / ${x.games}</td><td>${ctrl}</td></tr>`;
  }).join("");
  renderJobInto($("#acc-progress"), d.job);
  if (d.job.status === "running") pollStores();
}

async function accountAction(t) {
  const id = t.dataset.accLogin || t.dataset.accLogout || t.dataset.accCode || t.dataset.accSave;
  try {
    if (t.dataset.accSave === "steam") {
      const body = { steamid: $("#steam-id").value };
      if ($("#steam-key").value.trim()) body.api_key = $("#steam-key").value.trim();
      await api("/api/accounts/steam", { body });
      toast("Account Steam salvato: aggiorno gli store…");
      await api("/api/stores/refresh", { body: { only: ["steam"] } });
      pollStores(true);
    } else if (t.dataset.accLogin) {
      const x = SET.accounts.stores.find((s) => s.id === id);
      // Il login si fa nel browser predefinito: le finestre integrate vengono bloccate da Google/Apple
      // ("Accedi con Google" non funzionerebbe). Poi si incolla qui il risultato.
      await api("/api/open", { body: { url: x.login_url } });
      const box = document.querySelector(`.acc-manual[data-for="${id}"]`);
      box.hidden = false;
      box.querySelector("input").focus();
      return; // niente ricaricamento: nasconderebbe il riquadro appena aperto
    } else if (t.dataset.accCode) {
      const v = document.querySelector(`.acc-manual[data-for="${id}"] input`).value.trim();
      if (!v) return toast("Incolla prima il codice o l'indirizzo copiato dal browser", true);
      await api(`/api/accounts/${id}/code`, { body: { code: v } });
      toast("Account collegato: aggiorno la libreria…");
      pollStores(true);
    } else if (t.dataset.accLogout) {
      await api(`/api/accounts/${id}/logout`, { body: {} });
    }
  } catch (e) { return toast(e.message, true); }
  loadAccounts();
}

/* ---- impostazioni › aspetto: temi e plugin */
async function loadAppearance() {
  let ext;
  try { ext = await api("/api/extensions"); } catch (e) { return toast(e.message, true); }
  $("#theme-list").innerHTML = ext.themes.map((t) => `<label class="theme-card ${t.id === ext.active_theme ? "on" : ""}">
      <input type="radio" name="theme" value="${esc(t.id)}" ${t.id === ext.active_theme ? "checked" : ""}>
      <span class="sw" style="background:${esc(t.preview || "#1c2030")}"></span>
      <b>${esc(t.name)}</b><span class="muted small">${esc(t.description || "")}${t.author ? " · " + esc(t.author) : ""}</span></label>`).join("");
  $("#plugin-list").innerHTML = ext.plugins.length ? ext.plugins.map((p) => `<div class="plugin-row">
      <label class="chk"><input type="checkbox" data-plugin="${esc(p.id)}" ${p.enabled ? "checked" : ""}> <b>${esc(p.name)}</b></label>
      <span class="muted small">v${esc(p.version || "?")}${p.author ? " · " + esc(p.author) : ""} — ${esc(p.description || "")}
      ${p.error ? `<span class="status-bad">${esc(p.error)}</span>` : ""}</span></div>`).join("")
    : `<p class="muted">Nessun plugin installato.</p>`;
  $("#ext-credit").textContent = S.iconsCredit;
  $("#ext-paths").innerHTML = `Temi: <code>${esc(ext.paths.themes)}</code><br>Plugin: <code>${esc(ext.paths.plugins)}</code>`;
}

async function enterBigPicture() {
  const pw = window.pywebview && window.pywebview.api;
  try { if (pw && pw.set_fullscreen) await pw.set_fullscreen(true); } catch { /* browser */ }
  location.href = "/bigpicture";
}

/* ------------------------------------------------------------------ toast, impostazioni, splash */
let toastTimer = 0;
function toast(msg, err = false) {
  const t = $("#toast");
  if (t.parentElement !== overlayHost()) overlayHost().appendChild(t);
  t.textContent = msg;
  t.className = err ? "err" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), err ? 7000 : 3500);
}

/* ------------------------------------------------------------------ impostazioni */
const SET = { tab: "scan", emu: null, preview: null, platforms: null, extraEmus: [], lastStatus: null };

function openSettings(tab) {
  const dlg = $("#settings");
  // schede aggiunte dai plugin
  for (const t of GS.ui.settingsTabs) {
    if (!document.querySelector(`#tabs [data-tab="ext:${CSS.escape(t.id)}"]`)) {
      $("#tabs").insertAdjacentHTML("beforeend", `<button type="button" class="tab" data-tab="ext:${esc(t.id)}">${esc(t.label)}</button>`);
      $(".dlg-body", dlg).insertAdjacentHTML("beforeend", `<section data-pane="ext:${esc(t.id)}"></section>`);
    }
  }
  if (!dlg.open) dlg.showModal();
  showTab(typeof tab === "string" ? tab : SET.tab);
}

function showTab(tab) {
  SET.tab = tab;
  document.querySelectorAll("#tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll("[data-pane]").forEach((p) => p.classList.toggle("active", p.dataset.pane === tab));
  if (tab === "scan") { loadScan(); renderCustomConsoles(); }
  else if (tab === "emu") loadEmu();
  else if (tab === "acc") loadAccounts();
  else if (tab === "src") loadSources();
  else if (tab === "look") loadAppearance();
  else if (tab.startsWith("ext:")) {
    const t = GS.ui.settingsTabs.find((x) => "ext:" + x.id === tab);
    const pane = document.querySelector(`[data-pane="${CSS.escape(tab)}"]`);
    if (t && pane) { pane.innerHTML = ""; Promise.resolve(t.render(pane, GS)).catch((e) => { pane.textContent = e.message; }); }
  } else api("/api/status").then(showStatus);
}

async function pickPath(kind) {
  const a = window.pywebview && window.pywebview.api;
  if (!a) { toast("La finestra «Sfoglia» funziona nell'app desktop: qui incolla il percorso nel campo.", true); return null; }
  try { return await (kind === "folder" ? a.pick_folder() : a.pick_file()); } catch (e) { toast(String(e), true); return null; }
}

/* ---- scraping di nuovi giochi e console */
async function loadScan() {
  $("#scan-table").innerHTML = `<p class="muted">Controllo delle cartelle…</p>`;
  const [pv, js] = await Promise.all([api("/api/scan/preview").catch((e) => ({ error: e.message })), api("/api/scan")]);
  if (!SET.platforms) SET.platforms = await api("/api/platforms");
  SET.preview = pv;
  renderScan(pv, js);
  if (js.status === "running") pollScan();
}

function renderScan(pv, js) {
  if (pv.error) { $("#scan-table").innerHTML = `<p class="status-bad">${esc(pv.error)}</p>`; return; }
  $("#scan-root").textContent = pv.root;
  const fmt = (n) => (n ? n.toLocaleString("it") : "—");
  $("#scan-table").innerHTML = `<table class="emu"><tr><th style="width:28px"><input type="checkbox" id="scan-all" title="Seleziona tutte"></th>`
    + `<th>Console</th><th>Cartella</th><th class="num">Giochi</th><th class="num">Nuovi</th>`
    + `<th class="num" title="Giochi che nessuna fonte ha riconosciuto">Senza dati</th>`
    + `<th class="num" title="Giochi riconosciuti a cui manca qualcosa (descrizione, immagini...)">Incompleti</th></tr>`
    + pv.platforms.map((r) => `<tr class="${r.new ? "new" : ""}"><td><input type="checkbox" class="scan-sel" data-p="${esc(r.platform)}"></td>`
      + `<td>${esc(r.name)}</td><td class="muted small">${esc(r.folder)}</td><td class="num">${r.games.toLocaleString("it")}</td>`
      + `<td class="num n">${fmt(r.new)}</td><td class="num warn">${fmt(r.nodata)}</td><td class="num">${fmt(r.partial)}</td></tr>`).join("") + `</table>`;
  applyScanMode();
  const opts = `<option value="">— di quale console? —</option>` + SET.platforms.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  $("#scan-unknown").innerHTML = pv.unknown.length
    ? `<div class="unknown"><b>Cartelle non riconosciute</b> <span class="muted small">— se contengono giochi, indica la console e verranno incluse:</span>`
      + pv.unknown.map((u) => `<div class="u"><code>${esc(u)}</code><select class="field map-folder" data-folder="${esc(u)}">${opts}</select></div>`).join("") + `</div>`
    : "";
  if (js) { setScanRunning(js.status === "running"); renderScanJob(js); }
}

// testo del pulsante: conta i giochi delle console spuntate (nessuna spunta = tutte le console).
// Nessuna console viene spuntata in automatico.
function applyScanMode() {
  const pv = SET.preview;
  if (!pv || pv.error) return;
  const mode = $("#scan-mode").value;
  const need = (r) => (mode === "new" ? r.new : mode === "missing" ? r.nodata + r.partial : r.games);
  const sel = new Set([...document.querySelectorAll(".scan-sel:checked")].map((c) => c.dataset.p));
  const rows = sel.size ? pv.platforms.filter((r) => sel.has(r.platform)) : pv.platforms;
  const tot = rows.reduce((a, r) => a + need(r), 0);
  const n = tot.toLocaleString("it");
  const where = sel.size ? ` · ${sel.size} ${sel.size === 1 ? "console" : "console"}` : " · tutte le console";
  $("#scan-start").textContent = (mode === "new" ? (tot ? `⟳ Cerca ${n} nuovi giochi` : "⟳ Cerca nuovi giochi")
    : mode === "missing" ? `⟳ Completa i dati mancanti${tot ? ` (${n} giochi)` : ""}` : "⟳ Riscarica tutto") + where;
}

function renderScanJob(js) {
  const label = { running: "In corso…", done: "Completato", cancelled: "Interrotto", error: "Errore" }[js.status] || "";
  const bars = (js.tasks || []).filter((t) => t.total > 0).map((t) => {
    const pct = Math.min(100, Math.round((100 * t.done) / t.total));
    return `<div class="bar"><span>${esc(t.name)}</span><div class="track"><div class="fill" style="width:${pct}%"></div></div>`
      + `<span class="n">${Math.round(t.done).toLocaleString("it")} / ${Math.round(t.total).toLocaleString("it")}</span></div>`;
  }).join("");
  $("#scan-progress").innerHTML = js.status === "idle" ? "" : `<div class="phase">${esc(js.phase || label)}</div><div class="bars">${bars}</div>`;
  const log = $("#scan-log");
  log.textContent = (js.log || []).join("\n");
  log.scrollTop = log.scrollHeight;
}

function setScanRunning(on) {
  $("#scan-start").hidden = on;
  $("#scan-cancel").hidden = !on;
  $("#scan-btn").classList.toggle("busy", on);
  $("#scan-btn").textContent = on ? "Scraping in corso…" : "⟳ Nuovi giochi";
}

let scanTimer = 0;
async function pollScan() {
  clearTimeout(scanTimer);
  let js;
  try { js = await api("/api/scan"); } catch { return; }
  setScanRunning(js.status === "running");
  if ($("#settings").open && SET.tab === "scan") renderScanJob(js);
  if (js.status === "running") scanTimer = setTimeout(pollScan, 1000);
  else if (SET.lastStatus === "running") { // appena terminato
    if (js.status === "done") {
      await reload(true);
      toast(js.title ? `Ricerca completata per «${js.title}»` : "Scraping completato: libreria aggiornata");
      if (S.detail) openDetail(S.detail.id);
    }
    else if (js.status === "error") toast(js.phase, true);
    else if (js.status === "cancelled") toast("Scraping interrotto: rilancialo per riprendere");
    if ($("#settings").open && SET.tab === "scan") loadScan();
  }
  SET.lastStatus = js.status;
}

async function startScan() {
  const sel = [...new Set([...document.querySelectorAll(".scan-sel:checked")].map((c) => c.dataset.p))];
  const mode = $("#scan-mode").value;
  if (mode === "all" && !confirm("Riscaricare i dati di TUTTI i giochi selezionati da tutte le fonti?\n"
    + "Consuma molta quota delle API (ScreenScraper, TheGamesDB) e richiede molto tempo.")) return;
  try { await api("/api/scan", { body: { platforms: sel, mode } }); }
  catch (e) { return toast(e.message, true); }
  SET.lastStatus = "running";
  setScanRunning(true);
  pollScan();
}

/* ---- emulatori */
async function loadEmu() {
  try { SET.emu = await api("/api/emulators"); } catch (e) { return toast(e.message, true); }
  renderEmu(SET.emu);
}

function collectEmu() {
  const emulators = {};
  document.querySelectorAll(".emu-path").forEach((i) => { emulators[i.dataset.emu] = i.value.trim(); });
  const platforms = {};
  document.querySelectorAll("#emu-table tr[data-p]").forEach((tr) => {
    platforms[tr.dataset.p] = { emulator: $(".emu-sel", tr).value.replace("__new", ""), core: $(".emu-core", tr).value, args: $(".emu-args", tr).value };
  });
  return { emulators, platforms, retroarch_cores: $("#ra-cores") ? $("#ra-cores").value : undefined };
}

function renderEmu(d, draft) {
  // draft = modifiche non ancora salvate da conservare quando si ridisegna
  const paths = { ...d.emulators, ...(draft ? draft.emulators : {}) };
  $("#emu-saved-to").textContent = "Le modifiche vanno in " + d.settings;
  const used = new Set(SET.extraEmus);
  d.platforms.forEach((p) => { p.candidates.forEach((c) => used.add(c.split(" ")[0])); if (p.emulator) used.add(p.emulator); });
  Object.keys(paths).forEach((k) => used.add(k));
  const names = [...used].sort();
  $("#emu-paths").innerHTML = `<table class="emu"><tr><th style="width:130px">Emulatore</th><th>Percorso dell'eseguibile</th><th style="width:96px"></th></tr>`
    + names.map((n) => `<tr><td>${esc(n)}</td><td><input class="field emu-path" data-emu="${esc(n)}" value="${esc(paths[n] || "")}" placeholder="non configurato"></td>`
      + `<td><button class="btn emu-pick" data-emu="${esc(n)}">Sfoglia…</button></td></tr>`).join("")
    + `<tr><td class="muted">core RetroArch</td><td><input class="field" id="ra-cores" value="${esc(draft && draft.retroarch_cores !== undefined ? draft.retroarch_cores : d.retroarch_cores || "")}" `
    + `placeholder="facoltativo: di solito la cartella «cores» accanto a retroarch.exe"></td><td></td></tr></table>`;
  const known = [...new Set([...d.known, ...names])].sort();
  $("#emu-table").innerHTML = `<table class="emu"><tr><th>Console</th><th style="width:160px">Emulatore</th><th style="width:150px">Core RetroArch</th><th>Argomenti</th><th style="width:180px">Stato</th></tr>`
    + d.platforms.map((p) => {
      const dp = draft && draft.platforms[p.platform] ? draft.platforms[p.platform] : { emulator: p.emulator, core: p.core, args: p.args };
      const rec = new Set(p.candidates.map((c) => c.split(" ")[0]));
      const opts = [`<option value="">Automatico</option>`,
        ...known.map((k) => `<option value="${esc(k)}" ${k === dp.emulator ? "selected" : ""}>${esc(k)}${rec.has(k) ? " ★" : ""}</option>`),
        `<option value="__new">Altro…</option>`].join("");
      const status = p.effective ? `<span class="status-ok">✓ ${esc(p.effective)}</span>` : `<span class="status-bad">${esc(p.problem || "non configurato")}</span>`;
      return `<tr data-p="${esc(p.platform)}"><td>${esc(p.name)}<div class="muted small">consigliati: ${esc(p.candidates.join(", ") || "—")}</div></td>`
        + `<td><select class="field emu-sel">${opts}</select></td>`
        + `<td><input class="field emu-core" value="${esc(dp.core)}" placeholder="${esc(p.default_core)}"></td>`
        + `<td><input class="field emu-args" value="${esc(dp.args)}" placeholder="${esc(p.default_args)}"></td><td>${status}</td></tr>`;
    }).join("") + `</table>`;
}

async function saveEmu(body) {
  try {
    SET.emu = await api("/api/emulators", { body: body || collectEmu() });
    SET.extraEmus = [];
    renderEmu(SET.emu);
    toast("Impostazioni degli emulatori salvate");
    if (S.detail) openDetail(S.detail.id);
  } catch (e) { toast(e.message, true); }
}

function showStatus(st) {
  const n = S.games.length;
  $("#lib-info").textContent = `${n.toLocaleString("it")} versioni, ${S.groups.length.toLocaleString("it")} giochi unici, ${S.colls.length} collezioni.`;
  $("#build-log").textContent = (st.build_log || []).join("\n");
  $("#rebuild-btn").disabled = st.building;
  $("#rebuild-btn").textContent = st.building ? "Aggiornamento in corso…" : "↻ Aggiorna libreria";
  $("#thumbs-info").textContent = st.thumbs_total ? `Miniature copertine: ${st.thumbs_done.toLocaleString("it")} / ${st.thumbs_total.toLocaleString("it")}` : "";
}

async function rebuild() {
  await api("/api/rebuild", { body: {} });
  const tick = async () => {
    const st = await api("/api/status");
    showStatus(st);
    if (st.building) setTimeout(tick, 1000);
    else { await reload(true); toast("Libreria aggiornata"); }
  };
  tick();
}

async function waitFirstBuild() {
  $("#splash").hidden = false;
  for (;;) {
    const st = await api("/api/status");
    $("#splash-log").textContent = (st.build_log || []).join("\n");
    if (st.has_library && !st.building) break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  $("#splash").hidden = true;
}

async function reload(keepScroll) {
  await loadLibrary();
  if (S.view.type === "collection" && !S.collBySlug.has(S.view.id)) S.view = { type: "all" };
  renderSidebar();
  refreshGrid(keepScroll);
}

/* ------------------------------------------------------------------ eventi */
function bind() {
  $("#nav").addEventListener("click", (e) => {
    const a = e.target.closest(".nav-item");
    if (!a) return;
    if (a.dataset.pluginItem && !a.dataset.view) {
      const it = GS.ui.sidebarItems.find((x) => x.id === a.dataset.pluginItem);
      try { it?.onClick?.(GS); } catch (err) { toast(err.message, true); }
      return;
    }
    setView({ type: a.dataset.view, id: a.dataset.id || null });
  });
  $("#inst-filter").value = S.instFilter;
  $("#inst-filter").onchange = (e) => { S.instFilter = e.target.value; store.set("instFilter", S.instFilter); refreshGrid(); };
  $("#pc-add").onclick = () => openAddGame();
  $("#bp-btn").onclick = () => enterBigPicture();
  GS.on("uiChanged", () => { if (S.games.length) { renderSidebar(); } });
  layer.addEventListener("click", (e) => { const t = e.target.closest(".tile"); if (t) openDetail(+t.dataset.id); });
  layer.addEventListener("dblclick", (e) => { const t = e.target.closest(".tile"); if (t) play(+t.dataset.id); });
  scroller.addEventListener("scroll", scheduleRender, { passive: true });
  new ResizeObserver(() => { layout(); render(); }).observe(scroller);

  let st = 0;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(st);
    st = setTimeout(() => { S.search = e.target.value.trim(); refreshGrid(); }, 120);
  });
  $("#sort").value = S.sort;
  for (const k of ["preset", "genre", "decade"]) {
    $(`#disc-${k}`).onchange = (e) => { S.disc[k] = e.target.value; store.set("disc", S.disc); e.target.classList.toggle("on", !!e.target.value); refreshGrid(); };
  }
  for (const k of ["src", "plat", "size"]) {
    $(`#shop-${k}`).onchange = (e) => { S.shopF[k] = e.target.value; store.set("shopF", S.shopF); refreshGrid(); };
  }
  $("#sort").onchange = (e) => { S.sort = e.target.value; store.set("sort", S.sort); refreshGrid(); };
  $("#group").value = S.group;
  $("#group").onchange = (e) => { S.group = e.target.value; store.set("group", S.group); refreshGrid(); };
  $("#one-version").checked = S.oneVersion;
  $("#one-version").onchange = (e) => { S.oneVersion = e.target.checked; store.set("oneVersion", S.oneVersion); renderSidebar(); refreshGrid(); };
  $("#zoom").value = S.zoom;
  $("#zoom").oninput = (e) => {
    S.zoom = +e.target.value; store.set("zoom", S.zoom);
    const anchor = scroller.scrollTop / Math.max(1, S.total);
    for (const el of S.nodes.values()) el.remove();
    S.nodes.clear();
    layout(); scroller.scrollTop = anchor * S.total; render();
  };
  $("#coll-filter").addEventListener("input", (e) => { S.collFilter = e.target.value; renderCollList(); });
  $("#coll-sort").onclick = () => { S.collSort = S.collSort === "name" ? "count" : "name"; store.set("collSort", S.collSort); renderCollList();
    toast(S.collSort === "name" ? "Collezioni in ordine alfabetico" : "Collezioni per numero di giochi"); };
  $("#coll-new").onclick = async (e) => {
    const name = await askText(e.currentTarget, "Nome della nuova collezione");
    if (!name) return;
    try { const r = await api("/api/collections", { body: { name } }); await reload(true); setView({ type: "collection", id: r.slug }); }
    catch (err) { toast(err.message, true); }
  };
  $("#view-actions").addEventListener("click", async (e) => {
    if (e.target.id === "cat-open") { openCatalog(S.view.id); return; }
    if (e.target.closest("#smart-edit")) { openSmart(S.view.id); return; }
    if (e.target.closest("#smeta-open")) { $("#smeta").showModal(); pollStoreMeta(); return; }
    if (e.target.id === "dup-toggle") {
      const r = await api("/api/app/duplicates", { body: { skip: !S.skipDup } });
      S.skipDup = r.skip_duplicates;
      updateHeader();
      toast(S.skipDup ? "I duplicati non verranno più scrapati: usano i metadati della versione principale"
        : "I duplicati verranno scrapati come gli altri giochi");
      return;
    }
    const c = S.collBySlug.get(S.view.id);
    if (!c) return;
    if (e.target.id === "coll-rename") {
      const name = await askText(e.target, "Nuovo nome", c.name);
      if (name) { await api(`/api/collections/${encodeURIComponent(c.slug)}`, { method: "PATCH", body: { name } }); await reload(true); }
    } else if (e.target.id === "coll-delete") {
      if (!confirm(c.custom ? `Eliminare la collezione «${c.name}»?` : `Nascondere la collezione automatica «${c.name}»?`)) return;
      await api(`/api/collections/${encodeURIComponent(c.slug)}`, { method: "DELETE" });
      S.view = { type: "all" }; await reload(false);
    }
  });

  $("#detail-close").onclick = closeDetail;
  $("#detail").addEventListener("click", async (e) => {
    const d = S.detail;
    if (!d) return;
    const t = e.target;
    if (t.id === "d-play") play(d.id);
    else if (t.id === "d-install") installGame(d.id);
    else if (t.id === "d-torrent") api(`/api/game/${d.id}/torrent`, { body: {} }).then(() => toast("Link aperto nel client torrent"))
      .catch((err) => toast(err.message, true));
    else if (t.dataset.install) installGame(+t.dataset.install);
    else if (t.id === "d-page") api(`/api/game/${d.id}/store-page`, { body: {} }).catch((err) => toast(err.message, true));
    else if (t.id === "d-manual-del") {
      if (!confirm(`Rimuovere «${d.title}» dalla libreria? Il gioco sul disco non viene toccato.`)) return;
      try { await api(`/api/manual/${encodeURIComponent(d.key)}`, { method: "DELETE" }); } catch (err) { return toast(err.message, true); }
      closeDetail();
      pollStores(true);
    } else if (t.dataset.ga) {
      const a = GS.ui.gameActions.find((x) => x.id === t.dataset.ga);
      try { await a.run(d, GS); } catch (err) { toast(err.message, true); }
    }
    else if (t.id === "d-fav") setFlag("favorite", !d.user.favorite);
    else if (t.dataset.status) setFlag("status", d.user.status === t.dataset.status ? "" : t.dataset.status);
    else if (t.dataset.open) { e.preventDefault(); api("/api/open", { body: { url: t.dataset.open } }); }
    else if (t.id === "d-hide") setFlag("hidden", !d.user.hidden);
    else if (t.id === "d-folder") api(`/api/game/${d.id}/open-folder`, { body: {} }).catch((err) => toast(err.message, true));
    else if (t.id === "d-emu-cfg") { e.preventDefault(); openSettings("emu"); }
    else if (t.id === "d-profile") { e.preventDefault(); openProfile(d); }
    else if (t.id === "d-shots-all") { e.preventDefault(); openLightbox(SHOTS.detail, 0); }
    else if (t.closest("#d-myshots img")) { openLightbox(SHOTS.detail, +t.closest("img").dataset.i); }
    else if (t.id === "d-scrape") {
      try { await api("/api/scan", { body: { game: d.id, mode: "missing" } }); }
      catch (err) { return toast(err.message, true); }
      t.disabled = true;
      t.textContent = "Ricerca in corso…";
      toast(`Cerco i dati mancanti di «${d.title}»…`);
      SET.lastStatus = "running";
      pollScan();
    }
    else if (t.id === "d-add-coll") pickCollection(t, d.id, d.collections.map((c) => c.slug));
    else if (t.classList.contains("x")) {
      const slug = t.closest(".chip").dataset.slug;
      try { await api(`/api/collections/${encodeURIComponent(slug)}/members`, { body: { game: d.id, action: "remove" } }); } catch (err) { return toast(err.message, true); }
      await reload(true); openDetail(d.id);
    } else if (t.closest(".chip[data-slug]")) { setView({ type: "collection", id: t.closest(".chip").dataset.slug }); }
    else if (t.dataset.play) play(+t.dataset.play);
    else if (t.dataset.pref) {
      await api(`/api/game/${t.dataset.pref}/preferred`, { body: {} });
      await reload(true); openDetail(+t.dataset.pref);
    }
  });

  $("#settings-btn").onclick = () => openSettings();
  $("#scan-btn").onclick = () => openSettings("scan");
  $("#rebuild-btn").onclick = rebuild;
  $("#tabs").addEventListener("click", (e) => { const b = e.target.closest(".tab"); if (b) showTab(b.dataset.tab); });

  // scraping
  $("#scan-start").onclick = startScan;
  $("#scan-cancel").onclick = () => api("/api/scan/cancel", { body: {} }).then(() => toast("Interruzione in corso…"));
  $("#scan-reload").onclick = loadScan;
  $("#scan-mode").onchange = applyScanMode;
  $("#scan-table").addEventListener("change", (e) => {
    if (e.target.id === "scan-all") document.querySelectorAll(".scan-sel").forEach((c) => { c.checked = e.target.checked; });
    if (e.target.id === "scan-all" || e.target.classList?.contains("scan-sel")) applyScanMode();
  });
  $("#scan-unknown").addEventListener("change", async (e) => {
    const s = e.target.closest(".map-folder");
    if (!s || !s.value) return;
    try {
      const pv = await api("/api/folders", { body: { folder: s.dataset.folder, platform: s.value } });
      renderScan(pv, null);
      toast(`«${s.dataset.folder}» associata: ora puoi cercarne i giochi`);
    } catch (err) { toast(err.message, true); }
  });

  // store online per console
  $("#cat-save").onclick = async () => {
    const urls = $("#cat-urls").value.split(/\s+/).filter(Boolean);
    try { renderCatalog(await api(`/api/catalogs/${SET.catPid}`, { body: { urls } })); } catch (e) { return toast(e.message, true); }
    if (urls.length) pollCatalog(); else toast("Store online scollegato");
  };
  $("#cat-refresh").onclick = async () => {
    try { await api(`/api/catalogs/${SET.catPid}/refresh`, { body: {} }); } catch (e) { return toast(e.message, true); }
    pollCatalog();
  };
  // console personalizzate
  $("#console-add").onclick = () => { ["cn-name", "cn-id", "cn-exts", "cn-aliases", "cn-igdb"].forEach((i) => { $("#" + i).value = ""; }); $("#console-dlg").showModal(); };
  $("#cn-name").oninput = (e) => { $("#cn-id").placeholder = e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "") || "es. pc8801"; };
  $("#cn-save").onclick = async () => {
    const body = { name: $("#cn-name").value, id: $("#cn-id").value || $("#cn-id").placeholder.replace(/^es\. /, ""),
      exts: $("#cn-exts").value, aliases: $("#cn-aliases").value.split(","), cd: $("#cn-cd").checked,
      dir_games: $("#cn-dir").checked, igdb: $("#cn-igdb").value };
    try { await api("/api/platforms/custom", { body }); } catch (e) { return toast(e.message, true); }
    $("#console-dlg").close();
    SET.platforms = null;
    toast(`Console «${body.name}» aggiunta: ora puoi associarle una cartella e cercare i giochi`);
    loadScan(); renderCustomConsoles();
  };
  $("#custom-consoles").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-cdel]");
    if (!b || !confirm("Rimuovere questa console? I giochi già in libreria restano finché non aggiorni la libreria.")) return;
    await api(`/api/platforms/custom/${b.dataset.cdel}`, { method: "DELETE" });
    SET.platforms = null;
    loadScan(); renderCustomConsoles();
  });

  // fonti dei dati
  $("#src-table").addEventListener("click", (e) => {
    const b = e.target.closest("[data-mv]");
    if (!b) return;
    const tr = b.closest("tr"), dir = +b.dataset.mv;
    const sib = dir < 0 ? tr.previousElementSibling : tr.nextElementSibling;
    if (sib) (dir < 0 ? sib.before(tr) : sib.after(tr));
  });
  $("#src-table").addEventListener("change", (e) => {
    if (e.target.matches("[data-en]")) e.target.closest("tr").classList.toggle("src-off", !e.target.checked);
  });
  $("#src-save").onclick = async () => {
    try { SET.sources = await api("/api/sources", { body: collectSources() }); } catch (e) { return toast(e.message, true); }
    $("#rawg-key").value = "";
    loadSources();
    toast("Fonti salvate: valgono dal prossimo scraping");
  };
  $("#bulk-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-bulk]");
    if (!b) return;
    try { await api(`/api/bulk/${b.dataset.bulk}/refresh`, { body: {} }); } catch (err) { return toast(err.message, true); }
    toast("Download del database avviato");
    pollBulk();
  });
  $('[data-pane="src"]').addEventListener("click", (e) => {
    const a = e.target.closest("[data-open]");
    if (a) { e.preventDefault(); api("/api/open", { body: { url: a.dataset.open } }); }
  });
  $("#scan-plan").onclick = showPlan;
  $("#setup-open").onclick = async () => { $("#settings").close(); await SETUP.open(true); await reload(true); };
  $("#roms-change").onclick = async () => {
    let p = window.pywebview?.api ? await pickPath("folder") : prompt("Percorso della cartella delle ROM", $("#scan-root").textContent);
    if (!p) return;
    try { await api("/api/roms", { body: { path: p } }); } catch (e) { return toast(e.message, true); }
    toast("Cartella delle ROM aggiornata");
    loadScan();
  };
  document.querySelector('[data-tab="src"]')?.addEventListener("click", () => { pollHltb(); pollCollart(); });
  $("#collart-missing").onclick = () => startCollart("missing");
  $("#collart-all").onclick = () => startCollart("all");
  $("#collart-stop").onclick = () => api("/api/collart/cancel", { body: {} });
  $("#smeta-all").onclick = () => startStoreMeta(false);
  $("#prof-dlg").addEventListener("input", pfRefresh);
  $("#prof-dlg").addEventListener("change", pfRefresh);
  $("#pf-cancel").onclick = () => $("#prof-dlg").close();
  $("#pf-save").onclick = () => saveProfile(false);
  $("#pf-reset").onclick = () => saveProfile(true);
  $("#pf-exe-pick").onclick = async () => { const p = await pickPath("file"); if (p) { $("#pf-exe").value = p; pfRefresh(); } };
  $("#running").addEventListener("click", (e) => { if (e.target.closest("#run-shot")) api("/api/running/screenshot", { body: {} }); });
  api("/api/shots").then((d) => {
    $("#cnt-shots").textContent = d.shots.length || "";
    SHOTS.hotkey = d.settings.hotkey ? d.settings.hotkey.split("+").map((k) => k[0].toUpperCase() + k.slice(1)).join("+") : "—";
  }).catch(() => {});
  $("#smart-new").onclick = () => openSmart();
  $("#smart-dlg").addEventListener("click", (e) => {
    const c = e.target.closest(".sm-chip");
    if (!c) return;
    const list = new Set(smEdit.rules[c.dataset.k] || []);
    list.has(c.dataset.v) ? list.delete(c.dataset.v) : list.add(c.dataset.v);
    smEdit.rules[c.dataset.k] = [...list];
    if (!smEdit.rules[c.dataset.k].length) delete smEdit.rules[c.dataset.k];
    c.classList.toggle("on");
    smCount();
  });
  $("#smart-dlg").addEventListener("input", smCount);
  $("#sm-cancel").onclick = () => $("#smart-dlg").close();
  $("#sm-save").onclick = () => saveSmart(false);
  $("#sm-delete").onclick = () => { if (confirm("Eliminare questa collezione intelligente? I giochi non vengono toccati.")) saveSmart(true); };
  $("#hltb-missing").onclick = () => startHltb("missing");
  $("#hltb-all").onclick = () => startHltb("all");
  $("#hltb-stop").onclick = () => api("/api/hltb/cancel", { body: {} });
  pollWishBadge();
  setTimeout(() => api("/api/free").then((d) => { $("#cnt-free").textContent = d.items.filter((i) => !i.upcoming && !i.owned).length || ""; }).catch(() => {}), 4000);
  $("#smeta-missing").onclick = () => startStoreMeta(true);
  $("#smeta-close").onclick = () => $("#smeta").close();
  $("#smeta-stop").onclick = () => api("/api/stores/metadata/cancel", { body: {} });

  // account degli store
  $("#acc-refresh").onclick = async () => {
    try { await api("/api/stores/refresh", { body: {} }); } catch (e) { return toast(e.message, true); }
    pollStores(true);
  };
  $("#acc-table").addEventListener("click", (e) => {
    const t = e.target.closest("[data-acc-login],[data-acc-logout],[data-acc-code],[data-acc-save],[data-open]");
    if (!t) return;
    e.preventDefault();
    if (t.dataset.open) api("/api/open", { body: { url: t.dataset.open } }).catch((err) => toast(err.message, true));
    else accountAction(t);
  });
  $("#acc-table").addEventListener("change", async (e) => {
    const c = e.target.closest("[data-acc-enable]");
    if (c) { await api(`/api/accounts/${c.dataset.accEnable}/enabled`, { body: { enabled: c.checked } }); toast("Salvato: vale dal prossimo aggiornamento"); }
  });
  // aspetto: temi e plugin
  $("#theme-list").addEventListener("change", async (e) => {
    if (e.target.name !== "theme") return;
    await api("/api/app/theme", { body: { theme: e.target.value } });
    await loadExtensions();
    loadAppearance();
  });
  $("#plugin-list").addEventListener("change", async (e) => {
    const c = e.target.closest("[data-plugin]");
    if (!c) return;
    await api("/api/app/plugin", { body: { id: c.dataset.plugin, enabled: c.checked } });
    toast("Salvato: riavvia l'app per applicare la modifica ai plugin");
  });
  // aggiungi gioco PC
  $("#ag-pick").onclick = async () => {
    const p = await pickPath("file");
    if (p) {
      $("#ag-exe").value = p;
      if (!$("#ag-title").value) $("#ag-title").value = p.split(/[\/]/).slice(-2, -1)[0] || "";
    }
  };
  $("#ag-save").onclick = saveAddGame;

  // emulatori
  $("#emu-save").onclick = () => saveEmu();
  $("#emu-folder-pick").onclick = async () => { const p = await pickPath("folder"); if (p) $("#emu-folder").value = p; };
  $("#emu-detect").onclick = async () => {
    const folder = $("#emu-folder").value.trim();
    if (!folder) return toast("Indica prima la cartella in cui hai installato gli emulatori", true);
    try {
      const d = await api("/api/emulators/detect", { body: { folder } });
      SET.emu = d;
      renderEmu(d);
      const n = Object.keys(d.found).length;
      toast(n ? `Trovati e salvati: ${Object.keys(d.found).join(", ")}` : "Nessun emulatore conosciuto in questa cartella", !n);
    } catch (err) { toast(err.message, true); }
  };
  $("#emu-add").onclick = async (e) => {
    const name = await askText(e.currentTarget, "Nome dell'emulatore, es. mgba");
    if (!name) return;
    const draft = collectEmu();
    SET.extraEmus.push(name.toLowerCase());
    renderEmu(SET.emu, draft);
  };
  $("#emu-paths").addEventListener("click", async (e) => {
    const b = e.target.closest(".emu-pick");
    if (!b) return;
    const p = await pickPath("file");
    if (p) document.querySelector(`.emu-path[data-emu="${CSS.escape(b.dataset.emu)}"]`).value = p;
  });
  $("#emu-table").addEventListener("change", async (e) => {
    const s = e.target.closest(".emu-sel");
    if (!s || s.value !== "__new") return;
    const name = await askText(s, "Nome dell'emulatore, es. mgba");
    const draft = collectEmu();
    if (name) {
      SET.extraEmus.push(name.toLowerCase());
      draft.platforms[s.closest("tr").dataset.p].emulator = name.toLowerCase();
    }
    renderEmu(SET.emu, draft);
  });
  document.addEventListener("mousedown", (e) => {
    if (!$("#popover").hidden && !e.target.closest("#popover") && !e.target.closest("#d-add-coll, #coll-new, #view-actions")) closePopover();
  });
  document.addEventListener("keydown", (e) => {
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if (e.key === "/" && !typing) { e.preventDefault(); $("#search").focus(); }
    else if (e.key === "Escape") {
      if (!$("#popover").hidden) closePopover();
      else if (S.detail) closeDetail();
      else if (S.search) { $("#search").value = ""; S.search = ""; refreshGrid(); }
    } else if (e.key === "Enter" && S.detail && !typing) play(S.detail.id);
  });
}

/* ------------------------------------------------------------------ avvio */
GS.toast = (m, err) => toast(m, err);
GS.openGame = (id) => openDetail(id);
GS.library = () => S;
GS.currentGame = () => S.detail;
GS.refresh = () => reload(true);

(async function main() {
  bind();
  try {
    await SETUP.open();  // primo avvio: configurazione guidata (non fa nulla se gia' configurato)
    if (!(await loadLibrary())) {
      const st = await api("/api/status");
      if (!st.has_library) { await waitFirstBuild(); await loadLibrary(); }
    }
  } catch (e) {
    toast("Errore nel caricamento della libreria: " + e.message, true);
    return;
  }
  await loadExtensions();
  if (S.view.type === "collection" && !S.collBySlug.has(S.view.id)) S.view = { type: "all" };
  if (S.view.type === "platform" && !S.plats.has(S.view.id)) S.view = { type: "all" };
  if (S.view.type === "plugin" && !GS.ui.views.has(S.view.id)) S.view = { type: "all" };
  renderSidebar();
  setView(S.view);
  pollRunning();
  pollScan(); // se uno scraping era gia' in corso, ne mostra lo stato
})();
