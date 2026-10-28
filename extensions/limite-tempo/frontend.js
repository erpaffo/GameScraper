// Limite di tempo: scheda nelle impostazioni con i limiti e il tempo giocato oggi e in settimana.

const fmt = (s) => {
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
};
const bar = (usato, limite) => {
  if (!limite) return `<span class="muted">${fmt(usato)} · nessun limite</span>`;
  const pct = Math.min(100, Math.round((100 * usato) / (limite * 3600)));
  return `<div class="lt-track"><div class="lt-fill ${pct >= 100 ? "over" : ""}" style="width:${pct}%"></div></div>
    <span class="${pct >= 100 ? "status-bad" : "muted"}">${fmt(usato)} su ${limite} h</span>`;
};

export default function (gs) {
  gs.addSettingsTab({
    id: "limite-tempo",
    label: "Limite di tempo",
    async render(el) {
      let d;
      try { d = await gs.api("/plugin-api/limite-tempo/stato"); } catch (e) { el.textContent = e.message; return; }
      el.innerHTML = `<h3>Limite di tempo</h3>
        <p class="muted">Ti avvisa con una notifica quando superi il tempo di gioco scelto. Conta le partite del profilo
          <b>${gs.esc(d.profilo)}</b>. È un avviso: non chiude il gioco.</p>
        <label class="chk"><input type="checkbox" id="lt-on" ${d.attivo ? "checked" : ""}> Avvisi attivi</label>
        <div class="lt-grid">
          <label>Ore al giorno <input type="number" id="lt-g" min="0" max="24" step="0.5" value="${d.giorno_ore || ""}" placeholder="nessun limite"></label>
          <div>${bar(d.oggi, d.giorno_ore)}</div>
          <label>Ore a settimana <input type="number" id="lt-s" min="0" max="168" step="0.5" value="${d.settimana_ore || ""}" placeholder="nessun limite"></label>
          <div>${bar(d.settimana, d.settimana_ore)}</div>
        </div>
        <p><button class="btn primary" id="lt-save">Salva</button></p>
        <p class="muted small">La settimana comincia il lunedì. Per un altro profilo (per esempio dei figli) cambia profilo in Impostazioni e imposta i suoi limiti.</p>`;
      el.querySelector("#lt-save").onclick = async () => {
        try {
          await gs.api("/plugin-api/limite-tempo/salva", { body: {
            attivo: el.querySelector("#lt-on").checked, giorno_ore: el.querySelector("#lt-g").value || 0,
            settimana_ore: el.querySelector("#lt-s").value || 0 } });
          gs.toast("Limiti salvati");
        } catch (e) { gs.toast(e.message, true); }
      };
    },
  });
}
