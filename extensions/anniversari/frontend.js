// Anniversari retro: badge sulle copertine dei giochi usciti oggi e pagina «Oggi nel…».

const MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"];

export default function (gs) {
  let oggi = { giorno: "", giochi: [] };
  const anni = new Map();   // id gioco -> anni compiuti oggi

  async function load() {
    try {
      oggi = await gs.api("/plugin-api/anniversari/oggi");
      const prima = anni.size;
      anni.clear();
      for (const g of oggi.giochi) anni.set(g.id, g.anni);
      if (prima || anni.size) gs.redraw?.();   // i badge arrivano dopo la prima schermata: si ridisegnano le copertine
    } catch { /* senza backend niente badge */ }
  }

  gs.addTileBadge({ id: "anniversario", render: (g) => (anni.has(g.id) ? `${anni.get(g.id)} ANNI` : null) });
  gs.addSidebarItem({ id: "anniversari", label: "Oggi nel…", icon: "🎂", view: "anniversari" });
  gs.on("libraryLoaded", load);   // anche a mezzanotte passata, alla prima ricarica
  load();

  gs.registerView("anniversari", {
    title: "Oggi nel…",
    async render(el) {
      await load();
      const [m, d] = oggi.giorno.split("-").map(Number);
      const lib = gs.library();
      const name = (g) => lib?.plats.get(g.piattaforma)?.name || lib?.stores.find((s) => s.id === g.piattaforma)?.name || g.piattaforma;
      const rows = oggi.giochi.map((g) => `<li data-id="${g.id}"><b>${g.anni} anni fa</b> <a href="#">${gs.esc(g.titolo)}</a>
          <span class="muted">${gs.esc(name(g))}</span></li>`).join("");
      el.innerHTML = `<h2>Oggi, ${d} ${MESI[m - 1] || ""}</h2>`
        + (rows ? `<ul class="an-list">${rows}</ul>`
          : `<p class="muted">Nessun gioco della tua libreria è uscito in questo giorno. Servono le date di uscita: avvia il recupero dei dati.</p>`);
      el.onclick = (e) => {
        const li = e.target.closest("li[data-id]");
        if (li && e.target.closest("a")) { e.preventDefault(); gs.openGame(+li.dataset.id); }
      };
    },
  });
}
