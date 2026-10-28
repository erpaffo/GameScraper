// SteamGridDB: scheda nelle impostazioni per la chiave dell'API.

export default function (gs) {
  async function draw(el) {
    let d;
    try { d = await gs.api("/plugin-api/steamgriddb/stato"); } catch (e) { el.textContent = e.message; return; }
    el.innerHTML = `<h3>SteamGridDB</h3>
      <p class="muted">Aggiunge copertine verticali, sfondi e logo ai giochi a cui le altre fonti non li hanno trovati.
        La chiave è gratuita: <a href="#" data-url="https://www.steamgriddb.com/profile/preferences/api">steamgriddb.com › Preferenze › API</a>.
        La fonte si accoda da sola alle altre; per usarla servono i tipi di media <b>cover</b>, <b>fanart</b> o <b>wheel</b> attivi.</p>
      <p>${d.chiave ? "✔ Chiave impostata." : "Nessuna chiave impostata."}</p>
      <p><input type="password" id="sg-k" placeholder="Incolla la chiave" autocomplete="off" style="width:320px">
        <button class="btn primary" id="sg-save">Salva</button>
        ${d.chiave ? `<button class="btn" id="sg-del">Rimuovi</button>` : ""}</p>`;
    el.querySelector("a[data-url]").onclick = (e) => { e.preventDefault(); gs.api("/api/open", { body: { url: e.target.dataset.url } }); };
    const set = async (chiave) => {
      try { await gs.api("/plugin-api/steamgriddb/chiave", { body: { chiave } }); gs.toast("Salvato"); } catch (e) { return gs.toast(e.message, true); }
      draw(el);
    };
    el.querySelector("#sg-save").onclick = () => set(el.querySelector("#sg-k").value);
    el.querySelector("#sg-del")?.addEventListener("click", () => set(""));
  }
  gs.addSettingsTab({ id: "steamgriddb", label: "SteamGridDB", render: draw });
}
