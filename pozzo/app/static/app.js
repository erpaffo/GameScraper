"use strict";
/* Pozzo Launcher - frontend.
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
  nodes: new Map(), selected: null, detail: null, running: null, openSeq: 0,
  instFilter: store.get("instFilter", "all"), stores: [],
  allLocal: store.get("allLocal", false), // Tutti i giochi: solo quelli presenti sul PC, divisi per console
  disc: store.get("disc", { preset: "", genre: "", decade: "" }),
  shopF: store.get("shopF", { src: "", plat: "", size: "" }), shop: [], shopIds: new Set(),
};

// prima i giochi posseduti (pochi, subito), poi in sottofondo il catalogo degli store online
let onlineSeq = 0;
async function loadLibrary() {
  const seq = ++onlineSeq;
  const lib = await api("/api/library?part=owned");
  // stessa libreria (stessi id): il catalogo gia' caricato resta visibile finche' arriva quello nuovo
  const prev = lib.built_at === S.builtAt ? S.games.slice(S.nOwned?.[0] ?? S.games.length) : [];
  Object.assign(S, decodeLibrary(lib));
  S.nOwned = [S.games.length, S.groups.length];
  addOnline(prev);
  S.skipDup = lib.skip_duplicates !== false;
  S.hideDlc = new Set(lib.hide_dlc || []);
  S.userRev = lib.user_rev;
  S.metaRev = lib.meta_rev;
  S.catalogs = lib.catalogs || {};
  S.iconsCredit = lib.icons_credit || "";
  RECO.setPlatName(platName);
  S.smart = S.smart || [];
  libraryChanged();
  loadOnline(seq);
  return lib.games.rows.length > 0;
}

function libraryChanged() {
  S.shop = shopGames(S.games);
  renderDiscTools();
  S.shopIds = new Set(S.shop.map((g) => g.id));
  S.hasOnline = new Set(S.games.filter((g) => g.store === "online").map((g) => g.platform));
  GS.emit("libraryLoaded", S);
}

// accoda i giochi del catalogo dopo quelli posseduti, rinumerando i gruppi (gi) senza collisioni
function addOnline(games) {
  const gi = new Map();
  for (const g of games) {
    if (!gi.has(g.gi)) { gi.set(g.gi, S.groups.length); S.groups.push(g.grp); }
    g.gi = gi.get(g.gi);
    S.byId.set(g.id, g);
    S.games.push(g);
  }
}

async function loadOnline(seq) {
  if (S.games.length === S.nOwned[0]) loading("Caricamento dei giochi degli store…");
  try {
    const d = decodeLibrary(await api("/api/library?part=online"));
    if (seq !== onlineSeq) return; // nel frattempo la libreria e' stata ricaricata
    for (const g of S.games.slice(S.nOwned[0])) S.byId.delete(g.id);
    S.games.length = S.nOwned[0];
    S.groups.length = S.nOwned[1];
    addOnline(d.games);
    libraryChanged();
    renderSidebar();
    refreshGrid(true);
  } catch (e) {
    if (seq === onlineSeq) toast("Giochi degli store non caricati: " + e.message, true);
  } finally {
    if (seq === onlineSeq) loading(null);
  }
}

function loading(text) {
  $("#loadbar").hidden = !text;
  $("#loadbar-text").textContent = text || "";
}

/* ------------------------------------------------------------------ filtri e ordinamento */
const platName = (id) => S.plats.get(id)?.name || id;
// le applicazioni aggiunte a mano hanno questo genere (vedi build.py: APP_GENRE)
const APP_GENRE = "Applicazione";
const isApp = (g) => g.store === "manual" && String(g.genres || "").split(", ").includes(APP_GENRE);

function inView(g, v) {
  const grp = g.grp;
  if (v.type === "shop") return S.shopIds.has(g.id);
  if (g.store === "online" && v.type !== "platform") return false;
  if (v.type === "dups") return g.id !== grp.primary && !grp.hidden;
  if (S.oneVersion && g.id !== grp.primary && !(v.type === "store" && v.id !== "all")) return false;
  if (v.type !== "hidden" && grp.hidden) return false;
  switch (v.type) {
    case "favorites": return !!grp.favorite && (!v.plat || g.platform === v.plat);
    case "apps": return isApp(g);
    case "recent": return grp.last > 0;
    case "multi": return g.players >= 2;
    case "status": return (grp.status || "") === v.id;
    case "smart": { const sc = S.smart.find((x) => x.id === v.id); return !!sc && g.id === grp.primary && smartMatch(g, sc.rules); }
    case "platform": if (g.platform !== v.id) return false; break;
    case "store": if (g.platform !== "windows" || (v.id !== "all" && g.store !== v.id)) return false; break;
    case "collection": { const c = S.collBySlug.get(v.id); return !!c && grp.colls.includes(c.idx); }
    case "missing": return !g.found;
    case "hidden": return !!grp.hidden;
    case "all": if (S.allLocal && g.store && !g.installed) return false; break; // le ROM sono sempre sul disco
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
  // DLC nascoste per console (check box nella vista della console) e aggiornamenti: mai come riquadri,
  // stanno tra i download del gioco base (vedi build.py: _attach_updates)
  it = it.filter((g) => g.content !== "update" && !(g.content === "dlc" && S.hideDlc?.has(g.platform)));
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
  const group = S.view.type === "all" && S.allLocal ? "platform" : S.group;
  if (!group) return [{ label: null, items }];
  const keyOf = {
    platform: (g) => platName(g.platform),
    year: (g) => (g.year ? String(g.year) : "Anno sconosciuto"),
    letter: (g) => { const c = (g.sort || "#")[0].toUpperCase(); return /[A-Z]/.test(c) ? c : "#"; },
  }[group];
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
    : !$("#loadbar").hidden ? ""
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
    + (g.content === "dlc" ? `<span class="badge ext" title="Contenuto aggiuntivo (DLC/add-on): non è un gioco a sé">DLC</span>` : "")
    + (g.players >= 2 ? `<span class="badge ext" title="Multigiocatore locale">👥 ${g.players}</span>` : "")
    + (g.store === "online" ? `<span class="badge inst">🌐 Scarica${g.size ? " · " + fmtSize(g.size) : ""}</span>`
      : g.store && !g.installed ? `<span class="badge inst">⬇ Installa</span>` : "")
    + GS.ui.tileBadges.map((b) => { try { const t = b.render(g); return t ? `<span class="badge ext">${esc(t)}</span>` : ""; } catch { return ""; } }).join("");
  el.innerHTML = `<div class="cover" style="--h:${hue(g.title)}"><div class="ph">${esc(g.title)}<small>${esc(pn)}</small></div>`
    + (g.cover ? `<img alt="" decoding="async">` : "") + badges + `</div>`
    + `<div class="t" title="${esc(g.title)}">${esc(g.title)}</div><div class="s">${esc(sub)}</div>`;
  if (g.cover) {
    const img = el.querySelector("img");
    img.onload = () => img.classList.add("ok");
    // q=1: una copertina remota non ancora scaricata non tiene occupato il collegamento (arriva con applyPatches)
    const src = `/img/${g.id}/cover?w=${Math.round(S.tw * (window.devicePixelRatio || 1))}&q=1`;
    let tries = 0;
    // un errore puo' essere passeggero (app che riparte, disco occupato) o dovuto a numeri di gioco vecchi dopo una
    // ricostruzione della libreria: si ricontrolla la libreria e si riprova prima di rinunciare
    img.onerror = () => {
      if (++tries > 2) return img.remove();
      syncLibrary();
      setTimeout(() => { if (img.isConnected) img.src = `${src}&r=${tries}`; }, 1200 * tries);
    };
    img.src = src;
  }
  return el;
}

/* La libreria nella pagina puo' diventare vecchia: una ricostruzione fatta altrove (scansione, CLI, altra finestra)
   cambia i numeri dei giochi, e copertine e dati non si trovano piu'. Si confronta la data di costruzione. */
let syncBusy = false;
async function syncLibrary() {
  if (syncBusy || !S.games.length) return;
  syncBusy = true;
  try {
    const st = await api("/api/status");
    // libreria ricostruita altrove, oppure dati utente cambiati da un altro dispositivo (partita avviata dal telefono...)
    if (!st.building && ((st.built_at && st.built_at !== S.builtAt) || (st.user_rev != null && st.user_rev !== S.userRev))) await reload(true);
    else if (st.meta_rev != null && st.meta_rev !== S.metaRev) await applyPatches();
    if (["starting", "extracting", "running"].includes(st.running) && $("#running").hidden) pollRunning();
  } catch { /* server non raggiungibile: si riprova al prossimo controllo */ } finally { syncBusy = false; }
}

/* Copertine e dati trovati mentre un lavoro e' in corso (o copertine appena scaricate): solo i giochi cambiati,
   senza ricaricare la libreria. I riquadri visibili si ricreano e compaiono un po' alla volta. */
async function applyPatches() {
  const d = await api(`/api/library/patches?since=${S.metaRev || 0}`);
  if (d.reset) return reload(true);
  S.metaRev = d.rev;
  for (const [id, cover, year, dev, genres] of d.games) {
    const g = S.byId.get(id);
    if (!g) continue;
    Object.assign(g, { cover, year, dev, genres });
    g.q = fold(`${g.title} ${g.dev} ${platName(g.platform)} ${g.platform} ${g.store || ""}`);
    const el = S.nodes.get(id);
    if (el) { el.remove(); S.nodes.delete(id); }
  }
  if (d.games.length) render();
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
  const c = { all: 0, fav: 0, recent: 0, missing: 0, hidden: 0, apps: 0,
    plat: new Map(), coll: new Map(), store: new Map(), favPlat: new Map() };
  for (const g of S.games) {
    const grp = g.grp;
    if (S.oneVersion && g.id !== grp.primary) continue;
    if (g.store === "online") continue; // disponibili online: non sono giochi posseduti
    if (grp.hidden) { c.hidden++; continue; }
    c.all++;
    if (isApp(g)) c.apps++;
    if (grp.favorite) {
      c.fav++;
      c.favPlat.set(g.platform, (c.favPlat.get(g.platform) || 0) + 1);
    }
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

/* console preferite: salvate in questo browser/app (come zoom e ordinamento) */
const favPlat = (id) => (store.get("favPlats", []).includes(id) ? 1 : 0);
function toggleFavPlat(id) {
  const cur = store.get("favPlats", []);
  store.set("favPlats", cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);
  renderSidebar();
}

function openPlatCtx(e, id) {
  closeCtx();
  const m = document.createElement("div");
  m.id = "ctxmenu";
  m.innerHTML = `<div class="ci">${favPlat(id) ? "★ Togli dalle console preferite" : "☆ Aggiungi alle console preferite"}</div>`
    + `<div class="ci" data-scan>⟳ Cerca nuovi giochi</div>`;
  document.body.appendChild(m);
  m.style.left = Math.max(6, Math.min(e.clientX, innerWidth - m.offsetWidth - 8)) + "px";
  m.style.top = Math.max(6, Math.min(e.clientY, innerHeight - m.offsetHeight - 8)) + "px";
  m.onclick = async (ev) => {
    const it = ev.target.closest(".ci");
    if (!it) return;
    closeCtx();
    if (!("scan" in it.dataset)) return toggleFavPlat(id);
    try { await api("/api/scan", { body: { platforms: [id], mode: "new" } }); } catch (err) { return toast(err.message, true); }
    toast("Cerco i giochi nuovi: compaiono subito, i dati arrivano dopo");
    SET.lastStatus = "running"; setScanRunning(true); pollScan();
  };
}

function renderSidebar() {
  const c = counts();
  $("#cnt-all").textContent = c.all.toLocaleString("it");
  $("#cnt-fav").textContent = c.fav || "";
  $("#cnt-recent").textContent = c.recent || "";
  $("#cnt-apps").textContent = c.apps || "";
  $('[data-view="apps"]').hidden = !c.apps;
  // preferiti per console: una sotto-voce per le console che ne hanno almeno uno
  const favPlats = [...c.favPlat.entries()].sort((a, b) => collator.compare(platName(a[0]), platName(b[0])));
  $("#fav-plat-list").innerHTML = favPlats.map(([p, n]) =>
    `<a class="nav-item" data-view="favorites" data-plat="${esc(p)}" style="padding-left:28px" title="Preferiti · ${esc(platName(p))}">`
    + `<span class="ico">${platIcon(p)}</span><span class="lbl">${esc(platName(p))}</span><span class="cnt">${n}</span></a>`).join("");
  renderStatusList();
  renderSmartList();
  $("#cnt-multi").textContent = S.games.filter((g) => g.id === g.grp.primary && !g.grp.hidden && g.players >= 2 && g.store !== "online").length || "";
  $("#cnt-shop").textContent = new Set(S.shop.map((g) => g.gi)).size || "";
  $("#cnt-missing").textContent = c.missing || "";
  $("#cnt-hidden").textContent = c.hidden || "";
  $("#cnt-dups").textContent = S.games.filter((g) => g.store !== "online" && g.id !== g.grp.primary).length || "";
  const plats = [...S.plats.values()].filter((p) => (c.plat.get(p.id) || S.catalogs[p.id]?.length) && p.id !== "windows").sort((a, b) => (favPlat(b.id) - favPlat(a.id)) || collator.compare(a.name, b.name)); // prima le preferite, poi A–Z
  $("#platform-list").innerHTML = plats.map((p) =>
    `<a class="nav-item" data-view="platform" data-id="${esc(p.id)}"><span class="ico">${platIcon(p.id)}</span>`
    + `<span class="lbl">${favPlat(p.id) ? "★ " : ""}${esc(p.name)}</span><span class="cnt">${c.plat.get(p.id)?.toLocaleString("it") || ""}</span></a>`).join("");
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
    const on = a.dataset.view === S.view.type && (a.dataset.id || null) === (S.view.id || null)
      && (a.dataset.plat || null) === (S.view.plat || null);
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
  if (v.type === "favorites" && v.plat) return `★ Preferiti · ${platName(v.plat)}`;
  if (v.type === "apps") return "🧩 Applicazioni";
  return { shots: "Schermate", free: "Giochi gratis", ports: "Port PC", wishlist: "Lista desideri", stats: "Statistiche",
    top: !v.id || v.id === "all" ? "🏆 Migliori giochi" : `🏆 Top 100 · ${platName(v.id)}`,
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
  $("#group").disabled = S.view.type === "all" && S.allLocal; // la vista «Sul PC» e' gia' divisa per console
  $("#inst-filter").hidden = !(S.view.type === "store" || (S.view.type === "platform" && (S.view.id === "windows" || S.hasOnline.has(S.view.id))));
  const c = S.view.type === "collection" ? S.collBySlug.get(S.view.id) : null;
  $("#view-actions").innerHTML = c
    ? `<button class="icon-btn" id="coll-rename" title="Rinomina">✎ Rinomina</button>`
      + `<button class="icon-btn" id="coll-delete" title="${c.custom ? "Elimina" : "Nascondi"}">${c.custom ? "🗑 Elimina" : "⊘ Nascondi"}</button>`
    : S.view.type === "dups"
      ? `<button class="icon-btn" id="dup-toggle" title="Le versioni duplicate prendono i metadati dalla versione principale">`
        + `${S.skipDup ? "✓ Esclusi dallo scraping" : "✗ Inclusi nello scraping"} (clic per cambiare)</button>`
    : S.view.type === "all"
      ? `<button class="icon-btn${S.allLocal ? " on" : ""}" id="all-local" title="Solo i giochi scaricati sul PC (ROM e giochi PC installati), divisi per console">`
        + `💾 Sul PC · per console${S.allLocal ? " ✓" : ""}</button>`
    : S.view.type === "smart"
      ? `<button class="icon-btn" id="smart-edit" title="Modifica le regole">✎ Regole</button>`
    : S.view.type === "store"
      ? `<button class="icon-btn" id="plat-scan" title="Rilegge ${S.view.id === "all" ? "gli store PC" : "questo store"} e i giochi aggiunti a mano: i giochi nuovi compaiono subito">⟳ Cerca nuovi giochi</button>`
    : S.view.type === "shop"
      ? `<button class="btn smeta-btn" id="smeta-open" title="Cerca descrizioni, generi, giocatori e valutazioni dei giochi degli store SOLO nei database offline (LaunchBox, TheGamesDB): nessuna richiesta online">📚 Scraping metadati store <span class="pill">offline</span></button>`
    : S.view.type === "platform" && S.view.id !== "windows"
      ? `<label class="icon-btn" title="Nascondi le DLC di questa console: restano comunque tra i download del gioco base">`
        + `<input type="checkbox" id="hide-dlc"${S.hideDlc?.has(S.view.id) ? " checked" : ""}> Nascondi DLC</label>`
        + `<button class="icon-btn" id="plat-scan" title="Cerca nuovi giochi nella cartella di questa console: compaiono subito, i dati arrivano dopo">⟳ Cerca nuovi giochi</button>`
        + `<button class="icon-btn" id="top-open" title="I 100 migliori giochi di questa console, posseduti e scaricabili">🏆 Top 100</button>`
        + `<button class="icon-btn" id="cat-open" title="Collega pagine web con i giochi di questa console">`
        + ((S.catalogs[S.view.id] || []).map((u) => `<img class="sico" src="/icons/site?url=${encodeURIComponent(u)}" alt="">`).join("") || "🌐")
        + ` Store online${(S.catalogs[S.view.id] || []).length ? ` (${S.catalogs[S.view.id].length})` : ""}</button>` : "";
}

function setView(v) {
  S.view = v;
  store.set("view", v);
  GS.emit("viewChanged", v);
  const pv = $("#plugin-view");
  document.body.classList.toggle("foryou-mode", ["foryou", "free", "ports", "wishlist", "stats", "shots", "top"].includes(v.type));
  if (v.type !== "collection") $("#coll-banner").hidden = true;
  if (["free", "ports", "wishlist", "stats", "shots", "top"].includes(v.type)) {
    $("#grid-scroll").hidden = true;
    pv.hidden = false;
    markActive();
    updateHeader();
    $("#view-count").textContent = "";
    ({ free: renderFree, ports: renderPorts, wishlist: renderWishlist, stats: renderStats, shots: renderShots, top: renderTop })[v.type](pv);
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
  const my = ++S.openSeq; // un fetch lento non deve sovrascrivere il gioco aperto dopo
  S.selected = id;
  document.querySelectorAll(".tile.selected").forEach((t) => t.classList.remove("selected"));
  S.nodes.get(id)?.classList.add("selected");
  let d;
  try { d = await api(`/api/game/${id}`); } catch (e) { if (my === S.openSeq) { syncLibrary(); toast(e.message, true); } return; }
  if (my !== S.openSeq) return; // nel frattempo e' stato aperto (o chiuso) un altro gioco
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
  const dlSources = online ? (d.download_sources || []).filter((s) => s && s.url) : [];
  // giochi gia' scaricati che compaiono negli elenchi degli store online: link per riscaricarli
  const pages = !online ? (d.store_pages || []).filter((s) => s && s.url) : [];
  // gli aggiornamenti non sono il gioco: pulsanti a parte (⬆), non nella riga dei download
  const updates = (online ? dlSources : pages).filter((s) => s.update);
  const dlBase = dlSources.filter((s) => !s.update);
  const storePages = pages.filter((s) => !s.update);
  // una fonte sola: pulsante unico; due o piu' (es. NXBrew e Ziperto): un pulsante per sito
  const mainBtn = online
    ? (dlBase.length > 1
      ? dlBase.map((s, i) => `<button class="btn${i === 0 ? " primary" : ""}" data-open="${esc(s.url)}"
          title="Apri la pagina di download su ${esc(s.site || "questo sito")}">⬇ ${esc((s.site || "sito").replace(/^www\./, ""))}</button>`).join("")
      : `<button class="btn primary" id="d-install" title="Apre la pagina di download nel browser">⬇ Scarica (browser)</button>`)
      // solo i magnet per-file (con il nome del file dentro): gli altri scaricherebbero l'intero set
      + (d.magnet && /dn=[^&]*\./.test(d.magnet) ? `<button class="btn" id="d-torrent" title="Apre il link nel client torrent">🧲 Torrent</button>` : "")
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
      ${d.store === "ports" ? `<button class="btn" data-port="${esc(d.key.split(":").slice(1).join(":"))}">🕹 Gestisci port</button>` : ""}
      ${(d.ports || []).map((p) => `<button class="btn" data-port="${esc(p.slug)}" title="Versione nativa per PC: ${esc(p.name)}">🕹 Disponibile come port PC</button>`).join("")}
      ${actions.map((a) => `<button class="btn" data-ga="${esc(a.id)}">${esc((a.icon ? a.icon + " " : "") + a.label)}</button>`).join("")}
    </div>
    ${d.missing && d.missing.length ? `<div class="d-missing"><span>Mancano: ${esc(d.missing.join(", "))}</span>`
      + `<button class="btn" id="d-scrape" title="Riprova tutte le fonti per questo gioco (tutte le sue versioni)">⟳ Cerca dati mancanti</button></div>` : ""}
    ${online && d.magnet && !/dn=[^&]*\./.test(d.magnet) ? `<div class="d-note">Il link magnet scarica l'intero set:
      nel client torrent seleziona solo il file «${esc(d.file_name || d.title)}».</div>` : ""}
    <div class="d-emu muted">${online ? `${d.page_url ? `<img class="sico" src="/icons/site?url=${encodeURIComponent(d.page_url)}" alt="">` : "🌐"} Disponibile online${d.kind === "repack" ? " · 📦 Repack (gioco compresso: il download è corto, l'installazione lunga)" : d.kind === "direct" ? " · ⬇ Download diretto" : ""}${dlBase.length > 1 ? ` · ${dlBase.length} fonti (${dlBase.map((s) => esc(s.site)).join(", ")})` : ""} · ${esc(d.file_name || "")}`
      : pc ? `${esc(d.store_name)} · ${d.installed ? "installato" : "non installato"}`
      : d.emulator ? "Emulatore: " + esc(d.emulator) + (d.profile ? ` <span class="tag" title="Profilo di avvio di questo gioco">profilo</span>` : "")
      : `Nessun emulatore configurato per ${esc(d.platform_name)} — <a href="#" id="d-emu-cfg">impostazioni</a>`}
      ${!online ? `<a href="#" id="d-profile" title="${pc ? "Script prima e dopo il gioco" : "Emulatore, core, eseguibile, argomenti e script solo per questo gioco"}">⚙ Profilo di avvio</a>
      · <a href="#" id="d-shortcut" title="Collegamento sul desktop che avvia il gioco con Pozzo Launcher">🖥 Collegamento sul desktop</a>` : ""}</div>
    ${storePages.length ? `<div class="d-emu muted" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span>🌐 Pagine dello store:</span>
      ${storePages.map((s) => `<button class="btn" data-open="${esc(s.url)}"
        title="Apri la pagina di download su ${esc(s.site || "questo sito")}">⬇ ${esc((s.site || "sito").replace(/^www\./, ""))}</button>`).join("")}
      </div>` : ""}
    ${updates.length ? `<div class="d-emu muted" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span>⬆ Aggiornamenti installabili (${updates.length}):</span>
      ${updates.map((s) => `<button class="btn" data-open="${esc(s.url)}"
        title="${esc(s.name || "Aggiornamento")}">⬆ ${s.version ? "v" + esc(s.version) : "Update"}${s.size ? " · " + fmtSize(s.size) : ""}</button>`).join("")}
      </div>` : ""}
    ${ratingBlock(d)}
    ${online ? "" : `<div class="d-status">${Object.entries(STATUS).map(([k, s]) =>
      `<button class="btn st-btn${u.status === k ? " on st-" + k : ""}" data-status="${k}">${s.icon} ${s.label}</button>`).join("")}</div>`}
    <div class="d-mine"><span class="muted small">Il mio voto</span><span class="d-stars">${[1, 2, 3, 4, 5].map((n) =>
      `<button class="star${n <= (u.my_rating || 0) ? " on" : ""}" data-myrate="${n}" title="${n}/5">★</button>`).join("")}</span></div>
    <textarea class="field d-note" id="d-note" rows="2" placeholder="Note personali (trucchi, a che punto sei, con chi giocarlo…)">${esc(u.note || "")}</textarea>
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
    <div id="d-saves"></div>
    <div id="d-ext"></div>
    <div class="d-h">File</div>
    <div class="files">${d.files.map(esc).join("<br>")}${d.crc ? `<br>CRC ${esc(d.crc)}` : ""}
      ${Object.keys(d.sources).length ? `<br>Fonti: ${esc([...new Set(Object.values(d.sources))].join(", "))}` : ""}</div>`;
  $("#detail").classList.add("open");
  $("#detail").setAttribute("aria-hidden", "false");
  $("#detail").scrollTop = 0;
  renderDetailSections(d);
  if (d.shots) loadDetailShots(d.id);
  loadSaves(d);
  if (d.platform === "switch") loadSwitchContents(d);
  if (!pc) loadRA(d);
  GS.emit("gameOpened", d);
}

/* ---- RetroAchievements: obiettivi sbloccati (solo se l'account e' collegato e la console e' supportata) */
async function loadRA(d, force = false) {
  let r;
  try { r = await api(`/api/game/${d.id}/ra${force ? "?force=1" : ""}`); } catch { return; }
  if (S.detail !== d || !r.configured || !r.supported) return;
  let box = $("#d-ra");
  if (!box) { $("#d-ext").insertAdjacentHTML("beforebegin", `<div id="d-ra"></div>`); box = $("#d-ra"); }
  if (r.error) { box.innerHTML = `<div class="d-h">🏆 RetroAchievements</div><p class="muted small">${esc(r.error)}</p>`; return; }
  if (!r.found) { box.innerHTML = `<div class="d-h">🏆 RetroAchievements</div><p class="muted small">Gioco non trovato su RetroAchievements (o senza obiettivi).</p>`; return; }
  const g = r.game, done = g.achievements.filter((a) => a.unlocked).length, tot = g.achievements.length || g.num_achievements;
  const pct = tot ? Math.round(done / tot * 100) : 0;
  box.innerHTML = `<div class="d-h">🏆 RetroAchievements <span class="muted small">${done}/${tot} · ${pct}%${g.highest_award_kind ? ` · ${esc(g.highest_award_kind)}` : ""}</span>
      <a href="#" data-open="${esc(r.url)}" class="muted small">apri ↗</a> <a href="#" id="ra-refresh" class="muted small">↻</a></div>
    <div class="ra-bar"><span style="width:${pct}%"></span></div>
    <div class="ra-list">${g.achievements.map((a) => `<div class="ra-a${a.unlocked ? " on" : ""}" title="${esc(a.description)}${a.date_earned ? " · " + esc(a.date_earned) : ""}">
      ${a.badge_url ? `<img loading="lazy" src="${remoteImg(a.unlocked ? a.badge_url : a.badge_locked_url)}" alt="" onerror="this.remove()">` : ""}
      <span><b>${esc(a.title)}</b> <span class="muted small">${a.points} pt${a.unlocked_hardcore ? " · hardcore" : ""}</span><br><span class="muted small">${esc(a.description)}</span></span></div>`).join("")}</div>`;
  box.onclick = (e) => {
    if (e.target.id === "ra-refresh") { e.preventDefault(); loadRA(d, true); }
    const o = e.target.closest("[data-open]");
    if (o) { e.preventDefault(); api("/api/open", { body: { url: o.dataset.open } }); }
  };
}

/* ---- Switch: aggiornamento installato e DLC presenti nella cartella del gioco */
async function loadSwitchContents(d) {
  let r;
  try { r = await api(`/api/game/${d.id}/switch`); } catch { return; }
  if (S.detail !== d || (!r.update && !r.dlc)) return;
  const grid = $("#detail .d-grid");
  if (grid) grid.insertAdjacentHTML("afterbegin", `<dt>Contenuti</dt><dd>${[r.update ? `<span class="tag">Aggiornamento ${esc(r.update)}</span>` : "",
    r.dlc ? `<span class="tag">${r.dlc} DLC</span>` : ""].join(" ")}</dd>`);
}

/* ---- salvataggi del gioco: versioni prima e dopo ogni partita, rollback, percorsi manuali e rilevati */
const SV_REASON = { "prima-della-partita": "prima della partita", "dopo-partita": "dopo la partita", manuale: "manuale",
  "prima-del-ripristino": "prima del ripristino", ludusavi: "fatta da Ludusavi" };
async function loadSaves(d) {
  let r;
  try { r = await api(`/api/game/${d.id}/saves`); } catch { return; }
  const box = $("#d-saves");
  if (!box || S.detail !== d) return;
  const found = r.paths.length + r.shared.length + r.manual.length;
  const url = `/api/game/${d.id}/saves`;
  box.innerHTML = `<div class="d-h">💾 Salvataggi <span class="muted small">${r.paths.length ? `${r.paths.length} trovati con ${esc(r.emulator)}`
      : found ? "" : "nessuno trovato: aggiungi la cartella qui sotto"}</span></div>
    <div class="saves">
      <div class="sv-actions">${found ? `<button class="btn" id="sv-now">Copia adesso</button>` : ""}
        ${r.session ? `<button class="btn" id="sv-undo" title="Riporta i salvataggi a com'erano prima dell'ultima partita">↶ Annulla l'ultima partita</button>` : ""}</div>
      ${r.shared.length ? `<p class="muted small">⚠ ${r.shared.length} file condivisi con altri giochi (memory card): ripristinando torna indietro anche il loro salvataggio.</p>` : ""}
      ${r.suggested.map((p) => `<div class="sv-row sv-sug"><span>Cambiati nell'ultima partita</span><span class="small" title="${esc(p)}">${esc(p)}</span>`
        + `<button class="btn" data-sug="${esc(p)}" data-ok="1">Sono i salvataggi</button><button class="btn" data-sug="${esc(p)}">Ignora</button></div>`).join("")}
      ${r.items.map((b) => `<div class="sv-row" data-name="${esc(b.name)}"><span>${esc(fmtDate(b.time))}</span>`
        + `<span class="muted small">${esc(SV_REASON[b.reason] || b.reason)} · ${esc(fmtSize(b.size))}${b.note ? ` · <i>${esc(b.note)}</i>` : ""}</span>`
        + `<button class="btn sv-pin" title="${b.pinned ? "Fissata: non si cancella mai" : "Fissa: non verrà cancellata"}">${b.pinned ? "★" : "☆"}</button>`
        + `<button class="btn sv-note" title="Nota">✎</button><button class="btn sv-diff">Confronta</button>`
        + `<button class="btn sv-restore">Ripristina</button><button class="btn sv-del" title="Elimina">✕</button></div>`).join("")
      || `<p class="muted small">Ancora nessuna versione: se ne crea una prima e dopo ogni partita.</p>`}
      <details class="sv-paths"${r.paths.length ? "" : " open"}><summary class="small">Percorsi</summary>
        ${[...r.paths, ...r.shared].map((p) => `<div class="small muted" title="${esc(p)}">${esc(p)}</div>`).join("")}
        ${r.manual.map((p, i) => `<div class="sv-row small"><span class="muted">a mano</span><span title="${esc(p)}">${esc(p)}</span><button class="btn" data-unpath="${i}">✕</button></div>`).join("")}
        <div class="sv-row"><input id="sv-path" placeholder="Cartella o file dei salvataggi, es. %APPDATA%\Gioco\Saves"><button class="btn" id="sv-add">Aggiungi</button></div>
      </details></div>`;
  const restore = async (name, what) => {
    if (!confirm(`Ripristinare i salvataggi ${what}?\nQuelli attuali vengono prima copiati, quindi si può annullare.`
      + (r.shared.length ? "\nAttenzione: anche le memory card condivise tornano indietro." : ""))) return;
    try { const x = await api(`${url}/restore`, { body: { name } }); toast(`Salvataggi ripristinati (${x.written} file${x.removed ? `, ${x.removed} tolti` : ""})`); }
    catch (err) { return toast(err.message, true); }
    loadSaves(d);
  };
  const setPaths = async (paths) => {
    try { await api(`${url}/paths`, { body: { paths } }); } catch (err) { return toast(err.message, true); }
    loadSaves(d);
  };
  box.onclick = async (e) => {
    const t = e.target, row = t.closest(".sv-row[data-name]"), name = row?.dataset.name;
    try {
      if (t.id === "sv-now") {
        const x = await api(url, { body: {} });
        toast(x.unchanged ? "Nessuna modifica dall'ultima versione" : "Versione dei salvataggi creata");
      } else if (t.id === "sv-undo") return restore(r.session, "di prima dell'ultima partita");
      else if (t.id === "sv-add") { const v = $("#sv-path").value.trim(); if (v) return setPaths([...r.manual, v]); return; }
      else if (t.dataset.unpath) return setPaths(r.manual.filter((_, i) => i !== +t.dataset.unpath));
      else if (t.dataset.sug) await api(`${url}/suggest`, { body: { path: t.dataset.sug, accept: !!t.dataset.ok } });
      else if (!row) return;
      else if (t.classList.contains("sv-restore")) return restore(name, `del ${row.firstChild.textContent}`);
      else if (t.classList.contains("sv-pin")) await api(`${url}/meta`, { body: { name, pinned: t.textContent === "☆" } });
      else if (t.classList.contains("sv-note")) {
        const note = prompt("Nota per questa versione (es. «prima del boss»):", r.items.find((b) => b.name === name)?.note || "");
        if (note === null) return;
        await api(`${url}/meta`, { body: { name, note } });
      } else if (t.classList.contains("sv-del")) {
        if (!confirm(`Eliminare la versione del ${row.firstChild.textContent}?`)) return;
        await api(`${url}/delete`, { body: { name } });
      } else if (t.classList.contains("sv-diff")) {
        const x = await api(`${url}/diff?name=${encodeURIComponent(name)}`);
        const list = (k, label) => x[k].length ? `<div><b>${label} (${x[k].length})</b>${x[k].map((f) => `<div title="${esc(f)}">${esc(f)}</div>`).join("")}</div>` : "";
        row.nextElementSibling?.classList.contains("sv-diffbox") && row.nextElementSibling.remove();
        row.insertAdjacentHTML("afterend", `<div class="sv-diffbox small muted">${list("changed", "Diversi da adesso") + list("missing", "Non più sul disco")
          + list("extra", "Nuovi, verrebbero tolti") || `Identica ai salvataggi attuali (${x.same} file).`}</div>`);
        return;
      } else return;
    } catch (err) { return toast(err.message, true); }
    loadSaves(d);
  };
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
  S.openSeq++; // una risposta in arrivo non deve riaprire il pannello
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
  if (S.detail?.id === d.id) openDetail(d.id); // solo se il pannello mostra ancora questo gioco
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
    if (S.detail?.id === gameId) openDetail(gameId);
  };
}

/* ------------------------------------------------------------------ avvio giochi */
async function play(id) {
  try { await api(`/api/game/${id}/play`, { body: {} }); } catch (e) { return toast(e.message, true); }
  pollRunning();
}

/* ------------------------------------------------------------------ menu del tasto destro su un gioco */
function closeCtx() { document.getElementById("ctxmenu")?.remove(); }

async function setGrp(g, field, value) {
  await api(`/api/game/${g.id}/flag`, { body: { field, value } });
  if (field === "last_played") g.grp.last = 0;
  else g.grp[field] = field === "status" ? value || "" : value ? 1 : 0;
  renderSidebar();
  refreshGrid(true);
  if (S.detail?.id === g.id) openDetail(g.id);
}

/** Le categorie da cui il gioco si puo' togliere: preferiti, giocati di recente, stato e collezioni. */
function ctxRemovals(g) {
  const grp = g.grp, out = [];
  if (grp.favorite) out.push({ l: "Preferiti", view: "favorites", f: () => setGrp(g, "favorite", false) });
  if (grp.last > 0) out.push({ l: "Giocati di recente", view: "recent", f: () => setGrp(g, "last_played", null) });
  if (grp.status) out.push({ l: STATUS[grp.status]?.label || grp.status, view: "status", id: grp.status, f: () => setGrp(g, "status", "") });
  for (const i of grp.colls) {
    const c = S.colls[i];
    if (!c || c.hidden) continue;
    out.push({ l: c.name, view: "collection", id: c.slug, f: async () => {
      await api(`/api/collections/${encodeURIComponent(c.slug)}/members`, { body: { game: g.id, action: "remove" } });
      await reload(true);
    } });
  }
  return out;
}

function openCtx(e, g) {
  closeCtx();
  const grp = g.grp, rem = ctxRemovals(g);
  const here = rem.find((r) => r.view === S.view.type && (r.id ?? null) === (S.view.id ?? null));
  const pt = { getBoundingClientRect: () => ({ left: e.clientX, bottom: e.clientY }) };
  const rows = [];
  if (g.store !== "online") rows.push({ l: "▶ Gioca", f: () => play(g.id) });
  rows.push({ l: "Dettagli", f: () => openDetail(g.id) });
  if (!g.store) rows.push({ l: "📂 Mostra nella cartella", f: () => api(`/api/game/${g.id}/open-folder`, { body: {} }) });
  rows.push({ l: "🖼 Cerca immagini…", f: () => openMediaPick(g) });
  if (g.store !== "online") rows.push({ l: "🔗 Applicazioni abbinate…", f: () => openProfile(g) });
  if (here) rows.push("-", { l: `✖ Rimuovi da «${here.l}»`, f: here.f, hl: 1 });
  rows.push("-", { l: grp.favorite ? "★ Togli dai preferiti" : "☆ Aggiungi ai preferiti", f: () => setGrp(g, "favorite", !grp.favorite) });
  rows.push({ l: "＋ Aggiungi a una collezione…", f: () => pickCollection(pt, g.id, grp.colls.map((i) => S.colls[i]?.slug)) });
  rows.push({ h: "Stato" });
  for (const [k, s] of Object.entries(STATUS)) if (grp.status !== k) rows.push({ l: `${s.icon} ${s.label}`, f: () => setGrp(g, "status", k) });
  const others = rem.filter((r) => r !== here);
  if (others.length) rows.push({ h: "Rimuovi da" }, ...others.map((r) => ({ l: `✖ ${r.l}`, f: r.f })));
  rows.push("-", { l: grp.hidden ? "Mostra di nuovo" : "Nascondi dalla libreria", f: () => setGrp(g, "hidden", !grp.hidden) });
  if (g.store === "manual") rows.push({ l: "🗑 Rimuovi dalla libreria", f: () => removeManual(g) });

  const m = document.createElement("div");
  m.id = "ctxmenu";
  m.innerHTML = rows.map((r, i) => r === "-" ? "<hr>" : r.h ? `<div class="ch">${esc(r.h)}</div>`
    : `<div class="ci${r.hl ? " hl" : ""}" data-i="${i}">${esc(r.l)}</div>`).join("");
  document.body.appendChild(m);
  m.style.left = Math.max(6, Math.min(e.clientX, innerWidth - m.offsetWidth - 8)) + "px";
  m.style.top = Math.max(6, Math.min(e.clientY, innerHeight - m.offsetHeight - 8)) + "px";
  m.onclick = async (ev) => {
    const it = ev.target.closest(".ci");
    if (!it) return;
    closeCtx();
    try { await rows[+it.dataset.i].f(); } catch (err) { toast(err.message, true); }
  };
}

/** Toglie un gioco aggiunto a mano dalla libreria: i file sul disco non vengono toccati. */
async function removeManual(g) {
  if (!confirm(`Rimuovere «${g.title}» dalla libreria? Il gioco sul disco non viene toccato.`)) return;
  await api(`/api/manual/${encodeURIComponent(g.key)}`, { method: "DELETE" });
  if (S.detail?.id === g.id) closeDetail();
  await reload(true);
  toast("Rimosso dalla libreria");
}

/* ---- immagine scelta a mano: ricerca su Google Immagini (titolo gia' scritto) e indirizzo incollato */
let MD = null;
function googleImages() {
  const q = $("#md-q").value.trim() || MD?.title || "";
  $("#md-google").href = `https://www.google.com/search?tbm=isch&q=${encodeURIComponent(q)}`;
}

function openMediaPick(g) {
  MD = g;
  $("#md-game").textContent = `— ${g.title}`;
  $("#md-q").value = g.title;
  $("#md-url").value = "";
  googleImages();
  $("#media-dlg").showModal();
}

async function useMedia(url) {
  const g = MD;
  if (!g || !/^https?:\/\//i.test(url || "")) return toast("Serve un indirizzo di immagine (http o https)", true);
  try { await api(`/api/game/${g.id}/media`, { body: { kind: $("#md-kind").value, url } }); }
  catch (e) { return toast(e.message, true); }
  $("#media-dlg").close();
  toast("Immagine salvata");
  await reload(true);
  if (S.detail?.id === g.id) openDetail(g.id);
}

/* ---- incolla una cartella per una console (o per i giochi PC) e cerca solo i giochi nuovi */
async function pasteFolder(platform) {
  let path = "";
  try { path = (await navigator.clipboard.readText() || "").trim().replace(/^"|"$/g, ""); }
  catch { /* permesso negato o pagina non in HTTPS: si chiede a mano */ }
  if (!path) path = prompt(`Percorso della cartella (${platform === "windows" ? "giochi PC" : platName(platform)})`) || "";
  path = path.trim().replace(/^"|"$/g, "");
  if (!path) return;
  let r;
  try { r = await api("/api/folders/add", { body: { platform, path } }); }
  catch (e) { return toast(e.message, true); }
  // giochi PC: {added}; console: lo stato dello scraping appena avviato (i giochi nuovi si vedono nella barra)
  if (r.added) toast(`Cartella aggiunta: ${r.added} ${r.added === 1 ? "gioco PC" : "giochi PC"}`);
  else if (r.status === "running" || (r.keys || []).length) { toast("Cartella aggiunta: cerco i giochi nuovi…"); SET.lastStatus = "running"; pollScan(); }
  else toast("Cartella aggiunta");
  await reload(true);
  loadScan();
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
      + (r.status === "running" ? `<button class="btn" id="run-stop" title="Chiude il gioco in esecuzione">■ Chiudi gioco</button><button class="btn" id="run-shot" title="Schermata del gioco (${esc(SHOTS.hotkey)} · controller: Select+R1)">📷${r.shots ? " " + r.shots : ""}</button>` : "");
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

function openAddGame(kind = "game") {
  const dlg = $("#add-game");
  $("#ag-exe").value = ""; $("#ag-title").value = ""; $("#ag-title").dataset.auto = ""; $("#ag-args").value = "";
  $("#ag-kind").value = kind;
  dlg.showModal();
  $("#ag-exe").focus();
}

/** Titolo = nome del file .exe (senza estensione), a meno che l'utente ne abbia scritto uno suo. */
function agTitleFromExe() {
  const t = $("#ag-title");
  if (t.value && t.dataset.auto !== t.value) return;
  const name = $("#ag-exe").value.trim().replace(/^"|"$/g, "").split(/[\\/]/).pop().replace(/\.[^.]+$/, "");
  t.value = name;
  t.dataset.auto = name;
}

async function saveAddGame() {
  const exe = $("#ag-exe").value.trim();
  const kind = $("#ag-kind").value === "app" ? "app" : "game";
  if (!exe) return toast(kind === "app" ? "Scegli il file .exe dell'applicazione" : "Scegli il file .exe del gioco", true);
  const btn = $("#ag-save");
  btn.disabled = true;
  btn.textContent = "Aggiungo…";
  try {
    await api("/api/manual", { body: { exe, title: $("#ag-title").value.trim(), args: $("#ag-args").value, kind } });
  } catch (e) { return toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = "Aggiungi"; }
  $("#add-game").close();
  await reload(true);  // il gioco e' gia' in libreria: copertina e metadati arrivano dopo, solo per lui
  toast(kind === "app" ? "Applicazione aggiunta" : "Gioco aggiunto: cerco copertina e dati…");
  pollManual();
}

/** Finche' il server cerca copertina e metadati dei giochi appena aggiunti, ricarica la libreria al termine. */
let manualTimer = 0;
async function pollManual() {
  clearTimeout(manualTimer);
  let r;
  try { r = await api("/api/manual/state"); } catch { return; }
  if (r.busy) { manualTimer = setTimeout(pollManual, 1000); return; }
  await reload(true);
  if (S.detail) openDetail(S.detail.id);
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
  if (d.first) d.items.forEach((i) => { i.new = false; }); // prima apertura: non sono tutti «nuovi»
  freeBadge(d);
  // prima quelli ancora da prendere, poi chi scade prima; senza scadenza in fondo
  const endOf = (i) => (i.end ? Date.parse(i.end.replace(" ", "T")) || Infinity : Infinity);
  d.items.sort((a, b) => (a.claimed || a.owned) - (b.claimed || b.owned) || endOf(a) - endOf(b));
  api("/api/free/mark", { body: { seen: true } }).catch(() => {}); // da ora non sono piu' nuovi (l'etichetta resta in questa pagina)
  const draw = () => {
    const items = d.items.filter((i) => (!FREE_F.console || i.console === FREE_F.console) && (!FREE_F.kind || i.kind === FREE_F.kind));
    const kinds = [...new Set(d.items.map((i) => i.kind))];
    const cons = [...new Set(d.items.map((i) => i.console))];
    const chip = (k, v, l) => `<button class="pchip${FREE_F[k] === v ? " on" : ""}" data-f="${k}" data-v="${esc(v)}">${esc(l)}</button>`;
    pv.innerHTML = `<div class="page">
      <div class="page-head"><div><h2>🎁 Giochi gratis adesso</h2>
        <p class="muted small">Da tenere, inclusi negli abbonamenti, weekend gratuiti e DLC gratis.
          Fonti: <a href="#" data-open="https://www.allkeyshop.com/blog/daily-game-deals/">allkeyshop.com</a> ·
          <a href="#" data-open="https://www.gamerpower.com/">GamerPower</a> ·
          <a href="#" data-open="https://store.steampowered.com/search/?specials=1&maxprice=free">Steam</a> ·
          <a href="#" data-open="https://github.com/eikowagenknecht/lootscraper">LootScraper</a> (Prime)${d.time ? ` · aggiornato ${new Date(d.time * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" })}` : ""}</p>
        ${d.errors.length ? `<p class="warn small">⚠ ${d.errors.map((e) => esc(e.split(":")[0])).join(", ")} non ha risposto: ${d.stale ? "elenco dell'ultima volta" : "elenco parziale"}.</p>` : ""}</div>
        <button class="btn" id="free-refresh">↻ Aggiorna</button></div>
      <div class="pchips">${chip("console", "", "Tutte")}${cons.map((c) => chip("console", c, ({ pc: "🖥 PC", playstation: "PlayStation", xbox: "Xbox", switch: "Switch" })[c] || c)).join("")}
        <span class="sep"></span>${chip("kind", "", "Ogni offerta")}${kinds.map((k) => chip("kind", k, FREE_KIND[k] || k)).join("")}</div>
      <div class="free-grid">${items.map((i, n) => `<div class="free-card${i.owned || i.claimed ? " owned" : ""}" data-n="${d.items.indexOf(i)}">
        <div class="fc-cover">${i.cover ? `<img loading="lazy" src="${remoteImg(i.cover)}" alt="" onerror="this.remove()">` : ""}
          <span class="fc-kind k-${fold(i.kind).replace(/[^a-z]+/g, "")}">${esc(FREE_KIND[i.kind] || i.kind)}</span>
          ${i.drm && i.drm !== "none" ? `<img class="fc-drm" src="/icons/store/${esc(({ amazon: "amazon", epic: "epic", steam: "steam", gog: "gog", battlenet: "battlenet" })[i.drm] || "manual")}" alt="" onerror="this.remove()">` : ""}
          ${i.owned ? `<span class="fc-owned">✓ Già tuo</span>` : i.claimed ? `<span class="fc-owned">✓ Riscattato</span>` : i.new ? `<span class="fc-new">Nuovo</span>` : ""}
          ${i.wished && !i.owned ? `<span class="fc-wish" title="Nella tua lista desideri">♡</span>` : ""}</div>
        <div class="fc-info"><b title="${esc(i.full_title || i.title)}">${esc(i.title)}</b>
          <span class="muted small">${esc([({ pc: "PC", playstation: "PlayStation", xbox: "Xbox" })[i.console] || i.console,
            i.end ? "fino al " + new Date(i.end.replace(" ", "T")).toLocaleDateString("it") : "", i.worth ? "valore " + i.worth : ""].filter(Boolean).join(" · "))}</span>
          <span class="fc-btns"><button class="btn primary fc-open" data-n="${d.items.indexOf(i)}">Apri ↗</button>
          ${i.owned ? "" : `<button class="btn fc-claim" data-n="${d.items.indexOf(i)}" title="Segna come preso (o togli il segno)">${i.claimed ? "↺" : "✓ Preso"}</button>`}</span></div></div>`).join("")
        || `<p class="muted">Nessun gioco gratis con questi filtri.</p>`}</div></div>`;
  };
  draw();
  pv.onclick = (e) => {
    const f = e.target.closest("[data-f]");
    if (f) { FREE_F[f.dataset.f] = f.dataset.v; if (f.dataset.f === "console") store.set("freeConsole", f.dataset.v); draw(); return; }
    if (e.target.id === "free-refresh") { renderFree(pv, true); return; }
    const o = e.target.closest("[data-open]");
    if (o) { e.preventDefault(); api("/api/open", { body: { url: o.dataset.open } }); return; }
    const cl = e.target.closest(".fc-claim");
    if (cl) {
      const it = d.items[+cl.dataset.n];
      api("/api/free/mark", { body: { claim: it.id, value: !it.claimed } }).then(() => { it.claimed = !it.claimed; it.new = false; freeBadge(d); draw(); })
        .catch((err) => toast(err.message, true));
      return;
    }
    const c = e.target.closest(".fc-open, .free-card");
    if (c) api("/api/open", { body: { url: d.items[+c.dataset.n].url } }).then(() => toast("Apro la pagina nel browser"));
  };
}
/** Numero nella barra laterale: le offerte nuove (🆕), altrimenti quante sono ancora da prendere. */
function freeBadge(d) {
  const todo = d.items.filter((i) => !i.upcoming && !i.owned && !i.claimed).length;
  $("#cnt-free").textContent = d.new ? `🆕 ${d.new}` : todo || "";
}
const FREE_KIND = { "Free to keep": "Da tenere", "Free with Prime": "Con Prime", "Free with PS+": "Con PS Plus", Gamepass: "Game Pass",
  "Free DLC": "DLC gratis", "Free weekend": "Weekend gratis", "Free to play": "Free to play", "In arrivo": "In arrivo" };

/* ---- pagina: port PC (ricompilazioni e decomp dal catalogo di Quiver Launcher) */
const PORTS_F = { console: store.get("portsConsole", ""), mine: "", q: "", open: null };
/** Il catalogo si apre subito dalla copia salvata e si aggiorna in background: a fine aggiornamento la pagina si ridisegna. */
async function followPortsRefresh(pv) {
  if (PORTS_F.following) return;
  PORTS_F.following = true;
  try {
    let r;
    do { await new Promise((ok) => setTimeout(ok, 1500)); r = await api("/api/ports/refreshing"); } while (r.refreshing);
  } catch { return; } finally { PORTS_F.following = false; }
  if (S.view.type === "ports" && pv.isConnected && !PORTS_F.open) { S.ports = null; renderPorts(pv); }
}

async function renderPorts(pv, force = false) {
  if (PORTS_F.open) return renderPort(pv, PORTS_F.open);
  pv.innerHTML = `<div class="page"><p class="muted">Leggo il catalogo dei port…</p></div>`;
  let d;
  try { d = await api(`/api/ports${force ? "?force=1" : ""}`); } catch (e) { pv.innerHTML = `<div class="page"><p>${esc(e.message)}</p></div>`; return; }
  S.ports = d;
  portsBadge(d);
  if (d.refreshing) followPortsRefresh(pv);
  const rank = (i) => (i.installed?.update ? 0 : i.installed ? 1 : i.roms.length ? 2 : 3);
  d.items.sort((a, b) => rank(a) - rank(b) || b.recommended - a.recommended || collator.compare(a.name, b.name));
  const cons = Object.entries(d.consoles).map(([id, name]) => [id, name, d.items.filter((i) => i.consoles.includes(id)).length])
    .sort((a, b) => b[2] - a[2]);
  const chip = (k, v, l) => `<button class="pchip${PORTS_F[k] === v ? " on" : ""}" data-f="${k}" data-v="${esc(v)}">${esc(l)}</button>`;
  pv.innerHTML = `<div class="page">
    <div class="page-head"><div><h2>🕹 Port PC</h2>
      <p class="muted small">Giochi console ricompilati o ricostruiti per girare su Windows senza emulatore. Quasi tutti chiedono
        la tua ROM originale: quelli segnati «🎮 ROM» li hai già nella libreria. Catalogo:
        <a href="#" data-open="https://github.com/tgeorgiadis/quiver-launcher">Quiver Launcher</a>${d.time ? ` · aggiornato ${new Date(d.time * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" })}` : ""}</p>
      ${d.error ? `<p class="warn small">⚠ Il catalogo non ha risposto (${esc(d.error)}): ${d.stale ? "elenco dell'ultima volta" : "nessun elenco salvato"}.</p>` : ""}</div>
      <span class="fc-btns pt-tools"><button class="btn" id="pt-add">＋ Aggiungi a mano</button><button class="btn" id="pt-set">⚙</button>
        <button class="btn" id="pt-refresh">↻ Aggiorna</button></span></div>
    <div id="pt-form"></div>
    <div class="pchips"><input id="pt-q" class="field" placeholder="Cerca…" value="${esc(PORTS_F.q)}" autocomplete="off">
      ${chip("mine", "", "Tutti")}${chip("mine", "rom", "🎮 Ho la ROM")}${chip("mine", "inst", "✓ Installati")}${chip("mine", "upd", "⬆ Da aggiornare")}
      <span class="sep"></span>${chip("console", "", "Ogni console")}${cons.map(([id, name, n]) => chip("console", id, `${name} (${n})`)).join("")}</div>
    <div class="free-grid" id="pt-grid"></div></div>`;
  const draw = () => {
    const q = fold(PORTS_F.q);
    const items = d.items.filter((i) => (!PORTS_F.console || i.consoles.includes(PORTS_F.console))
      && (!PORTS_F.mine || (PORTS_F.mine === "rom" ? i.roms.length : PORTS_F.mine === "inst" ? i.installed : i.installed?.update))
      && (!q || fold(`${i.name} ${i.project} ${i.developer} ${i.tags.join(" ")}`).includes(q)));
    $("#pt-grid", pv).innerHTML = items.map((i) => `<div class="free-card" data-slug="${esc(i.slug)}">
      <div class="fc-cover">${i.cover ? `<img loading="lazy" src="${remoteImg(gridThumb(i.cover))}" data-full="${esc(remoteImg(i.cover))}" alt=""
          onerror="if (this.dataset.full && this.src.indexOf('thumb') > 0) { this.src = this.dataset.full; this.dataset.full = '' } else this.remove()">` : ""}
        <span class="fc-kind">${esc(i.consoles.map((c) => d.consoles[c] || c).join(", "))}</span>
        ${i.installed?.update ? `<span class="fc-new">⬆ ${esc(i.installed.latest)}</span>` : i.installed ? `<span class="fc-owned">✓ Installato</span>` : ""}
        ${i.roms.length ? `<span class="fc-wish pt-rom" title="Hai la ROM: ${esc(i.roms.map((r) => r.title).join(", "))}">🎮 ROM</span>` : ""}</div>
      <div class="fc-info"><b title="${esc(i.name)}">${esc(i.name)}</b>
        <span class="muted small">${esc([i.project !== i.name ? i.project : "", i.developer, i.installed?.version || i.version].filter(Boolean).join(" · "))}</span></div></div>`).join("")
      || `<p class="muted">Nessun port con questi filtri.</p>`;
  };
  draw();
  $("#pt-q", pv).oninput = (e) => { PORTS_F.q = e.target.value; draw(); };
  pv.onclick = async (e) => {
    const f = e.target.closest("[data-f]");
    if (f) {
      PORTS_F[f.dataset.f] = f.dataset.v;
      if (f.dataset.f === "console") store.set("portsConsole", f.dataset.v);
      pv.querySelectorAll(`[data-f="${f.dataset.f}"]`).forEach((b) => b.classList.toggle("on", b === f));
      return draw();
    }
    const o = e.target.closest("[data-open]");
    if (o) { e.preventDefault(); api("/api/open", { body: { url: o.dataset.open } }); return; }
    if (e.target.id === "pt-refresh") return renderPorts(pv, true);
    if (e.target.id === "pt-set") {
      $("#pt-form", pv).innerHTML = `<div class="d-note"><p class="small">Cartella dei port (vuota = predefinita):</p>
        <input class="field" id="pt-dir" value="${esc(d.dir)}" style="width:100%">
        <p class="small">Token GitHub (facoltativo: senza, GitHub permette 60 richieste all'ora) ${d.token ? "· <b>impostato</b>" : ""}</p>
        <input class="field" id="pt-tok" type="password" placeholder="${d.token ? "lascia vuoto per non cambiarlo" : "ghp_…"}" style="width:100%">
        <p><button class="btn primary" id="pt-set-save">Salva</button></p></div>`;
      return;
    }
    if (e.target.id === "pt-set-save") {
      const body = { dir: $("#pt-dir", pv).value };
      if ($("#pt-tok", pv).value) body.token = $("#pt-tok", pv).value;
      try { await api("/api/ports-settings", { body }); } catch (err) { return toast(err.message, true); }
      toast("Impostazioni dei port salvate");
      return renderPorts(pv);
    }
    if (e.target.id === "pt-add") {
      $("#pt-form", pv).innerHTML = `<div class="d-note"><p class="small">Un port che non è nel catalogo: repository GitHub
        (<code>utente/progetto</code>, si installa l'ultima release) <b>oppure</b> una cartella già scaricata con il suo .exe.</p>
        <input class="field" id="pt-repo" placeholder="utente/progetto" style="width:100%">
        <input class="field" id="pt-folder" placeholder="oppure: C:\\Giochi\\MioPort" style="width:100%">
        <input class="field" id="pt-name" placeholder="Nome (facoltativo)">
        <select class="field" id="pt-con"><option value="">Console originale…</option>${cons.map(([id, name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join("")}</select>
        <p><button class="btn primary" id="pt-add-go">Aggiungi</button></p></div>`;
      return;
    }
    if (e.target.id === "pt-add-go") {
      const body = { repo: $("#pt-repo", pv).value, folder: $("#pt-folder", pv).value, name: $("#pt-name", pv).value, console: $("#pt-con", pv).value };
      let r;
      try { r = await api("/api/ports/manual", { body }); } catch (err) { return toast(err.message, true); }
      if (r.status === "running") { PORTS_F.open = null; return followPortJob(pv); }
      toast(`${r.name} aggiunto`);
      pollStores(true);
      return renderPorts(pv);
    }
    const c = e.target.closest(".free-card");
    if (c) { PORTS_F.open = c.dataset.slug; renderPort(pv, c.dataset.slug); }
  };
}

/** Copertine di SteamGridDB: la miniatura (~50 KB) invece del PNG originale (~700 KB). */
const gridThumb = (u) => u.replace(/^(https:\/\/cdn2\.steamgriddb\.com)\/grid\/(\w+)\.\w+$/, "$1/thumb/$2.jpg");

function portsBadge(d) {
  const n = d.items.filter((i) => i.installed?.update).length;
  $("#cnt-ports").textContent = n ? `⬆ ${n}` : "";
}

async function renderPort(pv, slug) {
  if (!S.ports) { try { S.ports = await api("/api/ports"); } catch (e) { return toast(e.message, true); } }
  const i = S.ports.items.find((x) => x.slug === slug);
  if (!i) { PORTS_F.open = null; return renderPorts(pv); }
  const inst = i.installed;
  let m = null;
  if (inst) { try { m = await api(`/api/ports/${encodeURIComponent(slug)}`); } catch { /* cartella sparita: si vede come non installato */ } }
  const cname = (c) => S.ports.consoles[c] || c;
  const romFile = (r) => r.file.split(/[\\/]/).pop();
  pv.innerHTML = `<div class="page port-page">
    <p><button class="btn" id="pt-back">← Tutti i port</button></p>
    ${i.hero ? `<div class="pt-hero"><img src="${remoteImg(i.hero)}" alt="" onerror="this.parentNode.remove()"></div>` : ""}
    <h2>${esc(i.name)}</h2>
    <p class="muted">${esc([i.project !== i.name ? i.project : "", i.consoles.map(cname).join(", "), i.developer, inst ? `installato ${inst.version}` : i.version ? `ultima versione ${i.version}` : ""].filter(Boolean).join(" · "))}</p>
    ${i.broken ? `<p class="warn small">⚠ ${i.broken} giocator${i.broken === 1 ? "e lo segnala" : "i lo segnalano"} come non funzionante.</p>` : ""}
    <p>${esc(i.description)}</p>
    <div class="d-actions">
      ${!inst ? `<button class="btn primary" id="pt-install">⬇ Installa</button>`
        : `${inst.game_id ? `<button class="btn primary" id="pt-play">▶ Gioca</button><button class="btn" id="pt-lib">Scheda nella libreria</button>
             <button class="btn" id="pt-folder">📁 Cartella</button>` : ""}
           ${inst.update ? `<button class="btn primary" id="pt-update">⬆ Aggiorna a ${esc(inst.latest)}</button>
             <button class="btn" id="pt-skip" title="Non segnalare più questa versione">Salta questa versione</button>` : ""}
           ${inst.repository ? `<button class="btn" data-open="https://github.com/${esc(inst.repository)}">Progetto ↗</button>` : ""}
           <button class="btn" id="pt-remove">🗑 Disinstalla</button>`}
    </div>
    <div id="pt-job"></div>
    ${m && m.candidates.length > 1 ? `<div class="d-h">Eseguibile da avviare</div>
      <select class="field" id="pt-exe">${m.candidates.map((x) => `<option${x === m.exe ? " selected" : ""}>${esc(x)}</option>`).join("")}</select>` : ""}
    <div class="d-h">ROM originale</div>
    ${i.roms.length ? i.roms.map((r) => `<div class="sv-row"><span>🎮 ${esc(r.title)}</span><span class="muted small" title="${esc(r.file)}">${esc(romFile(r))}</span>
        <button class="btn" data-game="${r.id}">Apri</button></div>`).join("")
      : `<p class="muted small">Non hai la ROM di questo gioco nella libreria. Il port ha bisogno della tua copia originale.</p>`}
    ${m && m.setup.length ? m.setup.map((s) => `<div class="sv-row"><span>${esc(s.label)} <span class="muted small">→ ${esc(s.destination)}${s.accepted.filter(Boolean).length ? ` (${esc(s.accepted.filter(Boolean).join(", "))})` : ""}</span></span>
        ${s.present ? `<span class="muted small">✓ presente</span>` : ""}
        ${i.roms.length ? `<button class="btn" data-setup="${esc(s.id)}">${s.present ? "Ricopia" : "Copia dalla libreria"}</button>` : ""}</div>`).join("")
      : inst && i.roms.length ? `<p class="muted small">Il port chiede la ROM al primo avvio: scegli il file «${esc(romFile(i.roms[0]))}»
        (<a href="#" data-game-folder="${i.roms[0].id}">mostra nella cartella</a>).</p>` : ""}
    ${m ? `<p class="muted small">${m.user_files} file tuoi (salvataggi, configurazione, mod) nella cartella: restano negli aggiornamenti
      e si copiano con i salvataggi dalla scheda del gioco.</p>` : ""}
    ${m && m.mods ? `<div class="d-h">Mod</div><div id="pt-mods"><button class="btn" id="pt-mods-load">Mostra le mod</button></div>` : ""}
  </div>`;
  pv.onclick = async (e) => {
    const t = e.target, enc = encodeURIComponent(slug);
    const o = t.closest("[data-open]");
    if (o) { e.preventDefault(); api("/api/open", { body: { url: o.dataset.open } }); return; }
    if (t.id === "pt-back") { PORTS_F.open = null; return renderPorts(pv); }
    if (t.id === "pt-install" || t.id === "pt-update") {
      try { await api(t.id === "pt-install" ? "/api/ports/install" : `/api/ports/${enc}/update`, { body: { slug } }); } catch (err) { return toast(err.message, true); }
      return followPortJob(pv);
    }
    if (t.id === "pt-play") return play(inst.game_id);
    if (t.id === "pt-lib") return openDetail(inst.game_id);
    if (t.id === "pt-folder") return api(`/api/game/${inst.game_id}/open-folder`, { body: {} }).catch((err) => toast(err.message, true));
    if (t.id === "pt-skip") {
      await api(`/api/ports/${enc}/skip`, { body: { version: inst.latest } }).catch((err) => toast(err.message, true));
      S.ports = null;
      return renderPort(pv, slug);
    }
    if (t.id === "pt-remove") {
      if (!confirm(`Disinstallare «${i.name}»? La cartella va nel Cestino (salvataggi compresi: copiali prima se ti servono).`)) return;
      try { await api(`/api/ports/${enc}/uninstall`, { body: {} }); } catch (err) { return toast(err.message, true); }
      toast(`${i.name} disinstallato`);
      pollStores(true);
      S.ports = null;
      return renderPort(pv, slug);
    }
    if (t.dataset.game) return openDetail(+t.dataset.game);
    if (t.dataset.gameFolder) { e.preventDefault(); return api(`/api/game/${t.dataset.gameFolder}/open-folder`, { body: {} }).catch((err) => toast(err.message, true)); }
    if (t.dataset.setup) {
      t.disabled = true;
      try { const r = await api(`/api/ports/${enc}/rom`, { body: { id: t.dataset.setup } }); toast(`ROM copiata: ${r.path}`); }
      catch (err) { t.disabled = false; return toast(err.message, true); }
      return renderPort(pv, slug);
    }
    if (t.id === "pt-mods-load" || t.dataset.mod || t.dataset.modDel) return portMods(pv, slug, t);
  };
  pv.onchange = async (e) => {
    if (e.target.id !== "pt-exe") return;
    try { await api(`/api/ports/${encodeURIComponent(slug)}/exe`, { body: { exe: e.target.value } }); } catch (err) { return toast(err.message, true); }
    toast("Eseguibile salvato");
    pollStores(true);
  };
  const j = await api("/api/ports/job").catch(() => null);
  if (j && j.status === "running" && j.slug === slug) followPortJob(pv);
}

async function portMods(pv, slug, t) {
  const enc = encodeURIComponent(slug), box = $("#pt-mods", pv);
  try {
    if (t.dataset.mod) { t.disabled = true; await api(`/api/ports/${enc}/mods`, { body: { id: t.dataset.mod } }); toast("Mod installata"); }
    if (t.dataset.modDel) { await api(`/api/ports/${enc}/mods/${encodeURIComponent(t.dataset.modDel)}`, { method: "DELETE" }); toast("Mod rimossa"); }
  } catch (err) { t.disabled = false; return toast(err.message, true); }
  box.innerHTML = `<p class="muted">Carico le mod…</p>`;
  let r;
  try { r = await api(`/api/ports/${enc}/mods`); } catch (err) { box.innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
  box.innerHTML = `${r.links.map((l) => `<a href="#" class="small" data-open="${esc(l.url)}">${esc(l.provider)} ↗</a>`).join(" · ")}
    ${r.items.filter((x) => !x.nsfw).slice(0, 60).map((x) => `<div class="sv-row"><span><b>${esc(x.name)}</b> <span class="muted small">${esc(x.owner)} · ${esc(x.version)}</span><br>
      <span class="muted small">${esc(x.description)}</span></span>
      ${r.installed[x.id] ? `<button class="btn" data-mod-del="${esc(x.id)}">Rimuovi</button>` : `<button class="btn" data-mod="${esc(x.id)}">Installa</button>`}</div>`).join("")
      || `<p class="muted small">Nessuna mod scaricabile da qui: usa i link sopra.</p>`}`;
}

/** Avanzamento di installazione/aggiornamento; alla fine la libreria si aggiorna con il port. */
async function followPortJob(pv) {
  let j;
  do {
    try { j = await api("/api/ports/job"); } catch { return; }
    const box = $("#pt-job", pv);
    if (box) {
      const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
      box.innerHTML = `<p class="small">${j.status === "running" ? `⏳ ${j.total ? `${pct}% · ${fmtSize(j.done)} di ${fmtSize(j.total)}` : j.done ? fmtSize(j.done) : ""}` : ""}</p>
        <pre class="log">${esc(j.log.slice(-8).join("\n"))}</pre>`;
    }
    if (j.status === "running") await new Promise((r) => setTimeout(r, 1000));
  } while (j.status === "running");
  if (j.status === "error") return toast(j.log[j.log.length - 1] || "Installazione non riuscita", true);
  toast("Port installato: lo aggiungo alla libreria");
  pollStores(true);
  S.ports = null;
  if (PORTS_F.open && S.view.type === "ports") renderPort(pv, PORTS_F.open);
  else if (S.view.type === "ports") renderPorts(pv);
}

/** Prezzo dei negozi di key (GG.deals), con lo sconto rispetto al prezzo pieno (es. 60 € su Steam -> 9,50 € = -84%). */
function keyPrice(w, money, full) {
  const cut = full ? Math.round((1 - w.key_price / full) * 100) : 0;
  return `<span class="wl-key" title="Negozi di key${w.key_low != null ? ` · minimo storico ${money(w.key_low, w.currency)}` : ""}">🔑 `
    + `${cut > 0 ? `<span class="cut">-${cut}%</span>` : ""}<b>${money(w.key_price, w.currency)}</b></span>`;
}

/** Grafichino dello storico (un punto al giorno: il prezzo piu' basso tra store e negozi di key). */
function priceSpark(hist, money, cur) {
  const pts = (hist || []).map(([d, p, k]) => [d, Math.min(...[p, k].filter((v) => v != null))]).filter(([, v]) => isFinite(v));
  if (pts.length < 2) return "";
  const vs = pts.map(([, v]) => v), lo = Math.min(...vs), hi = Math.max(...vs), W = 90, H = 22;
  const xy = pts.map(([, v], i) => `${(i / (pts.length - 1) * W).toFixed(1)},${(hi === lo ? H / 2 : H - 2 - (v - lo) / (hi - lo) * (H - 4)).toFixed(1)}`);
  return `<span class="wl-spark" title="Da ${pts[0][0]}: minimo ${money(lo, cur)}, massimo ${money(hi, cur)}">`
    + `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><polyline points="${xy.join(" ")}" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`
    + ` min ${money(lo, cur)}</span>`;
}

/* ---- pagina: lista dei desideri */
async function renderWishlist(pv) {
  let d;
  try { d = await api("/api/wishlist"); } catch (e) { pv.innerHTML = `<div class="page"><p>${esc(e.message)}</p></div>`; return; }
  const money = (v, cur = "EUR") => (v == null ? "—" : v === 0 ? "Gratis" : v.toLocaleString("it", { style: "currency", currency: cur || "EUR" }));
  // header.jpg esiste per ogni gioco Steam; senza codice Steam (o se manca) si usa la copertina salvata alla ricerca
  const steamImg = (w) => remoteImg(w.steam_appid ? `https://cdn.cloudflare.steamstatic.com/steam/apps/${w.steam_appid}/header.jpg` : w.cover);
  const imgTag = (w) => (steamImg(w) ? `<img src="${steamImg(w)}" alt="" data-fb="${w.steam_appid && w.cover ? remoteImg(w.cover) : ""}" onerror="if(this.dataset.fb){this.src=this.dataset.fb;this.dataset.fb=''}else this.remove()">` : "");
  const s = d.settings;
  pv.innerHTML = `<div class="page">
    <div class="page-head"><div><h2>♡ Lista desideri</h2>
      <p class="muted small">Prezzi degli store ufficiali${s.has_key ? " (IsThereAnyDeal: Steam, GOG, Epic, Humble e altri)" : " (Steam; aggiungi la chiave IsThereAnyDeal per confrontare tutti gli store)"}.
        Negozi di key: GG.deals.
        Clicca un gioco per vedere tutte le offerte e i minimi storici.
        Avviso quando lo sconto supera il ${s.threshold}%, quando il prezzo (anche nei negozi di key) scende sotto quello che indichi o tocca il minimo storico.</p></div>
      <div class="row"><button class="btn" id="wl-import">⬇ Importa da Steam</button><button class="btn" id="wl-check">↻ Controlla prezzi</button>
        <button class="btn" id="wl-settings">⚙</button></div></div>
    <div class="wl-settings" hidden>
      <label>Chiave IsThereAnyDeal <input class="field" id="wl-key" placeholder="${s.has_key ? "già inserita (lascia vuoto per non cambiarla)" : "facoltativa"}">
        <a href="#" data-open="https://isthereanydeal.com/apps/my/">ottieni la chiave gratuita</a></label>
      <label>Chiave GG.deals <input class="field" id="wl-gg" placeholder="${s.has_gg ? "già inserita (lascia vuoto per non cambiarla)" : "facoltativa: prezzi dei negozi di key"}">
        <a href="#" data-open="https://gg.deals/api/">ottieni la chiave gratuita</a></label>
      <label>Paese <input class="field" id="wl-country" value="${esc(s.country)}" maxlength="2" style="width:60px"></label>
      <label>Avvisa da uno sconto del <input class="field" id="wl-thr" type="number" min="5" max="95" value="${s.threshold}" style="width:70px">%</label>
      <button class="btn primary" id="wl-save">Salva</button></div>
    <div class="wl-add"><input class="field grow" id="wl-q" placeholder="Cerca un gioco da aggiungere…" autocomplete="off"><div id="wl-res"></div></div>
    ${d.checking ? `<p class="muted small">Controllo dei prezzi in corso…</p>` : ""}
    ${!s.has_gg ? `<p class="muted small">🔑 Per i prezzi delle key aggiungi la chiave GG.deals in ⚙.</p>`
      : d.log.length ? `<p class="muted small">Ultimo avviso: ${esc(d.log[d.log.length - 1])}</p>` : ""}
    <div class="wl-list">${d.items.map((w) => `<div class="wl-row${w.alert ? " has-alert" : ""}${w.alert && !w.seen ? " new" : ""}" data-wid="${esc(w.id)}" title="Clicca per tutte le offerte">
      <div class="wl-img">${imgTag(w)}</div>
      <div class="wl-t"><b>${esc(w.title)}</b>${w.owned ? ` <span class="tag">già in libreria</span>` : ""}
        <span class="muted small">${w.checked ? `${esc(w.shop || "")} · controllato ${new Date(w.checked * 1000).toLocaleDateString("it")}` : "prezzo non ancora controllato"}
          ${w.low != null ? ` · minimo storico ${money(w.low, w.currency)}` : ""}</span>${priceSpark(w.history, money, w.currency)}</div>
      <div class="wl-price" title="${esc(w.steam ? "Steam" : w.shop || "")}">${(w.steam || w).cut ? `<span class="cut">-${(w.steam || w).cut}%</span><s class="muted">${money((w.steam || w).regular, w.currency)}</s>` : ""}<b>${money((w.steam || w).price, w.currency)}</b>
        ${w.key_price != null ? keyPrice(w, money, (w.steam || w).regular || (w.steam || w).price) : ""}</div>
      <div class="wl-alert">${w.alert ? `🔔 ${esc(w.alert)}` : ""}</div>
      <label class="wl-target" title="Avvisami quando costa meno di…">≤ <input class="field" type="number" step="0.5" min="0" data-target="${esc(w.id)}" value="${w.target ?? ""}" placeholder="€"></label>
      ${w.url ? `<button class="btn" data-open="${esc(w.url)}">Apri ↗</button>` : ""}
      ${w.key_url ? `<button class="btn" data-open="${esc(w.key_url)}" title="Confronto dei negozi di key su GG.deals">🔑 ↗</button>` : ""}
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
    else if (t.closest(".wl-row") && !t.closest("button, input, label, a")) openWishPrices(t.closest(".wl-row").dataset.wid);
    else if (t.id === "wl-settings") pv.querySelector(".wl-settings").hidden = !pv.querySelector(".wl-settings").hidden;
    else if (t.id === "wl-save") {
      const body = { country: $("#wl-country").value, threshold: $("#wl-thr").value };
      if ($("#wl-key").value.trim()) body.itad_key = $("#wl-key").value.trim();
      if ($("#wl-gg").value.trim()) body.gg_key = $("#wl-gg").value.trim();
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

/** Grafico a gradini del prezzo nel tempo: pts = [[data, prezzo]], low = linea tratteggiata del minimo storico. */
function lineChart(pts, color, low, money) {
  if (pts.length < 2) return `<p class="muted small">Non ci sono ancora abbastanza dati per il grafico.</p>`;
  const W = 640, H = 170, L = 58, B = 20, T = 8;
  const ts = pts.map(([d]) => +new Date(d)), vs = pts.map(([, v]) => v).concat(low != null ? [low] : []);
  const t0 = ts[0], t1 = ts[ts.length - 1], lo = Math.min(...vs), hi = Math.max(...vs);
  const x = (t) => L + (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * (W - L - 6));
  const y = (v) => T + (hi === lo ? (H - T - B) / 2 : (1 - (v - lo) / (hi - lo)) * (H - T - B));
  const path = pts.map(([, v], i) => (i ? `H${x(ts[i]).toFixed(1)}V${y(v).toFixed(1)}` : `M${x(t0).toFixed(1)},${y(v).toFixed(1)}`)).join("") + `H${x(t1).toFixed(1)}`;
  const day = (t) => new Date(t).toLocaleDateString("it", { day: "2-digit", month: "short", year: "2-digit" });
  return `<svg class="wp-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
    <text x="2" y="${T + 9}" class="ax">${money(hi)}</text><text x="2" y="${H - B}" class="ax">${money(lo)}</text>
    <text x="${L}" y="${H - 5}" class="ax">${day(t0)}</text><text x="${W - 4}" y="${H - 5}" class="ax" text-anchor="end">${day(t1)}</text>
    ${low != null ? `<line x1="${L}" x2="${W - 6}" y1="${y(low)}" y2="${y(low)}" stroke="${color}" stroke-dasharray="4 4" opacity=".5"><title>Minimo storico ${money(low)}</title></line>` : ""}
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

/** Dettaglio dei prezzi di un gioco: Steam, key più economica (GG.deals), offerte degli store (IsThereAnyDeal), grafici. */
async function openWishPrices(wid) {
  const dlg = $("#wl-dlg"), box = $("#wl-dlg-body");
  let d;
  try { d = await api(`/api/wishlist/prices/${encodeURIComponent(wid)}`); } catch (e) { return toast(e.message, true); }
  const money = (v) => (v == null ? "—" : v.toLocaleString("it", { style: "currency", currency: d.currency }));
  const a = d.detail || {}, steam = a.steam, key = a.key;
  const since = (days) => (days ? new Date(Date.now() - days * 864e5).toISOString().slice(0, 10) : "");
  // Steam: storico di ITAD + punti giornalieri registrati qui (questi vincono sullo stesso giorno)
  const byDay = new Map((a.hist || []).concat(d.history.filter((h) => h[1] != null).map((h) => [h[0], h[1]])));
  const steamPts = [...byDay].sort(([x], [y]) => (x < y ? -1 : 1));
  const keyPts = d.history.filter((h) => h[2] != null).map((h) => [h[0], h[2]]);
  const offers = a.offers || [];
  let range = 365;
  const draw = () => {
    const cut = (pts) => pts.filter(([day]) => day >= since(range));
    box.querySelector("#wp-charts").innerHTML = `
      <h4>Steam</h4>${lineChart(cut(steamPts), "#5b8cff", null, money)}
      <h4>Key più economica</h4>${keyPts.length > 1 ? lineChart(cut(keyPts), "#9be3b5", d.key_low, money)
        : `<p class="muted small">Lo storico delle key si costruisce giorno per giorno da quando segui il gioco${d.key_low != null ? ` (minimo storico: ${money(d.key_low)})` : ""}.</p>`}`;
    box.querySelectorAll("[data-range]").forEach((b) => b.classList.toggle("primary", +b.dataset.range === range));
  };
  box.innerHTML = `<div class="row"><h3 class="grow">${esc(d.title)}</h3>
      ${steam?.url ? `<button class="btn" data-open="${esc(steam.url)}">Steam ↗</button>` : ""}
      ${d.key_url ? `<button class="btn" data-open="${esc(d.key_url)}">GG.deals ↗</button>` : ""}
      <button class="icon-btn" id="wp-close" title="Chiudi">✕</button></div>
    <p class="muted small">${d.checked ? `Prezzi controllati il ${new Date(d.checked * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" })}.` : "Prezzi non ancora controllati."}
      ${d.has_gg ? "" : " Aggiungi la chiave GG.deals in ⚙ per le key."}${d.has_itad ? "" : " Aggiungi la chiave IsThereAnyDeal per il confronto degli store e lo storico."}</p>
    <div class="wp-cards">
      <div class="wp-card"><span class="muted small">Steam</span>
        <b>${steam ? money(steam.price) : "—"}</b>${steam?.cut ? ` <span class="cut">-${steam.cut}%</span> <span class="muted small">prezzo pieno <s>${money(steam.regular)}</s></span>` : ""}</div>
      <div class="wp-card key"><span class="muted small">Key più economica (GG.deals)</span>
        <b>${key?.key != null ? money(key.key) : "—"}</b>
        <span class="muted small">Minimo storico: ${money(key?.key_low)}</span></div>
      ${a.low != null ? `<div class="wp-card"><span class="muted small">Minimo storico negli store</span><b>${money(a.low)}</b></div>` : ""}
    </div>
    ${offers.length ? `<h4>Offerte degli store (${offers.length})</h4><table class="wp-table"><thead><tr><th>Negozio</th><th class="num">Prezzo pieno</th><th class="num">Sconto</th><th class="num">Prezzo</th><th></th></tr></thead>
      <tbody>${offers.map((o) => `<tr><td>${esc(o.shop)}</td><td class="num muted">${o.cut ? `<s>${money(o.regular)}</s>` : ""}</td>
        <td class="num">${o.cut ? `-${o.cut}%` : ""}</td><td class="num"><b>${money(o.price)}</b></td>
        <td>${o.url ? `<button class="btn" data-open="${esc(o.url)}">Apri ↗</button>` : ""}</td></tr>`).join("")}</tbody></table>` : ""}
    <div class="row wp-range"><span class="muted small grow">Andamento</span>
      <button class="btn" data-range="30">30 giorni</button><button class="btn" data-range="365">1 anno</button><button class="btn" data-range="0">Tutto</button></div>
    <div id="wp-charts"></div>`;
  box.onclick = (e) => {
    const t = e.target, o = t.closest("[data-open]"), r = t.closest("[data-range]");
    if (o) api("/api/open", { body: { url: o.dataset.open } });
    else if (r) { range = +r.dataset.range; draw(); }
    else if (t.id === "wp-close") dlg.close();
  };
  draw();
  if (!dlg.open) dlg.showModal();
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

/* ---- migliori giochi: Top 100 per console e classifica globale (100/1000) */
const TOP_F = { limit: +store.get("topLimit", 100) || 100, own: "", plat: "" };
let topTimer = 0;
async function renderTop(pv) {
  clearTimeout(topTimer);
  const pid = S.view.id || "all", glob = pid === "all";
  let d;
  try { d = await api(`/api/top?pid=${encodeURIComponent(pid)}&limit=${glob ? TOP_F.limit : 100}`); }
  catch (e) { pv.innerHTML = `<div class="page"><p>${esc(e.message)}</p></div>`; return; }
  if (S.view.type !== "top" || (S.view.id || "all") !== pid) return;
  const js = d.job, run = js.status === "running";
  const items = d.items.filter((t) => (!TOP_F.own || (TOP_F.own === "owned") === t.owned) && (!glob || !TOP_F.plat || t.platform === TOP_F.plat));
  const plats = [...new Set(d.items.map((t) => t.platform))].sort((a, b) => collator.compare(platName(a), platName(b)));
  const opt = (v, l, sel) => `<option value="${esc(v)}"${String(v) === String(sel) ? " selected" : ""}>${esc(l)}</option>`;
  const pct = (x) => Math.round(x * 100);
  const row = (t, i) => `<div class="top-row" data-id="${t.id}">
      <span class="top-n">${i + 1}</span>
      <span class="top-cv">${t.id ? `<img loading="lazy" src="/img/${t.id}/cover?w=120" alt="" onerror="this.remove()">` : ""}</span>
      <span class="top-t"><b>${esc(t.title)}</b>
        <span class="muted small">${[glob ? platName(t.platform) : "", t.year, t.also?.length ? `anche su ${t.also.map(platName).join(", ")}` : ""].filter(Boolean).map(esc).join(" · ")}</span>
        <span class="muted small">${[t.critics != null ? `Critica ${pct(t.critics)}` : "", t.rating != null ? `Giocatori ${pct(t.rating)}%${t.votes ? ` (${t.votes.toLocaleString("it")} voti)` : ""}` : "", t.sources.join(", ")].filter(Boolean).map(esc).join(" · ")}</span></span>
      <span class="badge ${t.owned ? "inst" : ""}">${t.owned ? "✓ Posseduto" : "⬇ Scaricabile"}</span>
      <span class="top-s" title="Punteggio fra critica e giocatori">${pct(glob ? t.best ?? t.score : t.score)}</span></div>`;
  pv.innerHTML = `<div class="page top">
    <p class="muted small">Solo giochi con voti affidabili (almeno 2 fonti, un voto della critica o 50 voti dei giocatori).
      Punteggio = media fra critica (Metacritic / IGDB) e giocatori. ${glob ? "Lo stesso gioco su più console conta una volta." : "Le versioni regionali contano una volta."}</p>
    <div class="top-tools">
      ${glob ? `<select class="field" id="top-limit">${opt(100, "Top 100", TOP_F.limit)}${opt(1000, "Top 1000", TOP_F.limit)}</select>
        <select class="field" id="top-plat">${opt("", "Tutte le console", TOP_F.plat)}${plats.map((p) => opt(p, platName(p), TOP_F.plat)).join("")}</select>` : ""}
      <select class="field" id="top-own">${opt("", "Posseduti e scaricabili", TOP_F.own)}${opt("owned", "Solo posseduti", TOP_F.own)}${opt("net", "Solo scaricabili", TOP_F.own)}</select>
      <button class="btn" id="top-fetch"${run ? " disabled" : ""} title="Chiede a IGDB e RAWG i voti dei candidati${glob ? " di tutte le console" : ""}: i giochi già cercati negli ultimi 30 giorni non si richiedono">↻ Aggiorna voti online${glob ? " (tutte le console)" : ""}</button>
      <button class="btn" id="top-stop"${run ? "" : " hidden"}>■ Ferma</button>
    </div>
    <div id="top-job"></div>
    ${items.length ? `<div class="top-list">${items.map(row).join("")}</div>`
      : `<p class="muted">Nessun gioco con voti affidabili${TOP_F.own || TOP_F.plat ? " con questi filtri" : ""}. Premi «↻ Aggiorna voti online» per cercarli su IGDB e RAWG.</p>`}</div>`;
  renderJobInto($("#top-job"), js);
  const re = () => renderTop(pv);
  $("#top-own").onchange = (e) => { TOP_F.own = e.target.value; re(); };
  if (glob) {
    $("#top-limit").onchange = (e) => { TOP_F.limit = +e.target.value; store.set("topLimit", TOP_F.limit); re(); };
    $("#top-plat").onchange = (e) => { TOP_F.plat = e.target.value; re(); };
  }
  $("#top-fetch").onclick = async () => {
    try { await api("/api/top/fetch", { body: { pid } }); } catch (e) { return toast(e.message, true); }
    re();
  };
  $("#top-stop").onclick = () => api("/api/top/cancel", { body: {} });
  pv.onclick = (e) => { const r = e.target.closest(".top-row[data-id]"); if (r && +r.dataset.id) openDetail(+r.dataset.id); };
  if (run) topTimer = setTimeout(async () => {
    const st = await api("/api/top/state").catch(() => null);
    if (st && st.status !== "running") { await reload(true); toast(st.phase, st.status === "error"); }
    if (S.view.type === "top") re();
  }, 2000);
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
  $("#pf-before").value = p.before || "";
  $("#pf-after").value = p.after || "";
  $("#pf-comp-exe").value = p.companion || "";
  $("#pf-comp-args").value = p.companion_args || "";
  $("#pf-comp-kill").checked = p.companion_close === "1";
  $("#pf-emu-box").hidden = !!PF.pc; // giochi PC: solo gli script
  if (PF.pc) return $("#prof-dlg").showModal();
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
  // app da avviare insieme al gioco (facoltativa), per giochi PC e console: tre campi nel profilo
  const comp = { companion: $("#pf-comp-exe").value.trim(), companion_args: $("#pf-comp-args").value.trim(),
    companion_close: $("#pf-comp-kill").checked ? "1" : "" };
  try {
    PF = Object.assign(PF, reset ? await api(`/api/game/${PF.id}/profile`, { method: "DELETE" })
      : await api(`/api/game/${PF.id}/profile`, { body: PF.pc ? { before: $("#pf-before").value, after: $("#pf-after").value, ...comp }
        : { emulator: $("#pf-emu").value, core: $("#pf-emu").value === "retroarch" || !$("#pf-emu").value ? $("#pf-core").value : "",
          exe: $("#pf-exe").value, args: $("#pf-args").value, extra: $("#pf-extra").value, before: $("#pf-before").value, after: $("#pf-after").value, ...comp } }));
  } catch (e) { return toast(e.message, true); }
  $("#prof-dlg").close();
  toast(reset ? "Profilo rimosso: si usano le impostazioni della console" : "Profilo di avvio salvato");
  if (S.detail?.id === PF.id) openDetail(PF.id);
}

/* ---- telefono e streaming */
let phoneTimer = 0;
const EMU_DSU = [
  ["Cemu (Wii U)", "Opzioni › Impostazioni di input › scheda del controller › Movimento: «DSU» (cemuhook), 127.0.0.1:26760."],
  ["Ryujinx (Switch)", "Opzioni › Impostazioni › Input › Configura › Movimento: «CemuHook compatible», server 127.0.0.1, porta 26760, slot del giocatore."],
  ["Dolphin (Wii)", "Controller › Sorgenti di input alternative › DSU Client: attiva, 127.0.0.1:26760; poi nel Wii Remote scegli il dispositivo DSUClient."],
  ["Azahar / Citra (3DS)", "Emulazione › Configura › Controlli › Movimento/Touch: «CemuhookUDP», 127.0.0.1:26760."],
];
async function loadRemote() {
  clearTimeout(phoneTimer);
  let ph, sun;
  try { [ph, sun] = await Promise.all([api("/api/phone"), api("/api/sunshine")]); } catch (e) { return toast(e.message, true); }
  $("#ph-on").checked = ph.enabled;
  const mins = ph.code_until ? Math.max(0, Math.round((ph.code_until - Date.now() / 1000) / 60)) : 0;
  $("#ph-box").innerHTML = !ph.enabled ? "" : ph.error ? `<p class="status-bad">${esc(ph.error)}</p>` : !ph.running ? `<p class="muted">Avvio…</p>` : `
    <h4>🔒 Certificato del PC (una volta sola)</h4>
    <p class="muted small">Con un certificato «autofirmato» il telefono mostra <i>connessione non privata</i> e il browser
      <b>rifiuta di installare l'app</b>. Installando invece la CA di questo PC l'avviso sparisce, l'app si installa e
      funziona anche senza rete.</p>
    <div class="ph-pair"${ph.ca_installed ? " hidden" : ""}>
      <div class="ph-qr">${ph.qr_ca || ""}</div>
      <div class="ph-steps">
        <b>Installa il certificato sul telefono</b>
        <ol>
          <li>Inquadra il QR (o apri <code>${esc(ph.ca_url || "")}</code>) e accetta l'avviso <i>Avanzate › Procedi</i>:
            è l'ultima volta che compare.</li>
          <li><b>Android</b>: Impostazioni › Sicurezza › Crittografia e credenziali › Installa un certificato ›
            <i>Certificato CA</i> › «Avvisa comunque» › scegli <code>PozzoLauncher-CA.crt</code>.</li>
          <li><b>iPhone/iPad</b>: Impostazioni › Generali › VPN e gestione dispositivi › installa il profilo; poi
            Impostazioni › Generali › Info › <i>Impostazioni attendibilità certificati</i> › attiva «Pozzo Launcher CA».</li>
          <li>Chiudi e riapri la pagina: niente avviso, e in alto compare <b>⬇ Installa</b> (su iPhone/iPad:
            <i>Condividi › Aggiungi alla schermata Home</i>).</li>
        </ol>
        <p class="muted small">Se il PC cambia indirizzo IP il certificato del server viene rifatto da solo, ma la CA resta
          la stessa: non serve reinstallare niente. La chiave <code>ca.key</code> resta nella cartella <code>certs</code> del PC.<br>
          Se il telefono non riesce a scaricarlo (download bloccato per l'avviso di sicurezza), salvalo sul PC con
          <a href="/api/phone/ca" download="PozzoLauncher-CA.crt">questo link</a> e passalo al telefono con cavo, e-mail o cloud.</p>
      </div>
    </div>
    <label class="small"><input type="checkbox" id="ph-ca-ok"${ph.ca_installed ? " checked" : ""}>
      Ho già installato il certificato sul telefono (nascondi il QR)</label>
    <div class="ph-pair">
      <div class="ph-qr">${ph.qr}</div>
      <div class="ph-steps">
        <b>Abbina un telefono</b>
        <ol>
          <li>Collega il telefono alla stessa rete Wi-Fi del PC.</li>
          <li>Inquadra il QR con la fotocamera, oppure apri <code>${esc(ph.urls[0] || "")}</code> e inserisci il codice
            <span class="ph-code">${esc(ph.code || "")}</span> <span class="muted small">(valido ancora ${mins} min, <a href="#" id="ph-newcode">nuovo codice</a>)</span></li>
          <li>Se compare ancora «non privata», manca il certificato del PC (punto sopra).</li>
        </ol>
        <p class="muted small">La prima volta Windows può chiedere di consentire Pozzo Launcher sulle reti private: rispondi Sì.</p>
      </div>
    </div>
    <h4>Telefoni abbinati</h4>
    ${ph.devices.length ? `<table class="emu">${ph.devices.map((d) => `<tr><td>📱 ${esc(d.name)}</td>
      <td class="muted small">abbinato ${new Date(d.created * 1000).toLocaleDateString("it")} · ultimo uso ${new Date(d.last * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" })}</td>
      <td><button class="btn" data-revoke="${esc(d.id)}">Revoca</button></td></tr>`).join("")}</table>` : `<p class="muted small">Nessuno.</p>`}
    <h4>Controller dal telefono</h4>
    <p class="small">${ph.vigem ? "✅ Controller virtuale Xbox pronto (ViGEmBus)" : `❌ ${esc(ph.vigem_error)} — <a href="#" data-open="https://github.com/nefarius/ViGEmBus/releases">scarica ViGEmBus</a>`}<br>
      ${ph.dsu ? "✅ Giroscopio per gli emulatori: server DSU attivo su 127.0.0.1:26760" : `❌ ${esc(ph.dsu_error || "Server DSU non attivo")}`}</p>
    ${ph.controllers.length ? `<div class="lobby small">${[0, 1, 2, 3].map((slot) => {
      const c = ph.controllers.find((x) => x.slot === slot);
      return `<div class="lb-seat" style="--pc:${c ? c.color : "#555"}"><b>Giocatore ${c ? c.player : slot + 1}</b>
        <span>${c ? `📱 ${esc(c.name)}${c.motion ? " · 🌀" : ""}` : `<i class="muted">libero</i>`}</span>
        ${c ? `<select data-swap="${slot}"><option value="">Sposta al posto…</option>${[0, 1, 2, 3].filter((s) => s !== slot)
          .map((s) => `<option value="${s}">${s + 1}</option>`).join("")}</select>` : ""}</div>`;
    }).join("")}</div>` : ""}
    <h4>Controller speciali</h4>
    <div class="row wrap small">
      <label class="chk"><input type="checkbox" id="ph-mouse" ${ph.mouse ? "checked" : ""}> Il telefono può muovere il mouse del PC
        <span class="muted">(pistola ottica / touchpad: dà il controllo del mouse a chi ha un telefono abbinato)</span></label>
      <label class="chk"><input type="checkbox" id="ph-touch" ${ph.touchscreen ? "checked" : ""}> Il telefono fa da schermo touch nei giochi DS e 3DS
        <span class="muted">(se è collegato all'avvio: sul PC solo lo schermo superiore, il touch sul telefono; con melonDS, Citra, Azahar, Lime3DS)</span></label>
      <label class="chk">Se il telefono non si usa per <input class="field" id="ph-touch-idle" type="number" min="5" max="600" step="5" value="${ph.screen_idle || 30}" style="width:70px"> secondi
        lo schermo inferiore ricompare sul PC
        <span class="muted">(prima resta nascosto dietro lo schermo superiore; se il telefono si scollega torna dopo pochi secondi)</span></label>
    </div>
    <div class="row wrap small">
      <label>Microfono del telefono (SingStar) → uscita del PC
        <select class="field" id="ph-mic"><option value="">Automatica (cavo virtuale, es. VB-Cable)</option>
          ${ph.mic_devices.map((d) => `<option ${d === ph.mic_device ? "selected" : ""}>${esc(d)}</option>`).join("")}</select></label>
      ${ph.mic_devices.some((d) => /CABLE|VB-Audio/i.test(d)) ? "" : `<span class="muted">Nessun cavo virtuale: <a href="#" data-open="https://vb-audio.com/Cable/">installa VB-Cable</a> (gratis), poi in PCSX2 scegli «CABLE Output» come microfono.</span>`}
    </div>
    <div class="row wrap small emusetup">
      <label>Telefoni <input class="field" id="es-n" type="number" min="1" max="4" value="${Math.max(1, ph.controllers.length)}" style="width:60px"></label>
      <button class="btn" data-es="pcsx2_buzz">🔴 Configura PCSX2 per Buzz!</button>
      <button class="btn" data-es="pcsx2_guitar">🎸 Configura PCSX2 per la chitarra</button>
      <button class="btn" data-es="dolphin_wii">🎮 Configura Dolphin per il Wii Remote</button>
    </div>
    <p class="muted small">Scrive le impostazioni dell'emulatore (chiudilo prima) e ne fa una copia accanto all'originale.
      Buzz!: porta USB 1 = Buzz Controller, telefono N = giocatore N. Chitarra: le porte 1..N diventano chitarre.
      Dolphin: Wii Remote 1..N = telefono 1..N, con Nunchuk, tasti e movimento dal server DSU.
      <a href="#" data-esundo="pcsx2_buzz">Annulla PCSX2</a> · <a href="#" data-esundo="dolphin_wii">Annulla Dolphin</a></p>
    <details class="small"><summary>Come attivare il giroscopio negli emulatori</summary>
      <ul>${EMU_DSU.map(([n, t]) => `<li><b>${n}</b>: ${t}</li>`).join("")}</ul>
      <p class="muted">Per i giochi senza giroscopio, nell'app del telefono scegli 🎯 <b>Mira</b>: inclinando il telefono si muove la levetta destra.</p>
    </details>`;
  const s = sun;
  if (document.activeElement.id !== "ph-public") $("#ph-public").value = ph.public_url || "";
  if (document.activeElement.id !== "ph-video") $("#ph-video").value = ph.video_addr || "";
  const inv = ph.invite || {};
  const gmin = inv.until ? Math.max(0, Math.round((inv.until - Date.now() / 1000) / 60)) : 0;
  $("#guest-box").innerHTML = `<p class="small">${inv.code
    ? `Manda all'amico il link <code>${esc(inv.link)}</code> e il codice <span class="ph-code">${esc(inv.code)}</span>
       <span class="muted small">(vale ancora ${gmin} min, per un ospite solo)</span>`
    : `<span class="muted">Nessun invito attivo: l'ospite non può entrare.</span>`}
    <button class="btn primary" id="ph-invite">${inv.code ? "Nuovo invito" : "Crea invito per un amico"}</button></p>
    <p class="muted small">Il video va con Moonlight: l'amico scrive l'indirizzo qui sopra e il PIN che Moonlight gli
      mostra, e il PC li conferma (serve un utente e una password di Sunshine salvati qui sotto).</p>
    ${(ph.guests || []).length ? `<h4>Ospiti</h4><table class="emu">${ph.guests.map((d) => `<tr><td>🙋 ${esc(d.name)}</td>
      <td class="muted small">entrato ${new Date(d.created * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" })}</td>
      <td><button class="btn" data-revoke="${esc(d.id)}">Togli</button></td></tr>`).join("")}</table>` : ""}`;
  $("#sun-box").innerHTML = !s.installed ? `<p>Sunshine non è installato. <a href="#" data-open="https://github.com/LizardByte/Sunshine/releases">Scaricalo</a>, poi riapri questa scheda.</p>`
    : `<p class="small">${s.service === "running"
      ? `✅ Sunshine è in esecuzione${s.start_mode && s.start_mode !== "auto" ? ` · ⚠ ma non parte con Windows: dopo un riavvio Moonlight non troverà il PC. <button class="btn" id="sun-start">Avvia sempre con Windows</button>` : ""}`
      : s.service === "stopped"
      ? `⏸ Sunshine è installato ma fermo: Moonlight non può trovare il PC. <button class="btn primary" id="sun-start">▶ Avvia Sunshine (anche con Windows)</button> <span class="muted">(chiede una volta i permessi di amministratore)</span>` : "Sunshine installato"}
      · <a href="#" data-open="${esc(s.web)}">interfaccia web di Sunshine</a> (per abbinare Moonlight con il PIN)</p>
    <div class="row wrap">
      <label>Utente di Sunshine <input class="field" id="sun-user" value="${esc(s.user)}" autocomplete="off"></label>
      <label>Password <input class="field" id="sun-pass" type="password" placeholder="${s.has_password ? "già salvata" : ""}" autocomplete="new-password"></label>
      <label>Giochi da pubblicare <input class="field" id="sun-count" type="number" min="0" max="100" value="${s.count}" style="width:80px"></label>
      <button class="btn primary" id="sun-pub">📡 Pubblica in Moonlight</button>
    </div>
    ${s.error ? `<p class="status-bad small">${esc(s.error)}</p>` : ""}
    <p class="muted small">Verranno pubblicati: <b>Pozzo Launcher Big Picture</b> (da lì scegli qualsiasi gioco col controller),
      <b>▶ Gioco in corso</b> (lo schermo del PC: per entrare nel gioco avviato dall'app del telefono con «Gioca su questo telefono»)
      ${s.preview.length ? ` e ${s.preview.length} giochi (in corso, preferiti, recenti): ${esc(s.preview.slice(0, 8).join(", "))}${s.preview.length > 8 ? "…" : ""}` : ""}.
      Le altre app di Sunshine non vengono toccate.</p>
    ${s.last ? `<p class="small">Ultima pubblicazione: ${new Date(s.last.time * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" })} · ${s.last.published} app</p>` : ""}
    ${s.apps ? `<p class="small muted">App in Moonlight ora: ${esc(s.apps.map((a) => a.name).join(" · "))}</p>` : ""}`;
  api("/api/stream/optimize").then((o) => {
    const ico = { ok: "✓", warn: "⚠", info: "ℹ" };
    $("#stream-opt").innerHTML = `<div class="health">` + o.checks.map((x) => `<div class="hl-row hl-${x.level === "info" ? "ok" : x.level}">`
      + `<span class="hl-ico">${ico[x.level] || "•"}</span><div class="hl-txt"><b>${esc(x.title)}</b>${x.detail ? `<div class="muted small">${esc(x.detail)}</div>` : ""}</div></div>`).join("") + `</div>`;
  }).catch(() => {});
  if (ph.enabled && SET.tab === "remote" && $("#settings").open) phoneTimer = setTimeout(loadRemote, ph.running ? 5000 : 1000);
}
async function phoneSet(body) {
  try { await api("/api/phone", { body }); } catch (e) { toast(e.message, true); }
  loadRemote();
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
    if (js.status === "done") {
      await reload(true);
      toast("Store aggiornati: libreria aggiornata");
      if (S.view.type === "ports") { S.ports = null; renderPorts($("#plugin-view")); } // pulsante «Gioca» del port appena installato
    }
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
  if (d.meta && d.meta.status === "running") pollMeta();
}

function renderCatalog(d) {
  $("#cat-pages").innerHTML = d.pages.map((p) => `<div class="muted">${esc(p.url)} — ${p.games.toLocaleString("it")} giochi`
    + (p.time ? ` · letta ${new Date(p.time * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" })}` : " · mai letta") + `</div>`).join("");
  $("#cat-log").textContent = (d.job.log || []).slice(-10).join("\n");
  renderMeta(d.meta, d.meta_coverage);
}

/* copertine e descrizioni delle voci online (GameTDB offline + pagine dei siti) */
function metaBars(m, cov) {
  const s = (m && m.summary) || {};
  const total = s.games || cov.total || 0;
  if (!total) return "";
  const row = (label, done) => {
    const pct = Math.min(100, Math.round((100 * (done || 0)) / total));
    return `<div class="bar"><span>${label}</span><div class="track"><div class="fill" style="width:${pct}%"></div></div>`
      + `<span class="n">${(done || 0).toLocaleString("it")} / ${total.toLocaleString("it")}</span></div>`;
  };
  return `<div class="bars">${row("🖼 Copertine", s.covers ?? cov.covers)}${row("📝 Descrizioni", s.descriptions ?? cov.descriptions)}</div>`;
}

function renderMeta(m, cov) {
  const box = $("#cat-meta-info");
  const btn = $("#cat-meta-btn");
  SET.metaRunning = !!(m && m.status === "running");
  cov = cov || {};
  if (!m || m.status === "idle") {
    btn.textContent = "🖼 Copertine e descrizioni";
    box.innerHTML = cov.total ? `<span class="muted small">Ultimo giro:</span>${metaBars(null, cov)}` : "";
    return;
  }
  const s = m.summary || {};
  if (m.status === "running") {
    btn.textContent = "⏹ Ferma";
    box.innerHTML = `<div class="phase">${esc(m.phase || "In corso")} · ${(s.pages || 0).toLocaleString("it")} pagine lette</div>`
      + metaBars(m, cov);
  } else if (m.status === "done") {
    btn.textContent = "🖼 Copertine e descrizioni";
    box.innerHTML = `<div class="phase">${esc(m.phase)}</div>` + metaBars(m, cov);
  } else {
    btn.textContent = "🖼 Copertine e descrizioni";
    box.innerHTML = `<div class="phase">${esc(m.phase || m.status)}</div>` + metaBars(m, cov);
  }
}

let metaTimer = 0;
async function pollMeta() {
  clearTimeout(metaTimer);
  const d = await api(`/api/catalogs/${SET.catPid}`).catch(() => null);
  if (!d) return;
  renderCatalog(d);
  if (d.meta && d.meta.status === "running") { metaTimer = setTimeout(pollMeta, 2000); return; }
  if (d.meta && d.meta.status === "done") { await reload(true); toast("Copertine e descrizioni aggiornate"); }
  else if (d.meta && d.meta.status === "error") toast("Lettura delle pagine non riuscita: vedi il registro", true);
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

/* ---- altri store PC: repack / download diretto */
let PCST = { rows: [], filter: "all", timer: 0 };

async function loadPcStores() {
  let d;
  try { d = await api("/api/pc-stores"); } catch (e) { return toast(e.message, true); }
  PCST.rows = d.stores || [];
  renderPcStores(d.job);
  if (d.job && d.job.status === "running") pollPcStores();
}

function renderPcStores(j) {
  const rows = PCST.rows.filter((r) => PCST.filter === "all" || r.kind === PCST.filter);
  $("#pcst-table").innerHTML = rows.length ? rows.map((r) => `<tr>
      <td class="small">${esc(r.url)}${r.type && r.type !== "filelist" ? ` <span class="src-kind offline">${esc(r.type)}</span>` : ""}</td>
      <td><select class="field" data-pcst-kind="${esc(r.url)}">
        <option value="repack"${r.kind === "repack" ? " selected" : ""}>📦 Repack</option>
        <option value="direct"${r.kind === "direct" ? " selected" : ""}>⬇ Download diretto</option></select></td>
      <td class="num">${r.games ? r.games.toLocaleString("it") : "—"}</td>
      <td><button class="btn" data-pcst-del="${esc(r.url)}">Rimuovi</button></td></tr>`).join("")
    : `<tr><td colspan="4" class="muted small">Nessuno store aggiunto${PCST.filter !== "all" ? " in questa categoria" : ""}.</td></tr>`;
  const jj = j || {};
  $("#pcst-info").textContent = jj.status === "running" ? (jj.phase || "Lettura degli elenchi…") : "";
  $("#pcst-log").textContent = (jj.log || []).slice(-8).join("\n");
  $("#pcst-refresh").disabled = jj.status === "running";
}

async function savePcStores(stores, refresh) {
  const body = { stores: stores.map((r) => ({ url: r.url, kind: r.kind })), refresh: !!refresh };
  let d;
  try { d = await api("/api/pc-stores", { body }); } catch (e) { return toast(e.message, true); }
  PCST.rows = d.stores || [];
  renderPcStores(d.job);
  if (refresh) pollPcStores();
}

async function pollPcStores() {
  clearTimeout(PCST.timer);
  const d = await api("/api/pc-stores").catch(() => null);
  if (!d) return;
  PCST.rows = d.stores || [];
  renderPcStores(d.job);   // la tabella si ridisegna; l'avanzamento lo segue il blocco «Aggiornamento degli store»
  if (d.job && d.job.status === "running") PCST.timer = setTimeout(pollPcStores, 3000);
}

/* ---- impostazioni › aggiornamento degli store (PC + console), in parallelo */
const SRF_ICON = { idle: "·", pending: "·", running: "⏳", done: "✓", error: "✕", skip: "⏭" };
const srfOpen = new Set(); // store con il log aperto (sopravvive ai aggiornamenti periodici)
let srfTimer = 0;
let srfLast = null;        // ultima risposta di /api/store-refresh (per ridisegnare senza rileggere)

function srfDur(sec) {
  if (!sec || sec < 1) return "—";
  if (sec < 90) return `${Math.round(sec)} s`;
  if (sec < 5400) return `${Math.round(sec / 60)} min`;
  return `${Math.floor(sec / 3600)} h ${String(Math.round((sec % 3600) / 60)).padStart(2, "0")}`;
}

/** Riga di dettaglio di uno store: pagina, ritmo e tempo che manca. */
function srfMeta(t) {
  if (t.status === "error") return esc(t.error || "errore");
  if (t.status === "idle") return t.kind === "repack" ? "📦 Repack" : t.kind === "direct" ? "⬇ Diretto" : "";
  if (t.status === "pending") return t.position ? `in coda · posizione ${t.position}` : "in coda";
  const gameCount = t.status === "running" ? (t.seen || 0) : (t.games || 0);
  const bits = [];
  if (t.pages > 1) bits.push(`pagina ${t.page || 0}/${t.pages}`);
  if (gameCount) bits.push(`${gameCount.toLocaleString("it")} giochi`);
  if (t.rate) bits.push(`${t.rate.toLocaleString("it", { maximumFractionDigits: 2 })} s/pagina`);
  if (t.status === "running") {
    if (t.eta) bits.push(`≈ ${srfDur(t.eta)} alla fine`);
    bits.push(`da ${srfDur(t.elapsed)}`);
  } else if (t.status === "done") bits.push(srfDur(t.elapsed));
  return esc(bits.join(" · "));
}

function renderStoreRefreshJob(js, list) {
  const running = js.status === "running";
  // «Aggiorna tutti» resta sempre disponibile: aggiunge alla coda gli store non ancora in coda/in corso
  $("#srf-run").hidden = false;
  const stop = $("#srf-stop");
  // sparisce appena non c'è più nulla da interrompere (fine lettura, ricostruzione della libreria)
  stop.hidden = !js.stoppable;
  stop.disabled = !!js.stopping;
  stop.textContent = js.stopping ? "■ Interruzione…" : "■ Interrompi";
  $("#srf-log").textContent = (js.log || []).slice(-12).join("\n");
  // a riposo si mostrano comunque tutti gli store configurati; durante un aggiornamento lo stato
  // del singolo store si sovrappone alla lista, così le altre righe restano visibili
  const live = new Map((js.targets || []).map((t) => [t.id || t.url, t]));
  const base = (list && list.length) ? list : (js.targets || []);
  const targets = base.map((t) => live.get(t.id || t.url) || { ...t, status: "idle", log: [] });
  for (const t of (js.targets || [])) {
    if (!targets.some((x) => (x.id || x.url) === (t.id || t.url))) targets.push(t);
  }
  if (js.status === "idle" && !targets.length) { $("#srf-progress").innerHTML = ""; return; }
  const done = js.done || 0, total = js.total || 0;
  const pct = total ? Math.round((100 * done) / total) : 0;
  const rows = targets.map((t) => {
    const id = t.id || t.url;
    const open = srfOpen.has(id);
    const log = (t.log || []).slice(-12).join("\n");
    const lastLog = t.status === "running" && (t.log || []).length ? t.log[t.log.length - 1] : "";
    const busy = t.status === "pending" || t.status === "running";
    return `<div class="srf-row">
      <span class="srf-ico">${SRF_ICON[t.status] || "·"}</span>
      <span class="grow">${esc(t.label)}</span>
      <span class="muted small srf-meta">${srfMeta(t)}</span>
      <button class="icon-btn" data-srf-run="${esc(id)}" title="Aggiungi alla coda: aggiorna solo questo store"${busy ? " disabled" : ""}>⟳</button>
      <button class="icon-btn" data-srf-log="${esc(id)}" title="Log di questo store">📄</button></div>
      ${lastLog ? `<div class="srf-last muted small">${esc(lastLog.slice(0, 150))}</div>` : ""}
      ${open && log ? `<pre class="log srf-rowlog">${esc(log)}</pre>` : ""}`;
  }).join("");
  // il riquadro si ridisegna ogni 1,5 s: si conserva la posizione di scorrimento
  const scrolled = $("#srf-progress .srf-list")?.scrollTop || 0;
  const bar = js.status === "idle" ? "" : `<div class="bar"><span>${esc(js.phase || "")}</span>
      <div class="track"><div class="fill" style="width:${pct}%"></div></div>
      <span class="n">${done} / ${total} · ${(js.games || 0).toLocaleString("it")} giochi`
    + `${js.queued ? ` · ${js.queued} in coda` : ""}${js.running ? ` · ${js.running} in corso` : ""}</span></div>`;
  $("#srf-progress").innerHTML = `${bar}<div class="srf-list">${rows}</div>`;
  const list2 = $("#srf-progress .srf-list");
  if (list2 && scrolled) list2.scrollTop = scrolled;
}

function renderStoreRefresh(d) {
  srfLast = d;
  const sch = d.schedule || {};
  $("#srf-mode").value = sch.mode || "startup";
  $("#srf-time").value = sch.time || "03:00";
  $("#srf-time").disabled = sch.mode !== "daily";
  $("#srf-workers").value = sch.workers || 4;
  const last = d.last ? String(d.last).replace("T", " ") : "";
  $("#srf-info").textContent = `${(d.stores || 0).toLocaleString("it")} elenchi configurati`
    + (last ? ` · ultimo: ${last}` : "")
    + (d.job?.log_file ? ` · log su file: ${d.job.log_file}` : "");
  renderStoreRefreshJob(d.job || { status: "idle" }, d.list);
}

async function loadStoreRefresh() {
  let d;
  try { d = await api("/api/store-refresh"); } catch (e) { return toast(e.message, true); }
  renderStoreRefresh(d);
  if (d.job && d.job.status === "running") pollStoreRefresh();
}

async function pollStoreRefresh() {
  clearTimeout(srfTimer);
  const d = await api("/api/store-refresh").catch(() => null);
  if (!d) return;
  renderStoreRefresh(d);
  if (d.job && d.job.status === "running") { srfTimer = setTimeout(pollStoreRefresh, 1500); return; }
  if (d.job && d.job.status === "done") {
    await reload(true);
    if ((d.job.phase || "").startsWith("Interrotto")) toast(d.job.phase);
    else toast(`Store aggiornati: ${(d.job.games || 0).toLocaleString("it")} giochi`);
  }
  else if (d.job && d.job.status === "error") toast("Aggiornamento non riuscito: vedi il registro", true);
}

/* ---- impostazioni › account degli store */
async function loadAccounts() {
  loadStoreRefresh(); // blocco «Aggiornamento degli store»: indipendente dagli account
  api("/api/ra").then((r) => { $("#ra-user").value = r.username; $("#ra-key").placeholder = r.has_key ? "già salvata (lascia vuoto per non cambiarla)" : ""; }).catch(() => {});
  let d;
  try { d = await api("/api/accounts"); } catch (e) { return toast(e.message, true); }
  SET.accounts = d;
  const when = d.refreshed ? new Date(d.refreshed * 1000).toLocaleString("it", { dateStyle: "short", timeStyle: "short" }) : "mai";
  $("#acc-info").textContent = `Ultimo aggiornamento: ${when}`;
  $("#acc-table").innerHTML = d.stores.filter((x) => x.id !== "manual").map((x) => {
    const a = x.account || {};
    let ctrl = "";
    if (x.id === "ports") {
      ctrl = `<span class="muted small">port non ufficiali installati</span> <button class="btn" data-ports-open>Gestisci</button>`;
    } else if (x.id === "steam") {
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
      <label class="chk"><input type="checkbox" data-plugin="${esc(p.id)}" ${p.enabled ? "checked" : ""} ${p.locked ? "disabled" : ""}> <b>${esc(p.name)}</b></label>
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
  fitSettings();
}

/** La finestra delle impostazioni si adatta a schede e pagina; se non ci sta nello schermo va a tutto schermo
    e scorrono in orizzontale solo le schede (rotella, trascinamento, dito), mai il contenuto. */
function fitSettings() {
  const dlg = $("#settings");
  if (!dlg.open) return;
  const head = $(".dlg-head", dlg), tabs = $("#tabs"), body = $(".dlg-body", dlg);
  const keep = body.scrollTop;                           // la rimisura non deve far saltare la pagina
  dlg.classList.remove("full");
  dlg.style.width = "";                                  // misura partendo dalla larghezza di base
  const base = dlg.clientWidth;
  const tabsW = [...tabs.children].reduce((w, t) => w + t.offsetWidth, 0) + 4 * Math.max(0, tabs.children.length - 1);
  const chrome = head.clientWidth - tabs.clientWidth;    // padding e pulsante di chiusura
  const need = Math.max(base, tabsW + chrome, base + body.scrollWidth - body.clientWidth);  // schede e contenuto piu' larghi
  if (need > innerWidth * 0.94 || innerHeight < 560) dlg.classList.add("full");
  else if (need > base) dlg.style.width = Math.ceil(need) + 2 + "px";   // +2: il bordo
  body.scrollTop = keep;
  fadeTabs();
  scrollActiveTab();
}

function fadeTabs() {
  const t = $("#tabs");
  t.classList.toggle("can-l", t.scrollLeft > 1);
  t.classList.toggle("can-r", t.scrollLeft + t.clientWidth < t.scrollWidth - 1);
}

function scrollActiveTab() {
  const t = $("#tabs"), a = t.querySelector(".tab.active");
  if (!a) return;
  const r = a.getBoundingClientRect(), b = t.getBoundingClientRect();
  if (r.left < b.left + 36) t.scrollLeft -= b.left + 36 - r.left;
  else if (r.right > b.right - 36) t.scrollLeft += r.right - (b.right - 36);
}

function initSettingsLayout() {
  const tabs = $("#tabs");
  tabs.addEventListener("scroll", fadeTabs, { passive: true });
  tabs.addEventListener("wheel", (e) => {              // rotella verticale = scorrimento delle schede
    if (tabs.scrollWidth > tabs.clientWidth && Math.abs(e.deltaY) > Math.abs(e.deltaX)) { tabs.scrollLeft += e.deltaY; e.preventDefault(); }
  }, { passive: false });
  let drag = null, dragEnd = 0;
  tabs.addEventListener("pointerdown", (e) => { if (e.pointerType === "mouse" && e.button === 0) drag = { x: e.clientX, l: tabs.scrollLeft, moved: false }; });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x;
    if (Math.abs(dx) > 5) drag.moved = true;
    if (drag.moved) tabs.scrollLeft = drag.l - dx;
  });
  window.addEventListener("pointerup", () => { if (drag?.moved) dragEnd = performance.now(); drag = null; });
  tabs.addEventListener("click", (e) => { if (performance.now() - dragEnd < 80) { e.stopPropagation(); e.preventDefault(); } }, true);  // dopo un trascinamento non si cambia scheda
  tabs.addEventListener("focusin", scrollActiveTab);
  // contenuto che arriva dopo (le schede caricano via rete) o finestra ridimensionata: si rimisura
  let raf = 0;
  const refit = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; fitSettings(); }); };
  window.addEventListener("resize", refit);
  new MutationObserver(refit).observe($("#settings .dlg-body"), { childList: true, subtree: true });
}

function showTab(tab) {
  SET.tab = tab;
  document.querySelectorAll("#tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll("[data-pane]").forEach((p) => p.classList.toggle("active", p.dataset.pane === tab));
  if (tab === "health") loadHealth();
  else if (tab === "about") loadAbout();
  else if (tab === "scan") { loadScan(); renderCustomConsoles(); loadPower(); }
  else if (tab === "emu") loadEmu();
  else if (tab === "remote") loadRemote();
  else if (tab === "acc") { loadAccounts(); loadPcStores(); }
  else if (tab === "src") loadSources();
  else if (tab === "integ") loadIntegrations();
  else if (tab === "look") loadAppearance();
  else if (tab.startsWith("ext:")) {
    const t = GS.ui.settingsTabs.find((x) => "ext:" + x.id === tab);
    const pane = document.querySelector(`[data-pane="${CSS.escape(tab)}"]`);
    if (t && pane) { pane.innerHTML = ""; Promise.resolve(t.render(pane, GS)).catch((e) => { pane.textContent = e.message; }); }
  } else { api("/api/status").then(showStatus); loadBackups(); loadSync(); loadSaveDir(); }
  if (tab === "remote") startPadTest(); else stopPadTest();  // la prova controller gira solo mentre la scheda e' aperta
}

/* ---- impostazioni › integrazioni (Discord) */
function renderIntegrations(d) {
  const dc = (d && d.discord) || {};
  $("#dc-on").checked = !!dc.enabled;
  $("#dc-time").checked = dc.show_time !== false;
  if (dc.client_id) $("#dc-id").value = dc.client_id;
  let html;
  if (!dc.enabled) html = `<span class="muted">Presenza spenta.</span>`;
  else if (!dc.client_id) html = `<span class="status-bad">Manca l'ID applicazione: senza, Discord non accetta la presenza.</span>`;
  else if (dc.connected) html = `✅ <b>Collegato a Discord</b>${dc.playing ? ` · in onda: ${esc(dc.playing)}` : ""}`;
  else {
    html = `<span class="status-bad">Non collegato: ${esc(dc.error || "Discord non risponde")}</span>`
      + ` <span class="muted small">(apri il client Discord sul PC e premi Salva)</span>`;
  }
  if (d && d.log_file) html += `<br><span class="muted small">Log su file: ${esc(d.log_file)}</span>`;
  $("#dc-state").innerHTML = html;
}

async function loadIntegrations() {
  try { renderIntegrations(await api("/api/integrations")); } catch (e) { toast(e.message, true); }
  try {
    const n = await api("/api/app/notify");
    $("#nt-on").checked = !!n.enabled;
    $("#nt-state").textContent = n.available ? "" : "Le notifiche di Windows sono disponibili solo su Windows.";
  } catch { /* niente: la scheda Discord funziona lo stesso */ }
}

/* ---- prova controller: ultimo stato ricevuto da ogni telefono (la scheda «Telefono e streaming») */
let padTestT = 0;
function stopPadTest() { clearTimeout(padTestT); padTestT = 0; }
function startPadTest() {
  stopPadTest();
  const tick = async () => {
    const box = $("#pads-test");
    if (!box || !$("#settings").open || SET.tab !== "remote") return stopPadTest();
    try { renderPads(await api("/api/phone/pads")); } catch (e) { box.textContent = e.message; }
    padTestT = setTimeout(tick, 400);
  };
  tick();
}
const PAD_BTN_LABEL = { a: "A", b: "B", x: "X", y: "Y", lb: "LB", rb: "RB", ls: "L3", rs: "R3",
  up: "▲", down: "▼", left: "◀", right: "▶", start: "Start", back: "Select", guide: "Guide" };
function renderPads(d) {
  const box = $("#pads-test");
  if (!box) return;
  const pads = d.pads || [];
  if (!pads.length) { box.innerHTML = `<p class="muted small">Nessun telefono collegato come controller (apri 🎮 sul telefono).</p>`; return; }
  const dot = (x, y) => `<i style="left:${50 + x * 45}%;top:${50 - y * 45}%"></i>`;
  box.innerHTML = pads.map((p) => `<div class="pad-t" style="--pc:${esc(p.color)}">
    <div><b>Giocatore ${p.player}</b> <span class="muted small">${esc(p.name)}${p.motion ? " · 🌀" : ""}${p.connected ? "" : " · controller virtuale non attivo"}</span></div>
    <div class="pad-k">${Object.entries(PAD_BTN_LABEL).map(([k, l]) => `<i class="${p.buttons.includes(k) ? "on" : ""}">${l}</i>`).join("")}</div>
    <div class="pad-x"><span class="stick">${dot(p.lx, p.ly)}</span><span class="stick">${dot(p.rx, p.ry)}</span>
      <label class="trig">LT<i><b style="width:${Math.round(p.lt * 100)}%"></b></i></label>
      <label class="trig">RT<i><b style="width:${Math.round(p.rt * 100)}%"></b></i></label>
      <button class="btn" type="button" data-pad-rumble="${p.slot}">📳 Vibra</button></div>
  </div>`).join("");
}

/* ---- al termine delle operazioni: sospendi o spegni il PC */
async function loadPower() {
  try {
    const p = await api("/api/app/power");
    $("#pw-after").value = p.after_jobs || "none";
    $("#pw-abort").hidden = !p.seconds;
    $("#pw-state").textContent = p.seconds ? `${p.mode === "sleep" ? "Sospensione" : "Spegnimento"} tra ${p.seconds} s` : "";
  } catch { /* niente */ }
}

/* ---- info: versione, aggiornamenti, novita' */
/** Markdown minimo del changelog (titoli, elenchi, grassetto, codice). Il testo e' sempre passato da esc() prima. */
function miniMd(md) {
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "", list = false;
  for (const raw of md.split("\n")) {
    const l = raw.trimEnd();
    const li = /^\s*- (.*)/.exec(l), cont = list && /^\s{2,}\S/.test(l);
    if (cont) { html = html.replace(/<\/li>$/, " " + inline(l.trim()) + "</li>"); continue; }
    if (list && !li) { html += "</ul>"; list = false; }
    if (li) { if (!list) { html += "<ul>"; list = true; } html += `<li>${inline(li[1])}</li>`; }
    else if (l.startsWith("### ")) html += `<h5>${inline(l.slice(4))}</h5>`;
    else if (l.startsWith("## ")) html += `<h4>${inline(l.slice(3))}</h4>`;
    else if (l.startsWith("# ")) continue;
    else if (l) html += `<p>${inline(l)}</p>`;
  }
  return html + (list ? "</ul>" : "");
}

async function loadStorage(force = false) {
  const box = $("#storage-box");
  box.innerHTML = `<span class="muted">Calcolo in corso…</span>`;
  try {
    const d = await api(`/api/storage${force ? "?force=1" : ""}`);
    const tbl = (head, rows) => `<table class="tbl"><thead><tr>${head.map((h, i) => `<th${i ? ' style="text-align:right"' : ""}>${h}</th>`).join("")}</tr></thead>`
      + `<tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td${i ? ' style="text-align:right"' : ""}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    const n = (x) => x.toLocaleString("it");
    box.innerHTML = `<p>Cartella: <code>${esc(d.path)}</code> · <b>${esc(fmtSize(d.total))}</b></p>`
      + `<h5>Per cartella</h5>` + tbl(["Cartella", "File", "Peso"], d.folders.map((f) => [esc(f.name), n(f.files), esc(fmtSize(f.bytes))]))
      + `<h5>Per console</h5>` + tbl(["Console", "Media (immagini, video)", "Metadati nel database", "Elenco store online", "Totale"],
          d.platforms.map((p) => [esc(platName(p.id)), `${esc(fmtSize(p.media))} · ${n(p.files)} file`,
            `${esc(fmtSize(p.db))} · ${n(p.rows)} voci`, p.entries ? `${esc(fmtSize(p.catalog))} · ${n(p.entries)} elenchi` : "—", esc(fmtSize(p.total))]))
      + `<h5>Per fonte dei metadati</h5>` + tbl(["Fonte", "Voci", "Peso"], d.sources.map((s) => [esc(s.source), n(s.rows), esc(fmtSize(s.bytes))]))
      + `<h5>Store online e altri dati</h5>` + tbl(["Dati", "Voci", "Peso"], d.kv.map((k) => [esc(k.label), n(k.rows), esc(fmtSize(k.bytes))]))
      + `<h5>Database</h5>` + tbl(["File", "Peso"], Object.entries(d.files).map(([k, v]) => [esc(k), esc(fmtSize(v))]));
  } catch (e) { box.innerHTML = ""; toast(e.message, true); }
}

async function loadAbout() {
  try {
    const a = await api("/api/about");
    $("#about-ver").textContent = a.version;
    $("#about-data").textContent = `· dati in ${a.data_dir}`;
    $("#about-log").innerHTML = a.changelog ? miniMd(a.changelog) : `<p class="muted">Changelog non disponibile.</p>`;
  } catch (e) { toast(e.message, true); }
  try {
    const t = await api("/api/api-token");
    $("#api-token").value = t.token;
    $("#api-url").textContent = `Indirizzo ${t.url} · token in ${t.file}`;
  } catch { /* niente: la scheda Info funziona lo stesso */ }
  checkUpdate(false, true);
}

async function checkUpdate(force = false, show = false) {
  let u;
  try { u = await api(`/api/update${force ? "?force=1" : ""}`); } catch (e) { if (show) $("#about-update").textContent = e.message; return; }
  const txt = u.newer ? `È disponibile la versione ${u.latest} (installata: ${u.current}).`
    : u.error && !u.latest ? `Impossibile controllare: ${u.error}`
    : u.latest ? `Hai l'ultima versione (${u.current}).` : "Nessuna release pubblica su GitHub (repository privato o senza release): controllo non disponibile.";
  if (show) {
    $("#about-update").innerHTML = esc(txt) + (u.newer && u.url ? ` <a href="#" data-open="${esc(u.url)}">Scarica ↗</a>` : "")
      + (u.newer && u.install_kind ? ` <button class="btn primary" id="about-install">⬇ Installa e riavvia</button>` : "");
    if ($("#about-install")) $("#about-install").onclick = installUpdate;
  } else if (u.newer && store.get("updateSeen", "") !== u.latest) {
    store.set("updateSeen", u.latest); // un solo avviso per versione
    updatePopup(u);
  }
}

/** Piccolo pop-up cliccabile: installa la nuova versione (o apre la pagina di download se non si puo' installare dall'app). */
function updatePopup(u) {
  document.querySelector("#update-pop")?.remove();
  const p = document.createElement("button");
  p.id = "update-pop";
  p.type = "button";
  p.innerHTML = `<b>⬆ Nuova versione ${esc(u.latest)}</b><span>${u.install_kind ? "Clicca per scaricare e installare" : "Clicca per scaricarla"}</span><i title="Chiudi">✕</i>`;
  p.onclick = (e) => {
    p.remove();
    if (e.target.tagName === "I") return;
    if (!u.install_kind) return api("/api/open", { body: { url: u.url } });
    openSettings("about");
    installUpdate();
  };
  overlayHost().appendChild(p);
}

/** Scarica e installa la nuova versione (dal server); alla fine l'app si chiude e si riapre aggiornata. */
async function installUpdate() {
  const box = $("#about-update");
  let st;
  try { st = await api("/api/update/install", { body: {} }); } catch (e) { return toast(e.message, true); }
  while (st.status === "running") {
    const pct = st.total ? ` ${Math.floor((100 * st.done) / st.total)}%` : "";
    box.innerHTML = `${esc(st.phase)}${pct}<div class="loadbar-track"><i></i></div>`;
    await new Promise((r) => setTimeout(r, 700));
    try { st = await api("/api/update/install"); } catch { st = { status: "done", phase: "Riavvio con la nuova versione…" }; } // server gia' chiuso
  }
  box.textContent = st.phase;
  if (st.status === "error") toast(st.phase, true);
}

/* ---- salute */
async function loadHealth() {
  $("#health-list").innerHTML = `<p class="muted">Controllo in corso…</p>`;
  let d;
  try { d = await api("/api/health"); } catch (e) { $("#health-list").innerHTML = `<p class="status-bad">${esc(e.message)}</p>`; return; }
  const ico = { ok: "✓", warn: "⚠", error: "✖" };
  SET.health = d.items;
  $("#health-list").innerHTML = `<div class="health">` + d.items.map((x, i) => `<div class="hl-row hl-${x.level}">`
    + `<span class="hl-ico">${ico[x.level]}</span><div class="hl-txt"><b>${esc(x.title)}</b>${x.detail ? `<div class="muted small">${esc(x.detail)}</div>` : ""}</div>`
    + (x.fix ? `<button class="btn hl-fix" data-i="${i}">${esc(x.fix.label)}</button>` : "") + `</div>`).join("") + `</div>`;
}

async function healthFix(fix) {
  if (fix.tab) return showTab(fix.tab);
  if (fix.backup) { await backupNow(); return loadHealth(); }
  if (fix.sunshine) {
    try { await api("/api/sunshine/start", { body: {} }); } catch (e) { return toast(e.message, true); }
    toast("Avvio di Sunshine: conferma la richiesta di Windows");
    return setTimeout(loadHealth, 5000);
  }
  if (fix.scan) {
    try { await api("/api/scan", { body: { mode: fix.scan } }); } catch (e) { return toast(e.message, true); }
    SET.lastStatus = "running";
    setScanRunning(true);
    showTab("scan");
  }
}

/* ---- copie di sicurezza dei dati utente */
async function loadBackups() {
  let d;
  try { d = await api("/api/backups"); } catch (e) { return toast(e.message, true); }
  $("#backup-list").innerHTML = d.items.length
    ? `<table class="emu"><tr><th>Copia</th><th style="width:150px">Data</th><th style="width:80px" class="num">Dimensione</th><th style="width:110px"></th></tr>`
      + d.items.map((b) => `<tr><td class="small">${esc(b.name)}</td><td>${esc(fmtDate(b.time))}</td><td class="num">${esc(fmtSize(b.size))}</td>`
        + `<td><button class="btn bk-restore" data-name="${esc(b.name)}">Ripristina</button></td></tr>`).join("") + `</table>`
      + `<p class="muted small">Cartella: <code>${esc(d.folder)}</code></p>`
    : `<p class="muted small">Ancora nessuna copia.</p>`;
}

/* ---- profili utente: dati personali separati (preferiti, tempo di gioco, collezioni...), libreria comune */
async function loadProfile() {
  try { S.profiles = await api("/api/profiles"); } catch { return; }
  $("#profile-name").textContent = S.profiles.profiles.find((x) => x.id === S.profiles.active)?.name || "Principale";
}

async function switchProfile(id) {
  S.profiles = await api("/api/profiles/active", { body: { id } });
  await loadProfile();
  await reload(true);
  toast(`Profilo: ${$("#profile-name").textContent}`);
}

function profileMenu(anchor) {
  const st = S.profiles || { profiles: [], active: "default" };
  const cur = st.profiles.find((x) => x.id === st.active) || { name: "" };
  const p = $("#popover");
  p.innerHTML = `<div class="opts">${st.profiles.map((x) => `<div class="opt${x.id === st.active ? " hl" : ""}" data-pid="${esc(x.id)}">`
      + `<span>${x.id === st.active ? "✓ " : ""}${esc(x.name)}</span></div>`).join("")}
    <div class="opt" data-act="new">＋ Nuovo profilo…</div>
    <div class="opt" data-act="rename">✎ Rinomina «${esc(cur.name)}»…</div>
    ${st.active !== "default" ? `<div class="opt" data-act="delete">🗑 Elimina «${esc(cur.name)}»…</div>` : ""}</div>`;
  placePopover(anchor);
  p.onclick = async (e) => {
    const o = e.target.closest(".opt");
    if (!o) return;
    closePopover();
    try {
      if (o.dataset.pid) { if (o.dataset.pid !== st.active) await switchProfile(o.dataset.pid); }
      else if (o.dataset.act === "new") {
        const name = await askText(anchor, "Nome del profilo, es. Luca");
        if (name) await switchProfile((await api("/api/profiles", { body: { name } })).created.id);
      } else if (o.dataset.act === "rename") {
        const name = await askText(anchor, "Nuovo nome", cur.name);
        if (name) { await api(`/api/profiles/${encodeURIComponent(st.active)}`, { method: "PATCH", body: { name } }); await loadProfile(); }
      } else if (o.dataset.act === "delete" && confirm(`Eliminare il profilo «${cur.name}»? Preferiti, tempo di gioco e collezioni `
          + "di questo profilo spariscono dall'app (il database resta nella cartella profiles/eliminati).")) {
        await api(`/api/profiles/${encodeURIComponent(st.active)}`, { method: "DELETE" });
        await loadProfile();
        await reload(true);
        toast("Profilo eliminato");
      }
    } catch (err) { toast(err.message, true); }
  };
}

/* ---- sincronizzazione tra PC (cartella condivisa e rete locale) */
async function loadSync(st) {
  try { st = st || await api("/api/sync"); } catch (e) { $("#sync-last").textContent = e.message; return; }
  $("#sync-folder").value = st.folder;
  document.querySelectorAll('[name="sync-what"]').forEach((r) => { r.checked = r.value === st.what; });
  const rows = [...st.peers.map((p) => ({ name: p.name, sub: p.url, del: `peer:${p.id}`, res: st.last?.peers?.[p.id] })),
    ...st.pcs.map((d) => ({ name: d.name, sub: "si sincronizza con questo PC", del: `pc:${d.id}` }))];
  $("#sync-peers").innerHTML = rows.length ? `<table class="emu">${rows.map((r) => `<tr><td>💻 ${esc(r.name)} <span class="muted small">${esc(r.sub)}</span>`
    + (r.res ? ` <span class="small ${r.res.error ? "status-bad" : "status-ok"}">${esc(r.res.error || "✓ sincronizzato")}</span>` : "")
    + `</td><td style="width:100px"><button class="btn" data-sync-del="${esc(r.del)}">Scollega</button></td></tr>`).join("")}</table>` : "";
  const l = st.last || {};
  const folder = l.folder ? (l.folder.error ? ` · cartella: ${l.folder.error}` : ` · cartella ✓ (${l.folder.from} file di altri PC)`) : "";
  $("#sync-last").textContent = st.busy ? "Sincronizzazione in corso…"
    : l.time ? `Ultima sincronizzazione: ${new Date(l.time * 1000).toLocaleTimeString("it", { hour: "2-digit", minute: "2-digit" })}${folder}`
    : "Non ancora sincronizzato";
}

function bindSync() {
  const save = async (body) => { try { await loadSync(await api("/api/sync", { body })); toast("Salvato"); } catch (e) { toast(e.message, true); } };
  $("#sync-folder-save").onclick = () => save({ folder: $("#sync-folder").value.trim() });
  $("#sync-folder-pick").onclick = async () => { const p = window.pywebview?.api ? await pickPath("folder") : null; if (p) { $("#sync-folder").value = p; save({ folder: p }); } };
  document.querySelectorAll('[name="sync-what"]').forEach((r) => { r.onchange = () => save({ what: r.value }); });
  $("#sync-code").onclick = async () => {
    try {
      const r = await api("/api/sync/code", { body: {} });
      $("#sync-code-out").innerHTML = `Codice <b>${esc(r.code)}</b> (vale ${r.minutes} minuti, una volta) · indirizzo di questo PC: `
        + (r.urls.map((u) => `<code>${esc(u.replace("https://", ""))}</code>`).join(" o ") || "non trovato");
    } catch (e) { toast(e.message, true); }
  };
  $("#sync-peer-add").onclick = async () => {
    const btn = $("#sync-peer-add");
    btn.disabled = true;
    try {
      await loadSync(await api("/api/sync/peer", { body: { url: $("#sync-peer-url").value.trim(), code: $("#sync-peer-code").value.trim() } }));
      $("#sync-peer-code").value = "";
      toast("PC abbinato: la prima sincronizzazione è partita");
    } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
  };
  $("#sync-peers").onclick = async (e) => {
    const b = e.target.closest("[data-sync-del]");
    if (!b) return;
    const [kind, id] = b.dataset.syncDel.split(":");
    try { await loadSync(await api(kind === "peer" ? `/api/sync/peer/${id}` : `/api/sync/pcs/${id}`, { method: "DELETE" })); } catch (err) { toast(err.message, true); }
  };
  $("#sync-now").onclick = async () => {
    const btn = $("#sync-now");
    btn.disabled = true;
    $("#sync-last").textContent = "Sincronizzazione in corso…";
    try { await loadSync(await api("/api/sync/now", { body: {} })); await reload(true); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
  };
}

/** Cartella delle copie dei salvataggi dei giochi (formato Ludusavi). */
async function loadSaveDir(r) {
  try { r = r || await api("/api/app/save-dir"); } catch { return; }
  $("#save-dir").value = r.save_dir;
  $("#save-dir").placeholder = `Predefinita: ${r.default}`;
}

async function backupNow() {
  try { const r = await api("/api/backups", { body: {} }); toast(`Copia creata: ${r.name}`); } catch (e) { return toast(e.message, true); }
  loadBackups();
}

async function restoreBackup(name) {
  if (!confirm(`Ripristinare «${name}»?
I dati attuali vengono prima salvati in una nuova copia, quindi l'operazione si può annullare.`)) return;
  let r;
  try { r = await api("/api/backups/restore", { body: { name } }); } catch (e) { return toast(e.message, true); }
  toast(`Ripristinato (${r.restored.join(", ")}). Copia dei dati precedenti: ${r.safety_copy}`);
  await reload(true);
  loadBackups();
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
  else loadRenamed();
}

/* ---- altre cartelle ROM (altri dischi) */
function renderExtraRoots(extra) {
  $("#scan-extra").innerHTML = `<div class="muted small extra-roots">Altre cartelle ROM: `
    + (extra.length ? extra.map((x) => `<span class="xr${x.online ? "" : " off"}" title="${x.online ? "" : "Non raggiungibile: disco scollegato?"}">`
      + `<code>${esc(x.path)}</code>${x.online ? "" : " ⚠"} <a href="#" class="xr-del" data-path="${esc(x.path)}" title="Togli">✕</a></span>`).join(" ")
      : "nessuna")
    + ` <button class="btn" id="xr-add" type="button">＋ Aggiungi</button>`
    + ` <button class="btn" id="xr-paste" type="button" title="Incolla dagli appunti una cartella con giochi PC (.exe)">📋 Incolla cartella PC</button></div>`;
}

async function extraRoot(action, path) {
  if (!path) return;
  try { await api("/api/roms/extra", { body: { action, path } }); } catch (e) { return toast(e.message, true); }
  toast(action === "add" ? "Cartella aggiunta: aggiorna la libreria o cerca i nuovi giochi" : "Cartella tolta");
  loadScan();
}

/* ---- cartelle rinominate o spostate: i dati del gioco (metadati, tempo di gioco, collezioni) passano alla nuova cartella */
async function loadRenamed() {
  const box = $("#scan-renamed");
  box.innerHTML = `<p class="muted small">Controllo delle cartelle rinominate…</p>`;
  let d;
  try { d = await api("/api/relink/plan"); } catch { box.innerHTML = ""; return; }
  if (!d.pairs.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="renamed"><b>⟲ ${d.pairs.length} giochi sembrano rinominati o spostati</b>
    <div class="muted small">I loro dati (metadati, tempo di gioco, preferiti, collezioni) possono passare alla nuova cartella invece di ripartire da zero.
    Prima dell'operazione si salva una copia dei database (*.bak).</div>
    <div class="rn-list">${d.pairs.map((x) => `<label><input type="checkbox" class="rn-sel" data-new="${esc(x.new)}" checked>
      <span>${esc(x.title)}</span> <span class="muted small">→ ${esc(x.path.split(/[\\/]/).slice(-2).join("/"))}</span></label>`).join("")}</div>
    <button class="btn primary" id="relink-apply">Riassocia i selezionati</button></div>`;
  $("#relink-apply").onclick = async () => {
    const sel = [...document.querySelectorAll(".rn-sel:checked")].map((c) => c.dataset.new);
    if (!sel.length) return;
    try { const r = await api("/api/relink/apply", { body: { new: sel } }); toast(`${r.applied} giochi riassociati: aggiorno la libreria…`); }
    catch (e) { return toast(e.message, true); }
    box.innerHTML = "";
    const tick = async () => {
      const st = await api("/api/status");
      if (st.building) return setTimeout(tick, 1000);
      await reload(true);
      loadScan();
    };
    tick();
  };
}

function renderScan(pv, js) {
  if (pv.error) { $("#scan-table").innerHTML = `<p class="status-bad">${esc(pv.error)}</p>`; return; }
  $("#scan-root").textContent = pv.root;
  renderExtraRoots(pv.extra || []);
  const fmt = (n) => (n ? n.toLocaleString("it") : "—");
  $("#scan-table").innerHTML = `<table class="emu"><tr><th style="width:28px"><input type="checkbox" id="scan-all" title="Seleziona tutte"></th>`
    + `<th>Console</th><th>Cartella</th><th class="num">Giochi</th><th class="num">Nuovi</th>`
    + `<th class="num" title="Giochi che nessuna fonte ha riconosciuto">Senza dati</th>`
    + `<th class="num" title="Giochi riconosciuti a cui manca qualcosa (descrizione, immagini...)">Incompleti</th>`
    + `<th title="Incolla dagli appunti la cartella di questa console: i giochi nuovi vengono cercati subito"></th></tr>`
    + pv.platforms.map((r) => `<tr class="${r.new ? "new" : ""}"><td><input type="checkbox" class="scan-sel" data-p="${esc(r.platform)}"></td>`
      + `<td>${esc(r.name)}</td><td class="muted small">${esc(r.folder)}</td><td class="num">${r.games.toLocaleString("it")}</td>`
      + `<td class="num n">${fmt(r.new)}</td><td class="num warn">${fmt(r.nodata)}</td><td class="num">${fmt(r.partial)}</td>`
      + `<td><button class="btn paste-dir" data-platform="${esc(r.platform)}" title="Incolla una cartella di ${esc(r.name)} (dagli appunti)">📋</button></td></tr>`).join("") + `</table>`;
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
  // scansione finita: i file trovati compaiono subito, i metadati arrivano dopo senza bloccare
  if (js.status === "running" && js.phase === "Scraping" && SET.shownFor !== js.started) { SET.shownFor = js.started; reload(true); }
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
  api("/api/app/launch").then((l) => {
    $("#ln-before").value = l.before || ""; $("#ln-after").value = l.after || ""; $("#ln-close").checked = !!l.close_launcher;
  }).catch(() => {});
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
    if (a.dataset.view === "ports") PORTS_F.open = null; // dalla barra laterale: sempre il catalogo
    setView({ type: a.dataset.view, id: a.dataset.id || null, plat: a.dataset.plat || undefined });
  });
  $("#inst-filter").value = S.instFilter;
  // altri store PC: aggiunta, rimozione, categoria e filtro
  $("#pcst-add").onclick = () => {
    const url = $("#pcst-url").value.trim();
    if (!/^https?:\/\//i.test(url)) return toast("Indirizzo non valido (serve http:// o https://)", true);
    if (PCST.rows.some((r) => r.url === url)) return toast("Questo elenco è già stato aggiunto");
    savePcStores([...PCST.rows, { url, kind: $("#pcst-kind").value }], false);
    $("#pcst-url").value = "";
  };
  // anteprima: legge solo la prima pagina e dice quanti giochi/pagine ci sono
  $("#pcst-probe").onclick = async () => {
    const url = $("#pcst-url").value.trim();
    if (!/^https?:\/\//i.test(url)) return toast("Indirizzo non valido (serve http:// o https://)", true);
    const out = $("#pcst-probe-out");
    out.textContent = "Lettura in corso…";
    let r;
    try { r = await api("/api/pc-stores/probe", { body: { url } }); } catch (e) { out.textContent = ""; return toast(e.message, true); }
    const many = r.pages > 1 ? `su ${r.pages.toLocaleString("it")} pagine` : "su una sola pagina";
    const ex = r.samples.length ? ` Esempi: ${r.samples.slice(0, 3).join(" · ")}` : "";
    out.textContent = `${r.games ? "✅" : "⚠️"} Formato ${r.kind}: ${r.games.toLocaleString("it")} giochi in questa pagina, ${many}.${ex}`;
  };
  // aggiornamento di tutti gli store: modalità (avvio / orario / manuale), parallelismo, avvio a mano
  $("#srf-mode").onchange = () => { $("#srf-time").disabled = $("#srf-mode").value !== "daily"; };
  $("#srf-save").onclick = async () => {
    const body = { mode: $("#srf-mode").value, time: $("#srf-time").value, workers: Number($("#srf-workers").value) || 4 };
    try { renderStoreRefresh(await api("/api/store-refresh", { body })); } catch (e) { return toast(e.message, true); }
    toast("Quando aggiornare gli store: salvato");
  };
  $("#srf-run").onclick = async () => {
    try { await api("/api/store-refresh/run", { body: {} }); } catch (e) { return toast(e.message, true); }
    pollStoreRefresh();
  };
  $("#srf-stop").onclick = async () => {
    try { await api("/api/store-refresh/stop", { body: {} }); } catch (e) { return toast(e.message, true); }
    pollStoreRefresh();
  };
  // log e aggiornamento del singolo store (row della lista)
  $("#srf-progress").addEventListener("click", async (e) => {
    const logBtn = e.target.closest("[data-srf-log]");
    if (logBtn) {
      if (!srfLast) return;
      const id = logBtn.dataset.srfLog;
      if (srfOpen.has(id)) srfOpen.delete(id); else srfOpen.add(id);
      renderStoreRefreshJob(srfLast.job || { status: "idle" }, srfLast.list);
      return;
    }
    const runBtn = e.target.closest("[data-srf-run]");
    if (!runBtn) return;
    try { await api("/api/store-refresh/run", { body: { ids: [runBtn.dataset.srfRun] } }); }
    catch (err) { return toast(err.message, true); }
    pollStoreRefresh();
  });
  // integrazioni: notifiche di Windows
  $("#nt-on").onchange = async (e) => {
    try { await api("/api/app/notify", { body: { enabled: e.target.checked } }); toast(e.target.checked ? "Notifiche attive" : "Notifiche spente"); }
    catch (err) { toast(err.message, true); }
  };
  // a coda vuota: sospendi o spegni
  $("#pw-after").onchange = async (e) => {
    try { await api("/api/app/power", { body: { after_jobs: e.target.value } }); loadPower(); } catch (err) { toast(err.message, true); }
  };
  $("#pw-abort").onclick = async () => {
    try { await api("/api/app/power/abort", { body: {} }); loadPower(); toast("Annullato"); } catch (err) { toast(err.message, true); }
  };
  // integrazioni: presenza su Discord
  $("#dc-save").onclick = async () => {
    const body = { enabled: $("#dc-on").checked, client_id: $("#dc-id").value.trim(), show_time: $("#dc-time").checked };
    let d;
    try { d = await api("/api/integrations/discord", { body }); } catch (e) { return toast(e.message, true); }
    $("#dc-state").textContent = "";
    renderIntegrations({ discord: d.discord });
    toast(body.enabled ? "Presenza su Discord aggiornata" : "Presenza su Discord spenta");
  };
  $("#pcst-refresh").onclick = async () => {
    try { await api("/api/pc-stores/refresh", { body: {} }); } catch (e) { return toast(e.message, true); }
    pollStoreRefresh(); // la coda unica mostra l'avanzamento nel blocco «Aggiornamento degli store»
    pollPcStores();
  };
  $("#pcst-meta").onclick = async () => {
    try { await api("/api/catalogs/windows/netmeta", { body: {} }); } catch (e) { return toast(e.message, true); }
    toast("Copertine degli store PC in corso: compaiono un po' alla volta (avanzamento in Attività)");
  };
  $("#pcst-filter").addEventListener("click", (e) => {
    const b = e.target.closest("[data-pcst-filter]");
    if (!b) return;
    PCST.filter = b.dataset.pcstFilter;
    document.querySelectorAll("#pcst-filter .btn").forEach((x) => x.classList.toggle("on", x === b));
    renderPcStores(null);
  });
  $("#pcst-table").addEventListener("click", (e) => {
    const del = e.target.closest("[data-pcst-del]");
    if (del) savePcStores(PCST.rows.filter((r) => r.url !== del.dataset.pcstDel), true);
  });
  $("#pcst-table").addEventListener("change", (e) => {
    const s = e.target.closest("[data-pcst-kind]");
    if (!s) return;
    savePcStores(PCST.rows.map((r) => (r.url === s.dataset.pcstKind ? { ...r, kind: s.value } : r)), false);
  });
  $("#acc-table").addEventListener("click", (e) => {
    if (e.target.closest("[data-ports-open]")) { $("#settings").close(); setView({ type: "ports", id: null }); }
  });
  $("#inst-filter").onchange = (e) => { S.instFilter = e.target.value; store.set("instFilter", S.instFilter); refreshGrid(); };
  $("#pc-add").onclick = () => openAddGame();
  $("#bp-btn").onclick = () => enterBigPicture();
  $("#random-btn").onclick = () => {
    // tra quelli mostrati; meglio se si possono avviare subito (niente da scaricare o installare)
    const ready = S.items.filter((g) => !g.store || (g.store !== "online" && g.installed));
    const pool = ready.length ? ready : S.items;
    if (!pool.length) return toast("Nessun gioco in questa vista");
    openDetail(pool[Math.floor(Math.random() * pool.length)].id);
  };
  GS.on("uiChanged", () => { if (S.games.length) { renderSidebar(); } });
  layer.addEventListener("click", (e) => { const t = e.target.closest(".tile"); if (t) openDetail(+t.dataset.id); });
  layer.addEventListener("dblclick", (e) => { const t = e.target.closest(".tile"); if (t) play(+t.dataset.id); });
  $("#platform-list").addEventListener("contextmenu", (e) => {
    const a = e.target.closest("[data-view=platform]");
    if (!a) return;
    e.preventDefault();
    openPlatCtx(e, a.dataset.id);
  });
  layer.addEventListener("contextmenu", (e) => {
    const t = e.target.closest(".tile");
    const g = t && S.byId.get(+t.dataset.id);
    if (!g) return;
    e.preventDefault();
    openCtx(e, g);
  });
  document.addEventListener("mousedown", (e) => { if (!e.target.closest("#ctxmenu")) closeCtx(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCtx(); });
  scroller.addEventListener("scroll", () => closeCtx(), { passive: true });
  window.addEventListener("blur", closeCtx);
  window.addEventListener("focus", syncLibrary);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) syncLibrary(); });
  setInterval(syncLibrary, 3000); // libreria ricostruita da un lavoro in sottofondo: compare entro 3 s
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
    if (e.target.id === "plat-scan") {
      try {
        if (S.view.type === "store") {
          await api("/api/stores/refresh", { body: { only: S.view.id === "all" ? null : [S.view.id] } });
          toast("Cerco i giochi nuovi…");
          pollStores(true);
        } else {
          await api("/api/scan", { body: { platforms: [S.view.id], mode: "new" } });
          toast("Cerco i giochi nuovi: compaiono subito, i dati arrivano dopo");
          SET.lastStatus = "running"; setScanRunning(true); pollScan();
        }
      } catch (err) { toast(err.message, true); }
      return;
    }
    if (e.target.id === "top-open") { setView({ type: "top", id: S.view.id }); return; }
    if (e.target.id === "all-local") { S.allLocal = !S.allLocal; store.set("allLocal", S.allLocal); setView(S.view); return; }
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

  $("#view-actions").addEventListener("change", async (e) => {
    if (e.target.id !== "hide-dlc") return;
    try {
      const r = await api("/api/app/hide-dlc", { body: { platform: S.view.id, hide: e.target.checked } });
      S.hideDlc = new Set(r.hide_dlc);
    } catch (err) { e.target.checked = !e.target.checked; return toast(err.message, true); }
    refreshGrid();
    toast(e.target.checked ? `DLC di ${platName(S.view.id)} nascoste` : `DLC di ${platName(S.view.id)} di nuovo visibili`);
  });

  $("#detail-close").onclick = closeDetail;
  $("#md-q").oninput = googleImages;
  $("#md-q").onkeydown = (e) => { if (e.key === "Enter") $("#md-google").click(); };
  $("#md-use").onclick = () => useMedia($("#md-url").value.trim());
  $("#md-paste").onclick = async () => {
    let url = "";
    try { url = (await navigator.clipboard.readText() || "").trim(); } catch { /* permesso negato: si incolla a mano */ }
    if (url) $("#md-url").value = url;
    useMedia(url || $("#md-url").value.trim());
  };
  // la nota si salva quando si esce dal campo (change), senza ridisegnare la scheda
  $("#detail").addEventListener("change", async (e) => {
    const d = S.detail;
    if (e.target.id !== "d-note" || !d) return;
    const v = e.target.value.trim();
    try { await api(`/api/game/${d.id}/flag`, { body: { field: "note", value: v } }); } catch (err) { return toast(err.message, true); }
    d.user.note = v;
    toast(v ? "Nota salvata" : "Nota cancellata");
  });
  $("#detail").addEventListener("click", async (e) => {
    const d = S.detail;
    if (!d) return;
    const t = e.target;
    if (t.id === "d-play") play(d.id);
    else if (t.id === "d-install") installGame(d.id);
    else if (t.id === "d-torrent") api(`/api/game/${d.id}/torrent`, { body: {} }).then(() => toast("Link aperto nel client torrent"))
      .catch((err) => toast(err.message, true));
    else if (t.dataset.install) installGame(+t.dataset.install);
    else if (t.dataset.port) { closeDetail(); PORTS_F.open = t.dataset.port; setView({ type: "ports" }); }
    else if (t.id === "d-page") api(`/api/game/${d.id}/store-page`, { body: {} }).catch((err) => toast(err.message, true));
    else if (t.id === "d-manual-del") {
      try { await removeManual(d); } catch (err) { toast(err.message, true); }
    } else if (t.dataset.ga) {
      const a = GS.ui.gameActions.find((x) => x.id === t.dataset.ga);
      try { await a.run(d, GS); } catch (err) { toast(err.message, true); }
    }
    else if (t.id === "d-fav") setFlag("favorite", !d.user.favorite);
    else if (t.dataset.status) setFlag("status", d.user.status === t.dataset.status ? "" : t.dataset.status);
    else if (t.dataset.myrate) {
      const n = +t.dataset.myrate === d.user.my_rating ? 0 : +t.dataset.myrate; // stessa stella = togli il voto
      try { await api(`/api/game/${d.id}/flag`, { body: { field: "my_rating", value: n } }); } catch (err) { return toast(err.message, true); }
      d.user.my_rating = n;
      document.querySelectorAll("#detail .star").forEach((b) => b.classList.toggle("on", +b.dataset.myrate <= n));
    }
    else if (t.dataset.open) { e.preventDefault(); api("/api/open", { body: { url: t.dataset.open } }); }
    else if (t.id === "d-hide") setFlag("hidden", !d.user.hidden);
    else if (t.id === "d-folder") api(`/api/game/${d.id}/open-folder`, { body: {} }).catch((err) => toast(err.message, true));
    else if (t.id === "d-emu-cfg") { e.preventDefault(); openSettings("emu"); }
    else if (t.id === "d-profile") { e.preventDefault(); openProfile(d); }
    else if (t.id === "d-shortcut") {
      e.preventDefault();
      api(`/api/game/${d.id}/shortcut`, { body: {} }).then(() => toast(`Collegamento «${d.title}» creato sul desktop`), (err) => toast(err.message, true));
    }
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
  $("#backup-now").onclick = backupNow;
  $("#save-dir-pick").onclick = async () => { const p = await pickPath("folder"); if (p) $("#save-dir").value = p; };
  $("#save-dir-save").onclick = () => api("/api/app/save-dir", { body: { save_dir: $("#save-dir").value } })
    .then((r) => { loadSaveDir(r); toast("Cartella dei salvataggi salvata"); }, (e) => toast(e.message, true));
  bindSync();
  $("#profile-btn").onclick = (e) => profileMenu(e.currentTarget);
  $("#ra-save").onclick = async () => {
    const body = { username: $("#ra-user").value };
    if ($("#ra-key").value.trim()) body.key = $("#ra-key").value.trim();
    try { await api("/api/ra", { body }); toast("RetroAchievements collegato"); $("#ra-key").value = ""; loadAccounts(); }
    catch (e) { toast(e.message, true); }
  };
  $('[data-pane="acc"]').addEventListener("click", (e) => {
    const o = e.target.closest("[data-open]");
    if (o) { e.preventDefault(); api("/api/open", { body: { url: o.dataset.open } }); }
  });
  $("#xf-pick").onclick = async () => { const p = window.pywebview?.api ? await pickPath("folder") : null; if (p) $("#xf-out").value = p; };
  // la pagina web ha un titolo proprio: il campo compare solo per quel formato
  $("#xf-format").onchange = () => { $("#xf-title-row").hidden = $("#xf-format").value !== "html"; };
  $("#xf-title-row").hidden = $("#xf-format").value !== "html";
  $("#xf-go").onclick = async (e) => {
    e.target.disabled = true;
    $("#xf-info").textContent = "Esportazione in corso (copia anche le immagini, può richiedere qualche minuto)…";
    try {
      const r = await api("/api/export/frontend", {
        body: { format: $("#xf-format").value, out_dir: $("#xf-out").value, title: $("#xf-title").value.trim() },
      });
      $("#xf-info").textContent = `Esportati ${r.games.toLocaleString("it")} giochi di ${Object.keys(r.summary).length} console in ${r.out_dir}`
        + (r.index ? ` · apri ${r.index}` : "");
    } catch (err) { $("#xf-info").textContent = ""; toast(err.message, true); }
    e.target.disabled = false;
  };
  $("#health-reload").onclick = loadHealth;
  $("#about-check").onclick = () => checkUpdate(true, true);
  $("#storage-go").onclick = () => loadStorage(true);
  $('[data-pane="about"]').addEventListener("click", (e) => {
    const o = e.target.closest("[data-open]");
    if (o) { e.preventDefault(); api("/api/open", { body: { url: o.dataset.open } }); }
  });
  $("#api-token-copy").onclick = async () => {
    try { await navigator.clipboard.writeText($("#api-token").value); toast("Token copiato"); }
    catch { toast("Copia non riuscita: selezionalo e copialo a mano", true); }
  };
  $("#api-token-new").onclick = async () => {
    if (!confirm("Cambiare il token? Script, Stream Deck e webhook che usano quello vecchio smettono di funzionare.")) return;
    try { const t = await api("/api/api-token", { body: {} }); $("#api-token").value = t.token; toast("Nuovo token creato"); }
    catch (e) { toast(e.message, true); }
  };
  checkUpdate(false, false); // subito all'avvio, in parallelo al caricamento dei giochi (la risposta resta in cache 12 ore)
  $("#health-list").addEventListener("click", (e) => { const b = e.target.closest(".hl-fix"); if (b) healthFix(SET.health[+b.dataset.i].fix); });
  $("#backup-list").addEventListener("click", (e) => { const b = e.target.closest(".bk-restore"); if (b) restoreBackup(b.dataset.name); });
  $("#tabs").addEventListener("click", (e) => { const b = e.target.closest(".tab"); if (b) { showTab(b.dataset.tab); fitSettings(); } });
  initSettingsLayout();

  // scraping
  $("#scan-start").onclick = startScan;
  $("#scan-cancel").onclick = () => api("/api/scan/cancel", { body: {} }).then(() => toast("Interruzione in corso…"));
  $("#scan-reload").onclick = loadScan;
  $("#scan-mode").onchange = applyScanMode;
  $("#scan-table").addEventListener("change", (e) => {
    if (e.target.id === "scan-all") document.querySelectorAll(".scan-sel").forEach((c) => { c.checked = e.target.checked; });
    if (e.target.id === "scan-all" || e.target.classList?.contains("scan-sel")) applyScanMode();
  });
  $("#scan-table").addEventListener("click", (e) => {
    const b = e.target.closest(".paste-dir");
    if (b) pasteFolder(b.dataset.platform);
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
  $("#cat-meta-btn").onclick = async () => {
    if (!SET.catPid) return;
    try {
      const running = SET.metaRunning;
      await api(`/api/catalogs/${SET.catPid}/netmeta${running ? "/stop" : ""}`, { body: {} });
    } catch (e) { return toast(e.message, true); }
    pollMeta();
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
  $("#scan-extra").addEventListener("click", async (e) => {
    const del = e.target.closest(".xr-del");
    if (del) { e.preventDefault(); return extraRoot("remove", del.dataset.path); }
    if (e.target.id === "xr-add") {
      const p = window.pywebview?.api ? await pickPath("folder") : prompt("Percorso di un'altra cartella ROM (anche una sola console, es. D:\\Switch)");
      extraRoot("add", p);
    }
    if (e.target.id === "xr-paste") pasteFolder("windows");
  });
  document.querySelector('[data-tab="src"]')?.addEventListener("click", () => { pollHltb(); pollCollart(); });
  $("#collart-missing").onclick = () => startCollart("missing");
  $("#collart-all").onclick = () => startCollart("all");
  $("#collart-stop").onclick = () => api("/api/collart/cancel", { body: {} });
  $("#smeta-all").onclick = () => startStoreMeta(false);
  $("#ph-on").onchange = (e) => phoneSet({ enabled: e.target.checked });
  $('[data-pane="remote"]').addEventListener("change", async (e) => {
    const t = e.target;
    if (t.id === "ph-mouse") phoneSet({ mouse: t.checked });
    if (t.id === "ph-ca-ok") phoneSet({ ca_installed: t.checked });
    if (t.id === "ph-touch") phoneSet({ touchscreen: t.checked });
    else if (t.id === "ph-public") phoneSet({ public_url: t.value });
    else if (t.id === "ph-video") phoneSet({ video_addr: t.value });
    else if (t.id === "ph-touch-idle") { const v = Math.min(600, Math.max(5, Number(t.value) || 30)); phoneSet({ screen_idle: v }); }
    else if (t.id === "ph-mic") phoneSet({ mic_device: t.value });
    else if (t.dataset.swap && t.value !== "") {
      try { await api("/api/phone/swap", { body: { a: +t.dataset.swap, b: +t.value } }); } catch (err) { toast(err.message, true); }
      loadRemote();
    }
  });
  $('[data-pane="remote"]').addEventListener("click", async (e) => {
    const t = e.target;
    const rb = t.closest("[data-pad-rumble]");
    if (rb) {
      try {
        await api("/api/phone/pads/rumble", { body: { slot: +rb.dataset.padRumble } });
        toast("📳 Vibrazione inviata al telefono");
      } catch (err) { toast(err.message, true); }
      return;
    }
    const o = t.closest("[data-open]");
    if (o) { e.preventDefault(); api("/api/open", { body: { url: o.dataset.open } }); return; }
    if (t.dataset.es) {
      t.disabled = true;
      try {
        const r = await api("/api/phone/emusetup", { body: { what: t.dataset.es, players: +$("#es-n").value || 1 } });
        toast("Fatto: " + r.note);
      } catch (err) { toast(err.message, true); }
      t.disabled = false;
      return;
    }
    if (t.dataset.esundo) {
      e.preventDefault();
      if (!confirm("Rimettere le impostazioni dell'emulatore com'erano prima dell'ultima configurazione di Pozzo Launcher?")) return;
      try { await api("/api/phone/emusetup", { body: { what: t.dataset.esundo, restore: true } }); toast("Impostazioni rimesse"); } catch (err) { toast(err.message, true); }
      return;
    }
    if (t.id === "ph-newcode") { e.preventDefault(); await api("/api/phone/code", { body: {} }); loadRemote(); }
    else if (t.id === "ph-invite") {
      e.preventDefault();
      t.disabled = true;
      try { await api("/api/phone/invite", { body: {} }); } catch (err) { toast(err.message, true); }
      loadRemote();
    }
    else if (t.dataset.revoke) {
      if (!confirm("Revocare questo telefono? Dovrà essere abbinato di nuovo.")) return;
      await api(`/api/phone/devices/${t.dataset.revoke}`, { method: "DELETE" }); loadRemote();
    } else if (t.id === "sun-start") {
      try { await api("/api/sunshine/start", { body: {} }); toast("Avvio di Sunshine…"); setTimeout(loadRemote, 4000); } catch (err) { toast(err.message, true); }
    } else if (t.id === "sun-pub") {
      t.disabled = true; t.textContent = "Pubblicazione…";
      try {
        await api("/api/sunshine", { body: { user: $("#sun-user").value, password: $("#sun-pass").value, count: $("#sun-count").value, publish: true } });
        toast("Pubblicato: apri Moonlight e aggiorna l'elenco del PC");
      } catch (err) { toast(err.message, true); }
      loadRemote();
    }
  });
  $("#prof-dlg").addEventListener("input", pfRefresh);
  $("#prof-dlg").addEventListener("change", pfRefresh);
  $("#pf-cancel").onclick = () => $("#prof-dlg").close();
  $("#pf-save").onclick = () => saveProfile(false);
  $("#pf-reset").onclick = () => saveProfile(true);
  $("#pf-exe-pick").onclick = async () => { const p = await pickPath("file"); if (p) { $("#pf-exe").value = p; pfRefresh(); } };
  $("#pf-comp-pick").onclick = async () => { const p = await pickPath("file"); if (p) $("#pf-comp-exe").value = p; };
  $("#running").addEventListener("click", async (e) => {
    if (e.target.closest("#run-shot")) api("/api/running/screenshot", { body: {} });
    if (!e.target.closest("#run-stop")) return;
    if (!confirm(`Chiudere «${S.running?.title || "il gioco"}»? I progressi non salvati vanno persi.`)) return;
    try {
      const r = await api("/api/running/stop", { body: {} });
      toast(r.found ? "Gioco chiuso" : "Gioco non trovato: esecuzione interrotta nel launcher", !r.found);
    } catch (err) { toast(err.message, true); }
    pollRunning();
  });
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
  setTimeout(() => api("/api/free").then((d) => { if (d.first) d.new = 0; freeBadge(d); }).catch(() => {}), 4000);
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
      agTitleFromExe();
    }
  };
  $("#ag-exe").onchange = agTitleFromExe;  // anche se il percorso viene incollato a mano
  $("#ag-title").oninput = () => { $("#ag-title").dataset.auto = ""; };
  $("#ag-save").onclick = saveAddGame;

  // emulatori
  $("#emu-save").onclick = () => saveEmu();
  $("#ln-save").onclick = () => api("/api/app/launch", { body: { before: $("#ln-before").value, after: $("#ln-after").value,
    close_launcher: $("#ln-close").checked } }).then(() => toast("Impostazioni di avvio salvate"), (e) => toast(e.message, true));
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
GS.redraw = () => { if (S.games.length) refreshGrid(true); };  // ridisegna le copertine (badge di un'estensione arrivati dopo)

(async function main() {
  bind();
  loading("Caricamento dei tuoi giochi…");
  await loadExtensions(); // prima della libreria: l'attesa si vede gia' col tema scelto
  loadProfile();
  try {
    await SETUP.open();  // primo avvio: configurazione guidata (non fa nulla se gia' configurato)
    if (!(await loadLibrary())) {
      const st = await api("/api/status");
      if (!st.has_library) { await waitFirstBuild(); await loadLibrary(); }
    }
  } catch (e) {
    loading(null);
    toast("Errore nel caricamento della libreria: " + e.message, true);
    return;
  }
  if (S.view.type === "collection" && !S.collBySlug.has(S.view.id)) S.view = { type: "all" };
  if (S.view.type === "platform" && !S.plats.has(S.view.id)) S.view = { type: "all" };
  if (S.view.type === "plugin" && !GS.ui.views.has(S.view.id)) S.view = { type: "all" };
  renderSidebar();
  setView(S.view);
  pollRunning();
  pollScan(); // se uno scraping era gia' in corso, ne mostra lo stato
})();
