// Store estensioni: elenco dall'indice online, con Installa / Aggiorna / Rimuovi. Le modifiche valgono dal riavvio.

const BTN = { available: "⬇ Installa", update: "⬆ Aggiorna", installed: "Rimuovi", incompatible: "Non compatibile" };

export default function (gs) {
  gs.addSidebarItem({ id: "estensioni", label: "Store estensioni", icon: "🧩", view: "estensioni" });

  gs.registerView("estensioni", {
    title: "Store estensioni",
    async render(el) {
      el.innerHTML = `<p class="muted">Le estensioni non sono più incluse nell'app: installa da qui quelle che ti servono.
        Le impostazioni di quelle che usavi sono rimaste. Dopo un'installazione o una rimozione riavvia l'app.</p>
        <div id="es-list" class="es-list"><p class="muted">Caricamento…</p></div>`;
      const list = el.querySelector("#es-list");
      const draw = async () => {
        let d;
        try { d = await gs.api("/plugin-api/estensioni/catalogo"); } catch (e) {
          list.innerHTML = `<p class="status-bad">${gs.esc(e.message)}</p>`; return;
        }
        list.innerHTML = d.items.length ? d.items.map((x) => `<div class="es-row">
            <div><b>${gs.esc(x.name || x.id)}</b> <span class="muted small">v${gs.esc(x.version || "?")}`
            + (x.installed_version && x.state === "update" ? ` (installata v${gs.esc(x.installed_version)})` : "")
            + (x.author ? ` · ${gs.esc(x.author)}` : "") + `</span>
              <div class="muted small">${gs.esc(x.description || "")}</div>`
            + (x.state === "incompatible" ? `<div class="status-bad small">Richiede GameScraper ${gs.esc(x.min_app)}</div>` : "")
            + (x.dev ? `<div class="muted small">Collegata alla cartella di sviluppo</div>` : "") + `</div>
            <div class="es-btns">`
            + (x.state === "update" && !x.dev ? `<button class="btn primary" data-act="installa" data-id="${gs.esc(x.id)}">${BTN.update}</button>` : "")
            + (x.state === "available" ? `<button class="btn primary" data-act="installa" data-id="${gs.esc(x.id)}">${BTN.available}</button>` : "")
            + (x.state === "incompatible" ? `<button class="btn" disabled>${BTN.incompatible}</button>` : "")
            + ((x.state === "installed" || x.state === "update") && !x.dev ? `<button class="btn" data-act="rimuovi" data-id="${gs.esc(x.id)}">${BTN.installed}</button>` : "")
            + (x.state === "installed" && x.dev ? `<span class="muted small">Installata</span>` : "")
            + `</div></div>`).join("")
          : `<p class="muted">Nessuna estensione nell'indice.</p>`;
      };
      list.onclick = async (e) => {
        const b = e.target.closest("[data-act]");
        if (!b) return;
        if (b.dataset.act === "rimuovi" && !confirm("Rimuovere questa estensione? Le sue impostazioni restano.")) return;
        b.disabled = true;
        try {
          await gs.api(`/plugin-api/estensioni/${b.dataset.act}`, { body: { id: b.dataset.id } });
          gs.toast(b.dataset.act === "installa" ? "Installata: riavvia l'app per usarla" : "Rimossa: riavvia l'app per applicare");
        } catch (err) { gs.toast(err.message, true); }
        draw();
      };
      draw();
    },
  });
}
