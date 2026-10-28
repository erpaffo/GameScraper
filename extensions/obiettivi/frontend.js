// Obiettivi personali: pagina con gli obiettivi a periodo, la serie di giorni e i distintivi.

const TIPI = { finiti: "Giochi finiti", ore: "Ore giocate", giorni: "Giorni di gioco" };
const PERIODI = { settimana: "in settimana", mese: "nel mese", anno: "nell'anno" };

export default function (gs) {
  gs.addSidebarItem({ id: "obiettivi", label: "Obiettivi", icon: "🏆", view: "obiettivi" });

  gs.registerView("obiettivi", {
    title: "Obiettivi",
    async render(el) {
      let d;
      const load = async () => { d = await gs.api("/plugin-api/obiettivi/stato"); };
      const save = async (obiettivi) => { d = await gs.api("/plugin-api/obiettivi/salva", { body: { obiettivi } }); draw(); };
      const plain = () => d.obiettivi.map(({ id, tipo, n, periodo }) => ({ id, tipo, n, periodo }));

      const draw = () => {
        el.innerHTML = `<div class="ob-serie"><b>🔥 ${d.serie.attuale}</b> ${d.serie.attuale === 1 ? "giorno" : "giorni"} di fila
            <span class="muted">· record ${d.serie.migliore}</span></div>
          <h3>I miei obiettivi</h3>
          ${d.obiettivi.length ? d.obiettivi.map((o) => {
            const pct = Math.min(100, Math.round((100 * o.valore) / o.n));
            return `<div class="ob-row ${o.fatto ? "done" : ""}"><div><b>${o.fatto ? "✔ " : ""}${gs.esc(o.etichetta)}</b>
                <span class="muted">${String(o.valore).replace(".", ",")} / ${o.n}</span></div>
              <div class="ob-track"><div class="ob-fill" style="width:${pct}%"></div></div>
              <button class="ob-del" data-id="${gs.esc(o.id)}" title="Elimina">✕</button></div>`;
          }).join("") : `<p class="muted">Nessun obiettivo: aggiungine uno qui sotto.</p>`}
          <form class="ob-form">Voglio
            <select name="tipo">${Object.entries(TIPI).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
            <input name="n" type="number" min="1" max="10000" value="3" required>
            <select name="periodo">${Object.entries(PERIODI).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
            <button class="btn primary" type="submit">Aggiungi</button></form>
          <h3>Distintivi</h3>
          <div class="ob-badges">${d.distintivi.map((b) => `<div class="ob-badge ${b.ottenuto ? "on" : ""}" title="${gs.esc(b.descrizione)}">
            <span>${b.ottenuto ? "🏅" : "🔒"}</span><b>${gs.esc(b.titolo)}</b>
            <small class="muted">${b.ottenuto ? gs.esc(b.descrizione) : `${String(b.valore).replace(".", ",")} / ${b.soglia}`}</small></div>`).join("")}</div>`;
      };

      el.onsubmit = async (e) => {
        e.preventDefault();
        const f = new FormData(e.target);
        try { await save([...plain(), { tipo: f.get("tipo"), n: f.get("n"), periodo: f.get("periodo") }]); } catch (err) { gs.toast(err.message, true); }
      };
      el.onclick = async (e) => {
        const b = e.target.closest(".ob-del");
        if (!b) return;
        try { await save(plain().filter((o) => o.id !== b.dataset.id)); } catch (err) { gs.toast(err.message, true); }
      };
      el.innerHTML = `<p class="muted">Calcolo…</p>`;
      try { await load(); draw(); } catch (e) { el.textContent = e.message; }
    },
  });
}
