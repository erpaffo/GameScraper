/* Barra dei menu in alto (File, Strumenti, Visualizza, Impostazioni): scorciatoie per le funzioni e le
   impostazioni piu' usate. Usa le funzioni di app.js (script classico, stesso ambito globale); le
   impostazioni complete restano nella finestra Impostazioni. */
(() => {
  const click = (sel) => document.querySelector(sel)?.click();
  const setSelect = (sel, v) => { const el = document.querySelector(sel); el.value = v; el.dispatchEvent(new Event("change")); };
  const scan = async (mode) => {
    try { await api("/api/scan", { body: { mode } }); } catch (e) { return toast(e.message, true); }
    toast(mode === "all" ? "Ricerca dei metadati di tutti i giochi avviata…" : "Cerco i giochi nuovi…");
    SET.lastStatus = "running";
    pollScan();
  };
  const theme = async (id) => { await api("/api/app/theme", { body: { theme: id } }); await loadExtensions(); };
  const sorts = [["title", "Titolo A–Z"], ["-year", "Anno ↓"], ["-last", "Ultimi giocati"], ["-playtime", "Tempo di gioco"], ["-rating", "Valutazione"]];
  const groups = [["", "Nessun gruppo"], ["platform", "Per console"], ["year", "Per anno"], ["letter", "Per lettera"]];

  // "-" = separatore, {h} = titolo di gruppo, chk() = segno di spunta (puo' essere asincrono)
  const MENUS = {
    File: () => [
      { l: "＋ Aggiungi gioco manualmente…", f: () => openAddGame("game") },
      { l: "🧩 Aggiungi applicazione…", f: () => openAddGame("app") },
      "-",
      { l: "Esporta libreria (backup)", f: async () => { const r = await api("/api/backups", { body: {} }); toast(`Backup creato: ${r.name}`); } },
      { l: "Importa libreria (ripristina backup)…", f: () => openSettings("lib") },
      "-",
      { l: "↻ Ricarica libreria", f: () => reload(true) },
      { l: "Aggiorna gli store", f: async () => { await api("/api/stores/refresh", { body: {} }); toast("Aggiornamento degli store avviato"); } },
      "-",
      { l: "Esci", f: async () => { if (confirm("Chiudere Pozzo Launcher?")) { try { await api("/api/app/quit", { body: {} }); } catch { /* il server si chiude */ } window.close(); } } },
    ],
    Strumenti: () => [
      { l: "⟳ Cerca nuovi giochi", f: () => scan("new") },
      { l: "Forza la ricerca dei metadati (tutti i giochi)", f: () => confirm("Cercare di nuovo i metadati di tutta la libreria? Può richiedere molto tempo.") && scan("all") },
      { l: "Cerca solo i dati mancanti", f: () => scan("missing") },
      "-",
      { l: "Pulisci la cache delle immagini", f: async () => { const r = await api("/api/cache/thumbs/clear", { body: {} }); toast(`Miniature eliminate: ${r.removed}. Si rigenerano da sole.`); } },
      { l: "Ricollega le cartelle spostate…", f: () => openSettings("scan") },
      { l: "Rimuovi doppioni aggiunti a mano", f: async () => { if (!confirm("Rimuovere dalla libreria i giochi aggiunti a mano più di una volta? I file sul disco non vengono toccati.")) return; const r = await api("/api/manual/dedupe", { body: {} }); toast(`Doppioni rimossi: ${r.removed}`); await reload(true); } },
    ],
    Visualizza: () => [
      { h: "Ordina per" },
      ...sorts.map(([v, l]) => ({ l, chk: () => S.sort === v, f: () => setSelect("#sort", v) })),
      { h: "Raggruppa" },
      ...groups.map(([v, l]) => ({ l, chk: () => ($("#group").value || "") === v, f: () => setSelect("#group", v) })),
      "-",
      { l: "Nascondi duplicati", chk: () => $("#one-version").checked, f: () => click("#one-version") },
      { l: "Copertine più grandi", f: () => { const z = $("#zoom"); z.value = Math.min(+z.max, +z.value + 30); z.dispatchEvent(new Event("input")); } },
      { l: "Copertine più piccole", f: () => { const z = $("#zoom"); z.value = Math.max(+z.min, +z.value - 30); z.dispatchEvent(new Event("input")); } },
      "-",
      { l: "Tema chiaro", chk: async () => (await api("/api/extensions")).active_theme === "chiaro",
        f: async () => theme((await api("/api/extensions")).active_theme === "chiaro" ? "default" : "chiaro") },
      { l: "🎮 Big Picture", f: () => enterBigPicture() },
    ],
    Impostazioni: () => [
      { l: "⚙ Tutte le impostazioni…", f: () => openSettings() },
      "-",
      { l: "Cartelle da scansionare…", f: () => openSettings("scan") },
      { l: "Account e store…", f: () => openSettings("acc") },
      { l: "Emulatori…", f: () => openSettings("emu") },
      { l: "Aspetto e temi…", f: () => openSettings("look") },
      "-",
      { l: "Notifiche", chk: async () => (await api("/api/app/notify")).enabled,
        f: async () => { const on = (await api("/api/app/notify")).enabled; await api("/api/app/notify", { body: { enabled: !on } }); toast(on ? "Notifiche disattivate" : "Notifiche attive"); } },
    ],
  };

  const bar = document.createElement("nav");
  bar.id = "menubar";
  bar.innerHTML = Object.keys(MENUS).map((k) => `<button type="button" class="mb-top" data-m="${k}">${k}</button>`).join("");
  document.getElementById("main").prepend(bar);

  // Attivita': tutte le operazioni in sottofondo (pannello qui sotto o finestra separata)
  bar.insertAdjacentHTML("beforeend", `<button type="button" id="mb-act" title="Operazioni in corso">⚙ Attività <span id="mb-act-n"></span></button>`);
  const actBtn = document.getElementById("mb-act");
  const popOut = () => (window.pywebview?.api?.open_activity ? window.pywebview.api.open_activity()
    : window.open("/activity", "pozzo-activity", "popup,width=520,height=640"));
  actBtn.onclick = () => {
    const pan = document.getElementById("act-pop");
    if (pan) return pan.remove();
    document.body.insertAdjacentHTML("beforeend", `<div id="act-pop">
      <div class="ah"><b>Attività</b><span><button type="button" id="act-out" title="Apri in una finestra separata">⧉ Finestra separata</button>
      <button type="button" id="act-x" title="Chiudi">✕</button></span></div><iframe src="/activity"></iframe></div>`);
    document.getElementById("act-out").onclick = () => { document.getElementById("act-pop").remove(); popOut(); };
    document.getElementById("act-x").onclick = () => document.getElementById("act-pop").remove();
  };
  document.addEventListener("mousedown", (e) => { if (!e.target.closest("#act-pop, #mb-act")) document.getElementById("act-pop")?.remove(); });
  const count = async () => {
    try {
      const n = (await api("/api/activity")).jobs.filter((j) => j.status === "running").length;
      document.getElementById("mb-act-n").textContent = n || "";
      actBtn.classList.toggle("busy", n > 0);
    } catch { /* riprova al prossimo giro */ }
  };
  count();
  setInterval(count, 3000);

  let open = null;
  const close = () => { document.getElementById("mb-drop")?.remove(); bar.querySelectorAll(".on").forEach((b) => b.classList.remove("on")); open = null; };

  async function show(btn) {
    close();
    open = btn.dataset.m;
    btn.classList.add("on");
    const rows = MENUS[open]();
    const marks = await Promise.all(rows.map((r) => (r.chk ? Promise.resolve().then(r.chk).catch(() => false) : false)));
    if (open !== btn.dataset.m) return; // nel frattempo si e' aperto un altro menu
    const d = document.createElement("div");
    d.id = "mb-drop";
    d.innerHTML = rows.map((r, i) => r === "-" ? "<hr>" : r.h ? `<div class="ch">${esc(r.h)}</div>`
      : `<div class="ci" data-i="${i}"><span class="mk">${marks[i] ? "✓" : ""}</span>${esc(r.l)}</div>`).join("");
    document.body.appendChild(d);
    const b = btn.getBoundingClientRect();
    d.style.left = Math.max(4, Math.min(b.left, innerWidth - d.offsetWidth - 8)) + "px";
    d.style.top = b.bottom + "px";
    d.onclick = async (ev) => {
      const it = ev.target.closest(".ci");
      if (!it) return;
      const f = rows[+it.dataset.i].f;
      close();
      try { await f(); } catch (e) { toast(e.message, true); }
    };
  }

  bar.addEventListener("click", (e) => {
    const b = e.target.closest(".mb-top");
    if (b) open === b.dataset.m ? close() : show(b);
  });
  bar.addEventListener("mouseover", (e) => {  // con un menu aperto basta passare sopra un altro per aprirlo
    const b = e.target.closest(".mb-top");
    if (b && open && open !== b.dataset.m) show(b);
  });
  document.addEventListener("mousedown", (e) => { if (open && !e.target.closest("#menubar, #mb-drop")) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && open) close(); });
})();
