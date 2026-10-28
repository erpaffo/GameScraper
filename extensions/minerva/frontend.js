// Store Minerva + Switch: «Importa store» riempie la sezione «Store online» di ogni console (cartelle di
// minerva-archive.org elencate da r-roms, più Switch, PS4 e PS5 preimpostati) senza leggerli; «Leggi» scarica
// l'elenco dei giochi di una console (o di tutte quelle mai lette) con la coda degli store dell'app.

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

export default function (gs) {
  gs.addSidebarItem({ id: "minerva", label: "Store Minerva", icon: "🦉", view: "minerva" });

  gs.registerView("minerva", {
    title: "Store Minerva + Switch",
    async render(el) {
      el.innerHTML = `<p class="muted">«Importa store» aggiunge allo «Store online» di ogni console le cartelle di
        minerva-archive.org elencate su r-roms.github.io, più gli store preimpostati: <b>Switch</b> (NXBrew, Ziperto
        NSP e XCI), <b>PS4</b> e <b>PS5</b> (superpsx). Non scarica niente: gli elenchi dei giochi si leggono con
        «Leggi», console per console. Gli store già presenti non si toccano.</p>
        <p><button class="btn primary" id="mv-go">Importa store</button>
          <button class="btn" id="mv-all" hidden>Leggi tutti quelli mai letti</button></p>
        <pre id="mv-log" class="muted" style="white-space:pre-wrap;max-height:30vh;overflow:auto"></pre>
        <p id="mv-read" class="muted"></p>
        <div id="mv-list"></div>`;
      const log = el.querySelector("#mv-log"), btn = el.querySelector("#mv-go"), all = el.querySelector("#mv-all");
      const readInfo = el.querySelector("#mv-read"), list = el.querySelector("#mv-list");
      const show = (j) => { log.textContent = j.log.slice(-40).join("\n"); btn.disabled = j.status === "running"; };
      let consoles = [];

      const drawList = async () => {
        try { consoles = (await gs.api("/plugin-api/minerva/console")).consoles; } catch { return; }
        const unread = consoles.flatMap((c) => c.stores.filter((s) => !s.games).map((s) => s.id));
        all.hidden = !unread.length;
        all.textContent = `Leggi tutti quelli mai letti (${unread.length})`;
        list.innerHTML = consoles.length ? `<table class="mv-table" style="width:100%;border-collapse:collapse">
          ${consoles.map((c) => `<tr style="border-top:1px solid var(--line)">
            <td style="padding:6px 0"><b>${gs.esc(c.name)}</b></td>
            <td class="muted">${c.stores.length} store${c.unread ? ` · ${c.unread} mai letti` : ""}</td>
            <td class="muted">${c.games.toLocaleString("it")} giochi</td>
            <td style="text-align:right"><button class="btn" data-read="${gs.esc(c.platform)}">Leggi</button></td></tr>`).join("")}
          </table>` : `<p class="muted">Nessuno store online configurato: premi «Importa store».</p>`;
      };

      // lettura: la coda degli store dell'app (rispetta il limite «in parallelo» e ricostruisce la libreria una volta)
      const followRead = async () => {
        let s;
        do {
          await wait(1500);
          try { s = (await gs.api("/api/store-refresh")).job; } catch { return; }
          if (el.isConnected) readInfo.textContent = s.status === "running" ? `${s.phase} · ${s.done}/${s.total} store letti` : "";
        } while (s.status === "running");
        gs.toast("Store letti: libreria aggiornata");
        gs.refresh();
        if (el.isConnected) drawList();
      };
      const read = async (ids) => {
        if (!ids.length) return;
        try { await gs.api("/api/store-refresh/run", { body: { ids } }); } catch (e) { return gs.toast(e.message, true); }
        gs.toast(`${ids.length} store in coda`);
        followRead();
      };
      list.onclick = (e) => {
        const b = e.target.closest("[data-read]");
        const c = b && consoles.find((x) => x.platform === b.dataset.read);
        if (c) read(c.stores.map((s) => s.id));
      };
      all.onclick = () => read(consoles.flatMap((c) => c.stores.filter((s) => !s.games).map((s) => s.id)));

      const follow = async () => {
        let j;
        do { await wait(1000); j = await gs.api("/plugin-api/minerva/stato"); if (el.isConnected) show(j); }
        while (j.status === "running");
        if (j.status !== "done") return gs.toast("Importazione degli store non riuscita", true);
        gs.toast("Store aggiunti alle console");
        if (el.isConnected) drawList();
      };
      btn.onclick = async () => { show(await gs.api("/plugin-api/minerva/importa", { body: {} })); follow(); };
      const j = await gs.api("/plugin-api/minerva/stato");
      show(j);
      if (j.status === "running") follow();
      drawList();
      if ((await gs.api("/api/store-refresh").catch(() => ({ job: {} }))).job.status === "running") followRead();
    },
  });
}
