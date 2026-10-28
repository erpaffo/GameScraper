// Diario di gioco: sezione nel dettaglio con voci datate e cronologia delle sessioni.

const dur = (s) => {
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${Math.max(1, m)} min`;
};
const when = (t) => new Date(t * 1000).toLocaleString("it", { dateStyle: "medium", timeStyle: "short" });

export default function (gs) {
  gs.addDetailSection({
    id: "diario",
    title: "📓 Diario",
    async render(game) {
      const box = document.createElement("div");
      box.className = "di-box";
      const draw = async () => {
        let d;
        try { d = await gs.api(`/plugin-api/diario/gioco?id=${game.id}`); } catch (e) { box.textContent = e.message; return; }
        // voci e sessioni in un'unica cronologia, dalla piu' recente
        const righe = [...d.voci.map((v) => ({ t: v.t, voce: v })), ...d.sessioni.map((s) => ({ t: s.inizio, s }))].sort((a, b) => b.t - a.t);
        box.innerHTML = `<form class="di-form"><textarea rows="2" maxlength="4000" placeholder="Nuova voce: a che punto sei, un trucco, una password…"></textarea>
            <button class="btn primary" type="submit">Aggiungi</button></form>
          <p class="muted small">${d.partite ? `${d.partite} ${d.partite === 1 ? "sessione" : "sessioni"} · ${dur(d.totale)} in totale` : "Nessuna sessione registrata."}</p>
          <ul class="di-list">${righe.map((r) => r.voce
            ? `<li class="di-voce"><span class="muted small">${when(r.t)}</span> <button class="di-del" data-t="${r.t}" title="Elimina">✕</button>
                <div>${gs.esc(r.voce.testo).replace(/\n/g, "<br>")}</div></li>`
            : `<li class="di-sess"><span class="muted small">${when(r.t)}</span> ▶ ${dur(r.s.secondi)}</li>`).join("")}</ul>`;
      };
      box.addEventListener("submit", async (e) => {
        e.preventDefault();
        const ta = box.querySelector("textarea");
        if (!ta.value.trim()) return;
        try { await gs.api("/plugin-api/diario/aggiungi", { body: { id: game.id, testo: ta.value } }); } catch (err) { return gs.toast(err.message, true); }
        draw();
      });
      box.addEventListener("click", async (e) => {
        const b = e.target.closest(".di-del");
        if (!b || !confirm("Eliminare questa voce del diario?")) return;
        try { await gs.api("/plugin-api/diario/elimina", { body: { id: game.id, t: +b.dataset.t } }); } catch (err) { return gs.toast(err.message, true); }
        draw();
      });
      await draw();
      return box;
    },
  });
}
