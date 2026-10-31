"use strict";
/* Stile TV "Classico": hero + scaffali orizzontali, pagina del gioco con copertina. Gli altri stili
 * ereditano da qui ogni pezzo che non ridefiniscono. Le funzioni usano gli helper di bigpicture.js. */
(() => {
  function classicCard(g, i, extra = "", score = "") {
    const tag = g.store ? (STORE_SHORT[g.store] || "PC") : (SHORT[g.platform] || g.platform.toUpperCase());
    return `<div class="card${g.store && !g.installed ? " notinst" : ""}" data-i="${i}" ${extra}>`
      + `<div class="cv" style="--h:${hueOf(g.title)}"><div class="ph">${esc(g.title)}</div>`
      + (g.cover ? `<img loading="lazy" decoding="async" src="/img/${g.id}/cover?w=300" onerror="this.remove()">` : "")
      + `<span class="tag">${esc(tag)}</span>${score ? `<span class="score">${esc(score)}</span>` : ""}</div><div class="lbl">${esc(g.title)}</div></div>`;
  }

  function classicTextCard(label, sub, i, hueKey, img, it = {}) {
    if (it.art) {  // collezione: copertina a tutto riquadro + logo
      return `<div class="card tile-text coll-card" data-i="${i}"><div class="cv" style="--h:${hueOf(hueKey || label)}">`
        + `<img class="art" src="${it.art}" alt="" loading="lazy" onerror="this.remove()">`
        + (it.icon ? `<img class="logo" src="${it.icon}" alt="" loading="lazy" onerror="this.remove()">` : "")
        + `<div class="txt">${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</div></div></div>`;
    }
    return `<div class="card tile-text${img ? " with-img" : ""}" data-i="${i}"><div class="cv" style="--h:${hueOf(hueKey || label)}">`
      + (img ? `<img class="sys" src="${img}" alt="" loading="lazy" onerror="this.remove()">` : "")
      + `<div class="txt">${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</div></div></div>`;
  }

  function classicNav() {
    const s = cur();
    $("#bp-nav").innerHTML = TABS.map((t, i) => `<span data-tab="${i}" class="bp-tab${s.tab === t.id ? " active" : ""}${s.navFocus === i ? " focus" : ""}">${esc(t.label)}</span>`).join("");
  }

  /* ---- home: scaffali orizzontali */
  function classicHome() {
    const games = visibleGames();
    const recent = games.filter((g) => g.grp.last).sort((a, b) => b.grp.last - a.grp.last).slice(0, 30);
    const favs = sortTitle(games.filter((g) => g.grp.favorite)).slice(0, 40);
    const pcInst = sortTitle(games.filter((g) => g.store && g.installed)).slice(0, 40);
    const counts = new Map();
    for (const g of games) for (const i of g.grp.colls) counts.set(i, (counts.get(i) || 0) + 1);
    const topColls = BP.lib.colls.filter((c) => !c.hidden && (counts.get(c.idx) || 0) >= 4)
      .sort((a, b) => (counts.get(b.idx) || 0) - (counts.get(a.idx) || 0)).slice(0, 6);
    const shelves = [];
    if (recent.length) shelves.push({ title: "Continua a giocare", items: recent });
    if (favs.length) shelves.push({ title: "Preferiti", items: favs });
    if (pcInst.length) shelves.push({ title: "Giochi PC installati", items: pcInst });
    for (const id of ["loved", "critics", "gems"]) {
      const d = discover(games, { preset: id });
      if (d.items.length >= 5) shelves.push({ title: d.preset.label, items: d.items.slice().sort(d.cmp).slice(0, 30),
        more: () => { store.set("bpDisc", id); push(discoverScreen("Libreria", sortTitle(games), "all", id)); } });
    }
    const shop = shopGames(BP.lib.games);
    if (shop.length) shelves.push({ title: "Store · da installare", sub: `${shop.length} giochi`,
      items: shopFilter(shop, { sort: "-rating" }).slice(0, 40), more: () => push(shopScreen()) });
    for (const c of topColls) {
      const items = games.filter((g) => g.grp.colls.includes(c.idx)).sort((a, b) => (a.year || 9999) - (b.year || 9999));
      shelves.push({ title: c.name, sub: `${items.length} giochi`, items: items.slice(0, 40), more: () => push(gridScreen(c.name, items, "coll")) });
    }
    const plats = [...BP.lib.plats.values()].filter((p) => p.id !== "windows").sort((a, b) => collator.compare(a.name, b.name));
    const stores = BP.lib.stores.filter((s) => games.some((g) => g.store === s.id));
    if (stores.length) shelves.push({ title: "Store", text: true, items: stores.map((s) => ({ label: s.name, key: s.id,
      sub: `${games.filter((g) => g.store === s.id && g.installed).length} installati / ${games.filter((g) => g.store === s.id).length}`,
      img: `/icons/store/${s.id}`,
      ok: () => push(gridScreen(s.name, sortTitle(games.filter((g) => g.store === s.id)), "pc")) })) });
    shelves.push({ title: "Console", text: true, items: plats.map((p) => ({ label: p.name, sub: `${p.count} giochi`, key: p.id,
      img: `/icons/platform/${encodeURIComponent(p.id)}`,
      ok: () => push(gridScreen(p.name, sortTitle(games.filter((g) => g.platform === p.id)), "plat")) })) });
    if (!shelves.length || shelves.every((s) => !s.items.length)) shelves.unshift({ title: "Libreria", items: sortTitle(games).slice(0, 40) });

    return shelvesScreen(shelves, "home");
  }

  function classicShelves(shelves, tab) {
    const scr = {
      tab, row: 0, cols: shelves.map(() => 0), navFocus: null, shelves,
      render(el) {
        el.innerHTML = `<div class="bp-home"><div class="bp-hero" id="hero"></div><div class="bp-shelves"><div class="bp-shelves-inner" id="shelves">`
          + shelves.map((sh, r) => `<section class="shelf" data-r="${r}"><h2>${esc(sh.title)}${sh.sub ? `<span>${esc(sh.sub)}</span>` : ""}</h2>`
            + `<div class="shelf-row">${sh.items.map((it, i) => (sh.text ? textCard(it.label, it.sub, i, it.key, it.img, it) : card(it, i))).join("")}</div></section>`).join("")
          + `</div></div></div>`;
        this.update();
      },
      current() { const sh = shelves[this.row]; return sh && !sh.text ? sh.items[this.cols[this.row]] : null; },
      update() {
        document.querySelectorAll(".card.focus").forEach((c) => c.classList.remove("focus"));
        renderNav();
        if (this.navFocus !== null) return;
        const sec = document.querySelector(`.shelf[data-r="${this.row}"]`);
        if (!sec) return;
        const c = sec.querySelectorAll(".card")[this.cols[this.row]];
        c?.classList.add("focus");
        // scorrimento: scaffale corrente in alto, carta corrente visibile
        $("#shelves").style.transform = `translateY(${-sec.offsetTop}px)`;
        const row = sec.querySelector(".shelf-row");
        const off = c ? Math.max(0, c.offsetLeft - row.clientWidth * 0.3) : 0;
        row.style.transform = `translateX(${-off}px)`;
        const g = this.current();
        const hero = $("#hero");
        if (g) {
          hero.innerHTML = `<h1>${esc(g.title)}</h1><div class="meta">${esc([g.store ? STORE_SHORT[g.store] : platName(g.platform), g.year, g.dev].filter(Boolean).join("  ·  "))}</div>`;
          setBackground(g.id);
          detail(g.id).then((d) => {
            if (d && d.media.logo && this.current() === g) hero.innerHTML = `<img class="logo-img" src="${d.media.logo}?w=700" onerror="this.outerHTML='<h1>${esc(g.title).replace(/'/g, "")}</h1>'">`
              + `<div class="meta">${esc([d.store_name || d.platform_name, d.year, d.developer].filter(Boolean).join("  ·  "))}</div>`;
          });
        } else {
          const it = shelves[this.row].items[this.cols[this.row]];
          hero.innerHTML = it ? `<h1>${esc(it.label)}</h1><div class="meta">${esc(it.sub || "")}</div>` : "";
          setBackground(null);
        }
      },
      move(dx, dy) {
        if (this.navFocus !== null) {
          if (dy > 0) { this.navFocus = null; this.update(); return true; }
          const n = this.navFocus + dx;
          if (n < 0 || n >= TABS.length) return false;
          this.navFocus = n; renderNav(); return true;
        }
        if (dy) {
          const r = this.row + dy;
          if (r < 0) { this.navFocus = 0; this.update(); return true; }
          if (r >= shelves.length) return false;
          this.row = r;
        } else {
          const c = this.cols[this.row] + dx;
          if (c < 0 || c >= shelves[this.row].items.length) return false;
          this.cols[this.row] = c;
        }
        this.update(); return true;
      },
      jump(d) { this.cols[this.row] = Math.max(0, Math.min(shelves[this.row].items.length - 1, this.cols[this.row] + d * 6)); this.update(); },
      ok() {
        if (this.navFocus !== null) { openTab(this.navFocus); return; }
        const sh = shelves[this.row], it = sh.items[this.cols[this.row]];
        if (!it) return;
        if (sh.text) it.ok(); else push(gameScreen(it.id));
      },
      fav() { const g = this.current(); if (g) toggleFav(g); },
      hints: [["a", "Apri"], ["y", "Cerca"], ["x", "Preferito"]],
    };
    return scr;
  }

  /* ---- pagina del gioco */
  function classicGame(id) {
    const scr = {
      tab: null, d: null, i: 0, buttons: [], navFocus: null,
      async render(el) {
        el.innerHTML = `<div class="bp-game"><div class="big-cover"></div><div class="info"><h1>…</h1></div></div>`;
        const d = this.d || await detail(id);
        if (!d) { toast("Gioco non trovato", true); pop(); return; }
        this.d = d;
        setBackground(id);
        const pc = !!d.store;
        this.buttons = [];
        if (pc && !d.installed) this.buttons.push({ label: "⬇ Installa", cls: "main", run: () => installGame(d) });
        else this.buttons.push({ label: "▶ Gioca", cls: "main", run: () => playGame(d), disabled: !d.emulator });
        const stOrder = ["", "backlog", "playing", "done", "dropped"];
        const st = d.user.status || "";
        if (!d.store || d.store !== "online") this.buttons.push({ label: st ? `${STATUS[st].icon} ${STATUS[st].label}` : "📋 Stato",
          cls: st ? "on" : "", run: async () => {
            const next = stOrder[(stOrder.indexOf(st) + 1) % stOrder.length];
            try { await api(`/api/game/${id}/flag`, { body: { field: "status", value: next } }); } catch (e) { return toast(e.message, true); }
            const g = BP.lib.byId.get(id);
            if (g) g.grp.status = next;
            BP.details.delete(id);
            toast(next ? `Stato: ${STATUS[next].label}` : "Stato tolto");
            const i = this.i; this.d = null; await this.render(main); this.i = i; this.update();
          } });
        this.buttons.push({ label: d.user.favorite ? "★ Preferito" : "☆ Preferito", cls: d.user.favorite ? "on" : "", run: () => toggleFav(BP.lib.byId.get(id)) });
        if (d.user.last_played) this.buttons.push({ label: "✖ Togli dai recenti", run: async () => {
          try { await api(`/api/game/${id}/flag`, { body: { field: "last_played", value: null } }); } catch (e) { return toast(e.message, true); }
          const g = BP.lib.byId.get(id);
          if (g) g.grp.last = 0;
          BP.details.delete(id);
          toast("Tolto da «Continua a giocare»");
          const i = this.i; this.d = null; await this.render(main); this.i = Math.min(i, this.buttons.length - 1); this.update();
        } });
        if (d.versions.length > 1) this.buttons.push({ label: `Versioni (${d.versions.length})`, run: () => push(versionsScreen(d)) });
        if (d.shots) this.buttons.push({ label: `📷 Schermate (${d.shots})`, run: () => push(shotsScreen(d)) });
        this.buttons.push({ label: "Indietro", run: () => pop() });
        const facts = [["Uscita", d.release_date || d.year], ["Sviluppatore", d.developer], ["Generi", (d.genres || []).join(", ")],
          ["Giocatori", d.players_local],
          ["Durata", d.hltb ? [d.hltb.main && `storia ${fmtHours(d.hltb.main)}`, d.hltb.complete && `100% ${fmtHours(d.hltb.complete)}`].filter(Boolean).join(" · ") : ""], ["Tempo di gioco", fmtTime(d.user.playtime || d.store_playtime)],
          ["Dimensione", fmtSize(d.size)],
          ["Valutazione", d.rating != null ? `${(d.rating * 5).toFixed(1)} / 5${d.rating_count ? ` (${d.rating_count.toLocaleString("it")} voti)` : ""}` : ""],
          ["Critica", d.rating_critics != null ? `${Math.round(d.rating_critics * 100)} / 100` : ""]].filter(([, v]) => v);
        el.innerHTML = `<div class="bp-game"><div class="big-cover">${d.media.cover ? `<img src="${d.media.cover}?w=700">` : ""}</div>
          <div class="info">${d.media.logo ? `<img class="logo-img" src="${d.media.logo}?w=700" onerror="this.replaceWith(Object.assign(document.createElement('h1'),{textContent:${JSON.stringify(d.title)}}))">` : `<h1>${esc(d.title)}</h1>`}
          <div class="meta">${esc([pc ? d.store_name : d.platform_name, d.year].filter(Boolean).join("  ·  "))}
            ${pc ? (d.installed ? " · installato" : " · non installato") : d.emulator ? " · " + esc(d.emulator) : " · nessun emulatore configurato"}</div>
          <div class="bp-actions">${this.buttons.map((b, i) => `<div class="bp-btn ${b.cls || ""}${b.disabled ? " disabled" : ""}" data-i="${i}">${esc(b.label)}</div>`).join("")}</div>
          <div class="bp-facts">${facts.map(([k, v]) => `<span>${k}: <b>${esc(v)}</b></span>`).join("")}</div>
          ${d.description ? `<div class="bp-desc">${esc(d.description)}</div>` : ""}</div></div>`;
        this.update();
      },
      update() {
        renderNav();
        document.querySelectorAll(".bp-btn").forEach((b) => b.classList.toggle("focus", +b.dataset.i === this.i && this.navFocus === null));
      },
      move(dx, dy) {
        if (this.navFocus !== null) {
          if (dy > 0) { this.navFocus = null; this.update(); return true; }
          const n = this.navFocus + dx;
          if (n < 0 || n >= TABS.length) return false;
          this.navFocus = n; renderNav(); return true;
        }
        if (dy < 0) { this.navFocus = 0; this.update(); return true; }
        const n = this.i + dx;
        if (n < 0 || n >= this.buttons.length) return false;
        this.i = n; this.update(); return true;
      },
      ok() {
        if (this.navFocus !== null) { openTab(this.navFocus); return; }
        const b = this.buttons[this.i];
        if (b && !b.disabled) b.run(); else if (b?.disabled) { beep("err"); toast("Nessun emulatore configurato per questa console (Impostazioni › Emulatori)", true); }
      },
      fav() { toggleFav(BP.lib.byId.get(id)); },
      hints: [["a", "Seleziona"], ["b", "Indietro"], ["x", "Preferito"]],
    };
    return scr;
  }
  BPLayouts.classic = { label: "Classico", home: classicHome, shelves: classicShelves, card: classicCard, textCard: classicTextCard, game: classicGame, nav: classicNav };
})();
