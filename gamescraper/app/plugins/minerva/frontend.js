// Store Minerva: un pulsante che importa i link di minerva-archive.org da r-roms in tutti gli store online.

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

export default function (gs) {
  gs.addSidebarItem({ id: "minerva", label: "Store Minerva", icon: "🦉", view: "minerva" });

  gs.registerView("minerva", {
    title: "Store Minerva",
    async render(el) {
      el.innerHTML = `<p class="muted">Aggiunge allo «Store online» di ogni console le cartelle di minerva-archive.org
        elencate su r-roms.github.io, poi ne legge l'elenco dei giochi. I link gia' presenti restano.</p>
        <p><button class="btn primary" id="mv-go">Importa da r-roms</button></p>
        <pre id="mv-log" class="muted" style="white-space:pre-wrap;max-height:60vh;overflow:auto"></pre>`;
      const log = el.querySelector("#mv-log"), btn = el.querySelector("#mv-go");
      const show = (j) => { log.textContent = j.log.slice(-40).join("\n"); btn.disabled = j.status === "running"; };
      const follow = async () => {
        let j;
        do { await wait(1500); j = await gs.api("/plugin-api/minerva/stato"); if (el.isConnected) show(j); }
        while (j.status === "running");
        if (j.status !== "done") return gs.toast("Importazione Minerva non riuscita", true);
        await gs.api("/api/rebuild", { body: {} });
        do { await wait(1500); } while ((await gs.api("/api/status")).building);
        gs.toast("Store Minerva aggiornati");
        gs.refresh();
      };
      btn.onclick = async () => { show(await gs.api("/plugin-api/minerva/importa", { body: {} })); follow(); };
      const j = await gs.api("/plugin-api/minerva/stato");
      show(j);
      if (j.status === "running") follow();
    },
  });
}
