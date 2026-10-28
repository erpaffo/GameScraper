// Pulizia libreria: liste di giochi da sistemare, con azioni di massa e badge «DUP» sui duplicati.
import { duplicates, incomplete, neverPlayed, stale } from "./logica.js";

const TABS = [["dup", "Duplicati"], ["mai", "Mai avviati"], ["fermi", "Fermi da anni"], ["meta", "Dati incompleti"]];
const ACTIONS = { hide: ["Nascondi", { field: "hidden", value: true }],
  dropped: ["Segna abbandonato", { field: "status", value: "dropped" }],
  backlog: ["Metti nel backlog", { field: "status", value: "backlog" }] };

export default function (gs) {
  let dupIds = new Set(), builtAt = null;
  const refreshDup = () => {
    const lib = gs.library();
    if (!lib || lib.builtAt === builtAt) return;
    builtAt = lib.builtAt;
    dupIds = new Set(duplicates(lib.games).flat().map((g) => g.id));
  };
  gs.addTileBadge({ id: "dup", render: (g) => { refreshDup(); return dupIds.has(g.id) ? "DUP" : null; } });
  gs.addSidebarItem({ id: "pulizia", label: "Pulizia libreria", icon: "🧹", view: "pulizia" });

  gs.registerView("pulizia", {
    title: "Pulizia libreria",
    render(el) {
      let tab = gs.store.get("pulizia.tab", "dup"), years = gs.store.get("pulizia.anni", 2);
      const sel = new Set();

      const rows = () => {
        const lib = gs.library();
        if (tab === "dup") return duplicates(lib.games).map((l) => ({ ids: l.map((g) => g.id), main: l[0], extra: `${l.length} copie` }));
        if (tab === "mai") return neverPlayed(lib.games).map((g) => ({ ids: [g.id], main: g, extra: g.year || "" }));
        if (tab === "fermi") return stale(lib.games, years).map((g) => ({ ids: [g.id], main: g,
          extra: `ultima volta ${new Date(g.grp.last * 1000).toLocaleDateString("it")}` }));
        return incomplete(lib.games).map(({ g, m }) => ({ ids: [g.id], main: g, extra: "manca: " + m.join(", ") }));
      };
      const platName = (g) => gs.library().plats.get(g.platform)?.name || g.store || g.platform;

      const draw = () => {
        const list = rows();
        const shown = list.slice(0, 500);
        el.innerHTML = `<div class="pl-tabs">${TABS.map(([k, l]) => `<button class="btn ${k === tab ? "primary" : ""}" data-tab="${k}">${l}</button>`).join("")}
            ${tab === "fermi" ? `<label class="pl-years">da <select id="pl-y">${[1, 2, 3, 5].map((y) => `<option ${y === years ? "selected" : ""}>${y}</option>`).join("")}</select> anni</label>` : ""}</div>
          <p class="muted">${list.length.toLocaleString("it")} ${list.length === 1 ? "voce" : "voci"}${list.length > shown.length ? ` (le prime ${shown.length})` : ""}.
            ${tab === "dup" ? "Per i duplicati le azioni valgono per tutte le copie tranne la prima." : ""}</p>
          <div class="pl-bar"><button class="btn" id="pl-all">Seleziona tutto</button><button class="btn" id="pl-none">Nessuno</button>
            ${Object.entries(ACTIONS).map(([k, [l]]) => `<button class="btn" data-act="${k}" ${sel.size ? "" : "disabled"}>${l} (${sel.size})</button>`).join("")}
            <button class="btn" id="pl-manual-dupes" title="Toglie le voci aggiunte a mano due volte (stesso eseguibile e stessi argomenti)">🧹 Rimuovi doppioni manuali</button></div>
          <ul class="pl-list">${shown.map((r, i) => {
            const on = r.ids.slice(r.ids.length > 1 ? 1 : 0).every((id) => sel.has(id));
            return `<li><label><input type="checkbox" data-i="${i}" ${on ? "checked" : ""}> <b>${gs.esc(r.main.title)}</b>
              <span class="muted">${gs.esc(platName(r.main))} · ${gs.esc(r.extra)}</span></label>
              <a href="#" data-open="${r.main.id}">Apri</a></li>`; }).join("")}</ul>`;
        el._rows = shown;
      };

      const targets = (r) => (r.ids.length > 1 ? r.ids.slice(1) : r.ids);   // duplicati: si tiene la prima copia
      el.onchange = (e) => {
        if (e.target.id === "pl-y") { years = +e.target.value; gs.store.set("pulizia.anni", years); sel.clear(); return draw(); }
        const i = e.target.dataset.i;
        if (i === undefined) return;
        for (const id of targets(el._rows[+i])) (e.target.checked ? sel.add(id) : sel.delete(id));
        draw();
      };
      el.onclick = async (e) => {
        const t = e.target.closest("[data-tab],[data-act],[data-open],#pl-all,#pl-none,#pl-manual-dupes");
        if (!t) return;
        if (t.dataset.open) { e.preventDefault(); return gs.openGame(+t.dataset.open); }
        if (t.dataset.tab) { tab = t.dataset.tab; gs.store.set("pulizia.tab", tab); sel.clear(); return draw(); }
        if (t.id === "pl-all") { el._rows.forEach((r) => targets(r).forEach((id) => sel.add(id))); return draw(); }
        if (t.id === "pl-none") { sel.clear(); return draw(); }
        if (t.id === "pl-manual-dupes") {
          if (!confirm("Rimuovere i doppioni tra le voci aggiunte a mano (stesso eseguibile e stessi argomenti)? Si tiene la più vecchia.")) return;
          try {
            const r = await gs.api("/api/manual/dedupe", { body: {} });
            gs.toast(r.removed ? `${r.removed} ${r.removed === 1 ? "doppione rimosso" : "doppioni rimossi"}` : "Nessun doppione da rimuovere");
            if (r.removed) await gs.refresh();
          } catch (err) { gs.toast(err.message, true); }
          return draw();
        }
        const [label, body] = ACTIONS[t.dataset.act];
        if (!sel.size || !confirm(`${label}: ${sel.size} giochi?`)) return;
        let ok = 0, fail = 0;
        for (const id of sel) { try { await gs.api(`/api/game/${id}/flag`, { body }); ok++; } catch { fail++; } }
        gs.toast(`${label}: ${ok} fatti${fail ? `, ${fail} non riusciti` : ""}`, !!fail);
        sel.clear();
        await gs.refresh();
        draw();
      };
      draw();
    },
  });
}
