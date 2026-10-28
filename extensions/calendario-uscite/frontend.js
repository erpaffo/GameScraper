// Calendario uscite: desideri e titoli attesi dei tuoi generi, raggruppati per mese.

const MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"];

export default function (gs) {
  gs.addSidebarItem({ id: "calendario-uscite", label: "Calendario uscite", icon: "📅", view: "calendario-uscite" });

  gs.registerView("calendario-uscite", {
    title: "Calendario uscite",
    async render(el) {
      const list = (items) => {
        const by = new Map();
        for (const v of items) {
          const [y, m] = v.data.split("-");
          const k = `${MESI[+m - 1]} ${y}`;
          if (!by.has(k)) by.set(k, []);
          by.get(k).push(v);
        }
        return [...by].map(([k, vs]) => `<h4>${k}</h4><ul class="cu-list">${vs.map((v) => `<li>
            <img src="${gs.esc(v.copertina || "")}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
            <div><b>${gs.esc(v.titolo)}</b>
              <div class="muted small">${new Date(v.data + "T00:00:00").toLocaleDateString("it", { day: "numeric", month: "long" })}
                ${v.giorni === 0 ? " · <b>oggi</b>" : v.giorni === 1 ? " · domani" : ` · tra ${v.giorni} giorni`}</div>
              <div class="muted small">${gs.esc(v.piattaforme.join(", "))}${v.generi.length ? " · " + gs.esc(v.generi.join(", ")) : ""}</div></div></li>`).join("")}</ul>`).join("");
      };
      el.innerHTML = `<p class="muted">Cerco le uscite…</p>`;
      let d;
      try { d = await gs.api("/plugin-api/calendario-uscite/uscite"); } catch (e) { el.innerHTML = `<p class="status-bad">${gs.esc(e.message)}</p>`; return; }
      el.innerHTML = `<h3>💝 Dalla tua lista dei desideri</h3>`
        + (d.desideri.length ? list(d.desideri) : `<p class="muted">Nessun gioco della lista dei desideri ha una data di uscita futura.</p>`)
        + `<h3>✨ I più attesi nei prossimi mesi</h3>`
        + (d.prossime.length ? list(d.prossime) : `<p class="muted">Niente in vista.</p>`)
        + `<p class="muted small">Dati di RAWG${d.generi.length ? " · filtrati sui tuoi generi più giocati" : ""}.</p>`;
    },
  });
}
