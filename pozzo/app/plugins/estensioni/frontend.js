// Store estensioni: elenco dalle fonti (lo store ufficiale e quelle aggiunte con un link), con Installa / Aggiorna /
// Rimuovi. Le modifiche valgono dal riavvio.

const BTN = { available: "⬇ Installa", update: "⬆ Aggiorna", installed: "Rimuovi", incompatible: "Non compatibile" };
const short = (link) => String(link).replace(/^https:\/\/(github\.com\/|raw\.githubusercontent\.com\/)?/, "").replace(/\/index\.json$/, "");

export default function (gs) {
  gs.addSidebarItem({ id: "estensioni", label: "Store estensioni", icon: "🧩", view: "estensioni" });

  gs.registerView("estensioni", {
    title: "Store estensioni",
    async render(el) {
      el.innerHTML = `<p class="muted">Installa da qui le estensioni che ti servono. Dopo un'installazione o una rimozione riavvia l'app.</p>
        <details class="es-src"><summary>Fonti delle estensioni</summary>
          <div id="es-sources"></div>
          <form id="es-add" class="es-add">
            <input class="field" name="link" placeholder="https://github.com/utente/repository" autocomplete="off" required>
            <button class="btn" type="submit">Aggiungi</button>
          </form>
          <p class="muted small">Incolla il link del repository di un'estensione o di un altro store di estensioni.
            Le estensioni eseguono codice sul tuo PC: aggiungi solo fonti di cui ti fidi.</p>
        </details>
        <div id="es-list" class="es-list"><p class="muted">Caricamento…</p></div>`;
      const list = el.querySelector("#es-list"), srcBox = el.querySelector("#es-sources");
      const draw = async () => {
        let d;
        try { d = await gs.api("/plugin-api/estensioni/catalogo"); } catch (e) {
          list.innerHTML = `<p class="status-bad">${gs.esc(e.message)}</p>`; return;
        }
        srcBox.innerHTML = d.sources.map((s) => `<div class="es-srow">
            <span>${s.official ? "⭐ <b>Store ufficiale</b>" : "🔗"} <span class="muted small">${gs.esc(short(s.link))}</span>
              <span class="muted small">· ${s.count} ${s.count === 1 ? "estensione" : "estensioni"}</span>
              ${s.errors.length ? `<div class="status-bad small">${s.errors.map(gs.esc).join("<br>")}</div>` : ""}</span>
            ${s.official ? "" : `<button class="btn" data-del="${gs.esc(s.link)}">Rimuovi</button>`}</div>`).join("");
        list.innerHTML = d.items.length ? d.items.map((x) => `<div class="es-row">
            <div><b>${gs.esc(x.name || x.id)}</b> <span class="muted small">v${gs.esc(x.version || "?")}`
            + (x.installed_version && x.state === "update" ? ` (installata v${gs.esc(x.installed_version)})` : "")
            + (x.author ? ` · ${gs.esc(x.author)}` : "") + `</span>`
            + (x.official ? "" : ` <span class="es-third" title="${gs.esc(x.source)}">da ${gs.esc(short(x.source))}</span>`)
            + `<div class="muted small">${gs.esc(x.description || "")}</div>`
            + (x.state === "incompatible" ? `<div class="status-bad small">Richiede PozzoLauncher ${gs.esc(x.min_app)}</div>` : "")
            + (x.dev ? `<div class="muted small">Collegata alla cartella di sviluppo</div>` : "") + `</div>
            <div class="es-btns">`
            + (x.state === "update" && !x.dev ? `<button class="btn primary" data-act="installa" data-id="${gs.esc(x.id)}" data-src="${gs.esc(x.source)}">${BTN.update}</button>` : "")
            + (x.state === "available" ? `<button class="btn primary" data-act="installa" data-id="${gs.esc(x.id)}" data-src="${gs.esc(x.source)}">${BTN.available}</button>` : "")
            + (x.state === "incompatible" ? `<button class="btn" disabled>${BTN.incompatible}</button>` : "")
            + ((x.state === "installed" || x.state === "update") && !x.dev ? `<button class="btn" data-act="rimuovi" data-id="${gs.esc(x.id)}">${BTN.installed}</button>` : "")
            + (x.state === "installed" && x.dev ? `<span class="muted small">Installata</span>` : "")
            + `</div></div>`).join("")
          : `<p class="muted">Nessuna estensione disponibile.</p>`;
      };
      list.onclick = async (e) => {
        const b = e.target.closest("[data-act]");
        if (!b) return;
        if (b.dataset.act === "rimuovi" && !confirm("Rimuovere questa estensione? Le sue impostazioni restano.")) return;
        b.disabled = true;
        try {
          await gs.api(`/plugin-api/estensioni/${b.dataset.act}`, { body: { id: b.dataset.id, fonte: b.dataset.src } });
          gs.toast(b.dataset.act === "installa" ? "Installata: riavvia l'app per usarla" : "Rimossa: riavvia l'app per applicare");
        } catch (err) { gs.toast(err.message, true); }
        draw();
      };
      el.querySelector("#es-add").onsubmit = async (e) => {
        e.preventDefault();
        const f = e.target, link = f.link.value.trim();
        if (!confirm("Le estensioni di questa fonte potranno eseguire codice sul tuo PC. Aggiungerla?")) return;
        f.querySelector("button").disabled = true;
        try { await gs.api("/plugin-api/estensioni/fonti/aggiungi", { body: { link } }); f.reset(); gs.toast("Fonte aggiunta"); }
        catch (err) { gs.toast(err.message, true); }
        f.querySelector("button").disabled = false;
        draw();
      };
      srcBox.onclick = async (e) => {
        const b = e.target.closest("[data-del]");
        if (!b || !confirm("Togliere questa fonte? Le estensioni già installate restano.")) return;
        try { await gs.api("/plugin-api/estensioni/fonti/rimuovi", { body: { link: b.dataset.del } }); } catch (err) { gs.toast(err.message, true); }
        draw();
      };
      draw();
    },
  });
}
