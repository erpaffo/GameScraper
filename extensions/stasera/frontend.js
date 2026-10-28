// Cosa gioco stasera?: una proposta dal backlog, filtrata per tempo, genere e console.
import { candidates, genresOf, pick } from "./logica.js";

const TIMES = [[0, "Quanto voglio"], [1, "Fino a 1 ora"], [3, "Fino a 3 ore"], [6, "Fino a 6 ore"], [12, "Fino a 12 ore"], [25, "Fino a 25 ore"]];

export default function (gs) {
  gs.addSidebarItem({ id: "stasera", label: "Cosa gioco stasera?", icon: "🎲", view: "stasera" });

  gs.registerView("stasera", {
    title: "Cosa gioco stasera?",
    async render(el) {
      const lib = gs.library();
      if (!lib) { el.innerHTML = `<p class="muted">Libreria non ancora caricata.</p>`; return; }
      const f = { hours: 0, genre: "", platform: "", unknown: false, started: false, ...gs.store.get("stasera", {}) };
      const seen = new Set();
      let cur = null;

      el.innerHTML = `<div class="ss-bar">
          <label>Tempo<select id="ss-h">${TIMES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label>
          <label>Genere<select id="ss-g"></select></label>
          <label>Console<select id="ss-p"></select></label>
          <label class="chk"><input type="checkbox" id="ss-u"> anche senza durata nota</label>
          <label class="chk"><input type="checkbox" id="ss-s"> anche quelli in corso</label>
        </div><div id="ss-out"></div>`;
      const $ = (s) => el.querySelector(s);

      const pool = () => candidates(lib.games, { ...f, statuses: f.started ? ["backlog", "", "playing"] : undefined });
      const fillSelects = () => {
        const base = candidates(lib.games, { statuses: f.started ? ["backlog", "", "playing"] : undefined });
        $("#ss-g").innerHTML = `<option value="">Tutti</option>` + genresOf(base).map((x) => `<option>${gs.esc(x)}</option>`).join("");
        const plats = [...new Set(base.filter((g) => !g.store).map((g) => g.platform))]
          .sort((a, b) => (lib.plats.get(a)?.name || a).localeCompare(lib.plats.get(b)?.name || b));
        $("#ss-p").innerHTML = `<option value="">Tutte</option>` + (base.some((g) => g.store) ? `<option value="pc">PC</option>` : "")
          + plats.map((p) => `<option value="${gs.esc(p)}">${gs.esc(lib.plats.get(p)?.name || p)}</option>`).join("");
        $("#ss-h").value = String(f.hours); $("#ss-g").value = f.genre; $("#ss-p").value = f.platform;
        $("#ss-u").checked = f.unknown; $("#ss-s").checked = f.started;
        if ($("#ss-g").value !== f.genre) f.genre = "";
        if ($("#ss-p").value !== f.platform) f.platform = "";
      };

      const draw = () => {
        const list = pool();
        const g = cur;
        if (!list.length) {
          $("#ss-out").innerHTML = `<p class="muted">Nessun gioco con questi filtri.${f.hours && !f.unknown
            ? " Prova ad accettare anche quelli senza durata nota." : ""}</p>`;
          return;
        }
        if (!g) {
          $("#ss-out").innerHTML = `<p class="muted">${list.length.toLocaleString("it")} giochi possibili.</p>
            <p><button class="btn primary" id="ss-go">🎲 Proponimi un gioco</button></p>`;
          return;
        }
        const hrs = g.hltb != null ? `⏱ circa ${String(Math.round(g.hltb * 10) / 10).replace(".", ",")} h` : "durata non nota";
        const last = g.grp.last ? `ultima volta il ${new Date(g.grp.last * 1000).toLocaleDateString("it")}` : "mai avviato";
        $("#ss-out").innerHTML = `<div class="ss-card">
            <img class="ss-cover" src="/img/${g.id}/cover?w=300" alt="" onerror="this.style.visibility='hidden'">
            <div class="ss-info"><h2>${gs.esc(g.title)}</h2>
              <p class="muted">${gs.esc(g.store ? g.store.toUpperCase() : lib.plats.get(g.platform)?.name || g.platform)}${g.year ? " · " + g.year : ""}
                ${g.genres ? " · " + gs.esc(g.genres) : ""}</p>
              <p>${hrs} · ${last}</p>
              <p class="ss-btns"><button class="btn primary" id="ss-play">▶ Avvia</button>
                <button class="btn" id="ss-next">🎲 Un altro</button>
                <button class="btn" id="ss-open">Dettagli</button></p>
              <p class="muted small">${list.length.toLocaleString("it")} giochi possibili · i meno giocati escono più spesso</p></div></div>`;
      };

      const next = () => {
        const list = pool();
        cur = pick(list, seen);
        if (!cur && list.length) { seen.clear(); cur = pick(list, seen); gs.toast("Li hai visti tutti: si riparte"); }
        if (cur) seen.add(cur.id);
        draw();
      };

      el.onchange = (e) => {
        f.hours = +$("#ss-h").value; f.genre = $("#ss-g").value; f.platform = $("#ss-p").value;
        f.unknown = $("#ss-u").checked;
        const started = $("#ss-s").checked;
        gs.store.set("stasera", f);
        seen.clear(); cur = null;
        if (started !== f.started) { f.started = started; gs.store.set("stasera", f); fillSelects(); }
        draw();
      };
      el.onclick = async (e) => {
        const b = e.target.closest("button");
        if (!b) return;
        if (b.id === "ss-go" || b.id === "ss-next") next();
        else if (b.id === "ss-open" && cur) gs.openGame(cur.id);
        else if (b.id === "ss-play" && cur) {
          b.disabled = true;
          try { await gs.api(`/api/game/${cur.id}/play`, { body: {} }); gs.toast(`Avvio di «${cur.title}»…`); } catch (err) { gs.toast(err.message, true); }
          b.disabled = false;
        }
      };
      fillSelects();
      draw();
    },
  });
}
