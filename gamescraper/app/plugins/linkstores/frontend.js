// Estensione «Link → Store»: legge una pagina che raccoglie altri siti, raggruppa i link per
// intestazione (h2/h3) e permette di aggiungere ciascuno come store di una console o come store PC.
//
// Note di progetto: la pagina si legge una sola volta (una richiesta, lato server); qui si mostra
// solo l'elenco, quindi il pulsante «Aggiungi» non scarica niente: scrive settings.json.

const MAX_ROWS = 400; // oltre questa soglia si mostra un avviso e si invita a filtrare

export default function (gs) {
  gs.addSidebarItem({ id: "linkstores", label: "Link → Store", icon: "🔗", view: "linkstores" });

  const esc = gs.esc;
  let state = { platforms: [], catalogs: {}, pc_stores: [] };
  let scan = null; // ultima lettura
  let url = "";
  let query = "";
  let externalOnly = true;
  let showAll = false;
  const touched = new Map(); // console -> Set di indirizzi aggiunti in questa sessione
  let touchedPc = false;

  const platName = (pid) => state.platforms.find((p) => p.id === pid)?.name || pid;

  async function loadState() {
    try {
      state = await gs.api("/plugin-api/linkstores/state");
    } catch (e) {
      gs.toast(`Store non leggibili: ${e.message}`, true);
    }
  }

  /** Dove è già configurato un indirizzo (null se non lo è). */
  function already(target) {
    if ((state.pc_stores || []).some((s) => s.url === target)) return "store PC";
    for (const [pid, urls] of Object.entries(state.catalogs || {})) {
      if ((urls || []).includes(target)) return platName(pid);
    }
    return null;
  }

  const platOptions = () => state.platforms
    .map((p) => `<option value="${esc(p.id)}">${p.pc ? "💻 " : ""}${esc(p.name)}</option>`).join("");

  function rowHtml(link) {
    const there = already(link.url);
    return `<div class="ls-row" data-url="${esc(link.url)}">
      <div class="ls-text"><a href="${esc(link.url)}" target="_blank" rel="noreferrer">${esc(link.text)}</a>
        <span class="muted small">${esc(link.host)}</span></div>
      <div class="ls-ctl">${there ? `<span class="ls-ok">✓ ${esc(there)}</span>` : `
        <select class="field ls-plat">${platOptions()}</select>
        <select class="field ls-kind"><option value="repack">📦 Repack</option><option value="direct">⬇ Diretto</option></select>
        <button class="btn" data-ls-add>Aggiungi</button>`}</div></div>`;
  }

  /** La categoria (repack/direct) ha senso solo per gli store PC. */
  function syncKind(row) {
    const kind = row.querySelector(".ls-kind");
    const plat = row.querySelector(".ls-plat");
    if (kind && plat) kind.hidden = plat.value !== "windows";
  }

  function updateTools(el) {
    const read = el.querySelector("#ls-read");
    const n = [...touched.values()].reduce((a, s) => a + s.size, 0) + (touchedPc ? 1 : 0);
    if (read) {
      read.hidden = !n;
      read.textContent = `↻ Leggi gli store aggiunti (${n})`;
    }
  }

  async function addLink(row, el) {
    const link = row.dataset.url;
    const platform = row.querySelector(".ls-plat").value;
    const kind = row.querySelector(".ls-kind").value;
    let r;
    try {
      r = await gs.api("/plugin-api/linkstores/add", { body: { url: link, platform, kind } });
    } catch (e) {
      return gs.toast(e.message, true);
    }
    state = r.state || state;
    const dest = r.platform === "windows" ? `store PC · ${r.kind}` : platName(r.platform);
    if (r.added) {
      if (r.platform === "windows") touchedPc = true;
      else {
        if (!touched.has(r.platform)) touched.set(r.platform, new Set());
        touched.get(r.platform).add(link);
      }
    }
    row.querySelector(".ls-ctl").innerHTML = `<span class="ls-ok">✓ ${esc(dest)}</span>`;
    updateTools(el);
    gs.toast(r.added ? `Aggiunto come ${dest}` : `Era già configurato come ${dest}`);
  }

  /** Avvia la lettura degli elenchi appena aggiunti (stessi endpoint di Impostazioni › Store). */
  async function readNow(el) {
    let started = 0;
    for (const [pid, urls] of touched) {
      const all = state.catalogs?.[pid] || [...urls];
      try {
        await gs.api(`/api/catalogs/${pid}`, { body: { urls: all, refresh: true } });
        started++;
      } catch (e) {
        gs.toast(`${platName(pid)}: ${e.message}`, true);
      }
    }
    if (touchedPc && state.pc_stores?.length) {
      try {
        await gs.api("/api/pc-stores", { body: { stores: state.pc_stores, refresh: true } });
        started++;
      } catch (e) {
        gs.toast(`Store PC: ${e.message}`, true);
      }
    }
    touched.clear();
    touchedPc = false;
    updateTools(el);
    if (started) gs.toast("Lettura avviata: l'avanzamento è in Impostazioni › Store");
  }

  function renderOut(el) {
    const out = el.querySelector("#ls-out");
    const count = el.querySelector("#ls-count");
    if (!scan) {
      out.innerHTML = `<p class="muted">Incolla l'indirizzo di una pagina che raccoglie altri siti e premi «Leggi i link».</p>`;
      count.textContent = "";
      return;
    }
    const q = query.trim().toLowerCase();
    const match = (l) => !q || l.text.toLowerCase().includes(q) || l.url.toLowerCase().includes(q);
    const groups = scan.groups.map((g) => ({ ...g, links: g.links.filter(match) })).filter((g) => g.links.length);
    const total = groups.reduce((a, g) => a + g.links.length, 0);
    let shown = 0;
    const html = groups.map((g) => {
      const links = showAll ? g.links : g.links.slice(0, Math.max(0, MAX_ROWS - shown));
      shown += links.length;
      if (!links.length) return "";
      return `<div class="ls-group"><h3 class="ls-h">${esc(g.name)}
        <span class="muted small">${g.links.length}</span></h3>${links.map(rowHtml).join("")}</div>`;
    }).join("");
    out.innerHTML = html || `<p class="muted">Nessun link corrisponde al filtro.</p>`;
    if (!showAll && total > shown) {
      out.insertAdjacentHTML("beforeend", `<p class="muted small">Mostrati ${shown} di ${total} link.
        <button class="btn" id="ls-more">Mostra tutti</button></p>`);
      out.querySelector("#ls-more").onclick = () => { showAll = true; renderOut(el); };
    }
    count.textContent = `${total.toLocaleString("it")} link in ${groups.length} gruppi` +
      (scan.title ? ` · ${scan.title}` : "");
    out.querySelectorAll(".ls-row").forEach(syncKind);
  }

  gs.registerView("linkstores", {
    title: "Link → Store",
    async render(el) {
      await loadState();
      el.innerHTML = `
        <p class="muted small">Una pagina che raccoglie altri siti: i link sono raggruppati per intestazione
          (h2/h3) e ognuno può diventare lo store di una console oppure — per il PC — uno store
          «repack» o «download diretto». Aggiungere un link non scarica nulla: si legge poi con
          «↻ Leggi gli store aggiunti» (o da Impostazioni › Store).</p>
        <div class="ls-bar">
          <input class="field grow" id="ls-url" placeholder="https://esempio.test/pagina-con-link/"
                 value="${esc(url)}">
          <label class="ls-chk small" title="Utile quando la pagina elenca altri siti">
            <input type="checkbox" id="ls-ext"${externalOnly ? " checked" : ""}> solo altri siti</label>
          <button class="btn" id="ls-scan">📖 Leggi i link</button>
        </div>
        <div class="ls-bar" id="ls-tools">
          <input class="field grow" id="ls-q" placeholder="Filtra per nome o indirizzo" value="${esc(query)}">
          <span class="muted small" id="ls-count"></span>
          <button class="btn" id="ls-read" hidden></button>
        </div>
        <div id="ls-out"></div>`;

      const scanBtn = el.querySelector("#ls-scan");
      el.querySelector("#ls-url").oninput = (e) => { url = e.target.value.trim(); };
      el.querySelector("#ls-ext").onchange = (e) => { externalOnly = e.target.checked; };
      el.querySelector("#ls-q").oninput = (e) => { query = e.target.value; renderOut(el); };
      el.querySelector("#ls-read").onclick = () => readNow(el);

      scanBtn.onclick = async () => {
        if (!/^https?:\/\//i.test(url)) return gs.toast("Indirizzo non valido (serve http:// o https://)", true);
        scanBtn.disabled = true;
        scanBtn.textContent = "Lettura…";
        el.querySelector("#ls-out").innerHTML = `<p class="muted">Lettura della pagina…</p>`;
        try {
          scan = await gs.api("/plugin-api/linkstores/scan", { body: { url, external_only: externalOnly } });
          showAll = false;
          query = "";
          el.querySelector("#ls-q").value = "";
          await loadState();
        } catch (e) {
          scan = null;
          gs.toast(e.message, true);
        } finally {
          scanBtn.disabled = false;
          scanBtn.textContent = "📖 Leggi i link";
          renderOut(el);
        }
      };

      // un solo ascoltatore per tutti i link: la pagina può averne centinaia
      el.querySelector("#ls-out").addEventListener("change", (e) => {
        const row = e.target.closest(".ls-row");
        if (row && e.target.classList.contains("ls-plat")) syncKind(row);
      });
      el.querySelector("#ls-out").addEventListener("click", (e) => {
        const btn = e.target.closest("[data-ls-add]");
        if (btn) addLink(btn.closest(".ls-row"), el);
      });

      updateTools(el);
      renderOut(el);
    },
  });
}
