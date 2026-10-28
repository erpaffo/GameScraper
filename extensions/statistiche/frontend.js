// Plugin di esempio (frontend): una vista nella barra laterale e un'azione nel dettaglio dei giochi.

const fmt = (sec) => {
  if (!sec) return "—";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
};

export default function (gs) {
  gs.addSidebarItem({ id: "statistiche", label: "Statistiche", icon: "📊", view: "statistiche" });

  gs.registerView("statistiche", {
    title: "Statistiche",
    async render(el) {
      el.innerHTML = `<p class="muted">Calcolo…</p>`;
      const d = await gs.api("/plugin-api/statistiche/riepilogo");
      const lib = gs.library();
      const name = (id) => lib?.plats.get(id)?.name || lib?.stores.find((s) => s.id === id)?.name || id;
      const rows = Object.entries(d.per_piattaforma).sort((a, b) => b[1].giochi - a[1].giochi);
      const maxG = Math.max(1, ...rows.map(([, v]) => v.giochi));
      const pct = d.giochi ? Math.round((100 * d.con_dati) / d.giochi) : 0;
      el.innerHTML = `
        <div class="st-cards">
          <div class="st-card"><b>${d.giochi.toLocaleString("it")}</b><span>giochi unici</span></div>
          <div class="st-card"><b>${pct}%</b><span>con metadati</span></div>
          <div class="st-card"><b>${fmt(d.tempo_totale)}</b><span>tempo di gioco registrato</span></div>
        </div>
        <h3>Giochi per console e store</h3>
        <div class="st-bars">${rows.map(([k, v]) => `<div class="st-row"><span>${gs.esc(name(k))}</span>
          <div class="st-track"><div class="st-fill" style="width:${(100 * v.giochi) / maxG}%"></div></div>
          <span class="st-n">${v.giochi.toLocaleString("it")}${v.tempo ? " · " + fmt(v.tempo) : ""}</span></div>`).join("")}</div>
        <h3>I piu' giocati</h3>
        ${d.piu_giocati.length ? `<ol class="st-top">${d.piu_giocati.map((t) => `<li><b>${gs.esc(t.titolo)}</b>
          <span class="muted">${gs.esc(name(t.piattaforma))} · ${fmt(t.tempo)} · ${t.partite} avvii</span></li>`).join("")}</ol>`
          : `<p class="muted">Ancora nessuna partita registrata: avvia un gioco dall'app.</p>`}`;
    },
  });

  gs.addGameAction({
    id: "youtube",
    label: "Video su YouTube",
    icon: "▶",
    run: (game) => gs.api("/api/open", {
      body: { url: "https://www.youtube.com/results?search_query=" + encodeURIComponent(`${game.title} ${game.platform_name} gameplay`) },
    }),
  });
}
