// Riepilogo annuale: pagina con i numeri dell'anno e un'immagine da salvare.

const num = (n) => String(n).replace(".", ",");

/** Disegna la scheda 1080x1350 da condividere. */
function drawCard(d, css) {
  const c = document.createElement("canvas");
  c.width = 1080; c.height = 1350;
  const x = c.getContext("2d");
  const g = x.createLinearGradient(0, 0, 1080, 1350);
  g.addColorStop(0, css.bg); g.addColorStop(1, css.bg2);
  x.fillStyle = g; x.fillRect(0, 0, 1080, 1350);
  const font = "system-ui, -apple-system, 'Segoe UI', sans-serif";
  const text = (t, px, py, size, weight = 700, color = css.text, align = "left") => {
    x.font = `${weight} ${size}px ${font}`; x.fillStyle = color; x.textAlign = align; x.fillText(t, px, py);
  };
  text(`Il mio ${d.anno} in giochi`, 80, 140, 64, 800, css.accent);
  text(`${num(d.ore)} ore`, 80, 300, 150, 800);
  text(`${d.partite} partite · ${d.giorni} giorni di gioco · ${d.giochi} giochi`, 80, 360, 34, 500, css.muted);
  text("I più giocati", 80, 480, 40, 700, css.accent);
  d.top.forEach((t, i) => {
    const y = 550 + i * 78;
    text(`${i + 1}`, 80, y, 44, 800, css.muted);
    let title = t.titolo;
    x.font = `600 42px ${font}`;
    while (x.measureText(title).width > 720 && title.length > 4) title = title.slice(0, -2);
    text(title === t.titolo ? title : title + "…", 150, y, 42, 600);
    text(`${num(t.ore)} h`, 1000, y, 42, 700, css.muted, "right");
  });
  let y = 1010;
  const line = (label, value) => { text(label, 80, y, 30, 500, css.muted); text(value, 80, y + 46, 46, 700); y += 120; };
  if (d.console) line("Console preferita", `${d.console} · ${num(d.console_ore)} h`);
  if (d.mese) line("Mese più attivo", `${d.mese} · ${num(d.mese_ore)} h`);
  if (d.finiti) line("Giochi finiti", String(d.finiti));
  text("GameScraper", 1000, 1300, 30, 700, css.muted, "right");
  return c;
}

export default function (gs) {
  gs.addSidebarItem({ id: "riepilogo-annuale", label: "Il mio anno", icon: "🎁", view: "riepilogo-annuale" });

  gs.registerView("riepilogo-annuale", {
    title: "Il mio anno",
    async render(el) {
      el.innerHTML = `<p class="muted">Calcolo…</p>`;
      const show = async (anno) => {
        let d;
        try { d = await gs.api(`/plugin-api/riepilogo-annuale/anno${anno ? "?anno=" + anno : ""}`); } catch (e) { el.textContent = e.message; return; }
        const maxM = Math.max(1, ...d.per_mese);
        el.innerHTML = `<div class="ra-head"><label>Anno <select id="ra-y">${(d.anni.length ? d.anni : [d.anno]).map((y) =>
            `<option ${y === d.anno ? "selected" : ""}>${y}</option>`).join("")}</select></label>
            <button class="btn primary" id="ra-img" ${d.partite ? "" : "disabled"}>⬇ Salva come immagine</button></div>`
          + (d.partite ? `<div class="ra-cards">
              <div class="ra-card"><b>${num(d.ore)}</b><span>ore giocate</span></div>
              <div class="ra-card"><b>${d.giorni}</b><span>giorni di gioco</span></div>
              <div class="ra-card"><b>${d.giochi}</b><span>giochi diversi</span></div>
              <div class="ra-card"><b>${d.finiti}</b><span>giochi finiti</span></div></div>
            <h3>I più giocati</h3>
            <ol class="ra-top">${d.top.map((t) => `<li><b>${gs.esc(t.titolo)}</b> <span class="muted">${gs.esc(t.console)} · ${num(t.ore)} h · ${t.partite} partite</span></li>`).join("")}</ol>
            <p>Console preferita: <b>${gs.esc(d.console)}</b> (${num(d.console_ore)} h) · mese più attivo: <b>${d.mese}</b> · giorno preferito: <b>${d.giorno_settimana}</b>
              ${d.sessione_piu_lunga ? `<br>Sessione più lunga: <b>${num(d.sessione_piu_lunga.ore)} h</b> a ${gs.esc(d.sessione_piu_lunga.titolo)}` : ""}</p>
            <div class="ra-months">${d.per_mese.map((h, i) => `<div title="${num(h)} h"><i style="height:${Math.round((100 * h) / maxM)}%"></i><span>${"GFMAMGLASOND"[i]}</span></div>`).join("")}</div>`
            : `<p class="muted">Nessuna partita registrata nel ${d.anno}.</p>`);
        el.querySelector("#ra-y").onchange = (e) => show(+e.target.value);
        el.querySelector("#ra-img").onclick = () => {
          const cs = getComputedStyle(document.documentElement);
          const v = (n, d0) => cs.getPropertyValue(n).trim() || d0;
          const canvas = drawCard(d, { bg: v("--bg", "#151821"), bg2: v("--bg-3", "#232a3d"), text: v("--text", "#f2f4f8"),
            muted: v("--muted", "#9aa4b8"), accent: v("--accent", "#7c8cff") });
          const a = document.createElement("a");
          a.download = `il-mio-${d.anno}-in-giochi.png`;
          a.href = canvas.toDataURL("image/png");
          a.click();
        };
      };
      show(0);
    },
  });
}
