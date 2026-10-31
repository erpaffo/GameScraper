(() => {
  const esc = (v) => GS.esc(v == null ? "" : v);
  const cover = (g) => `/img/${encodeURIComponent(g.id)}/cover?w=300`;
  const genresOf = (g) => String(g.genres || "").split(", ").map((x) => x.trim()).filter(Boolean);

  const card = (g) => `<button type="button" class="cnx-card" data-cnx-id="${esc(g.id)}" aria-label="${esc(g.title)}">`
    + `<img src="${cover(g)}" alt="" loading="lazy" onerror="this.remove()">`
    + `<span class="cnx-card-t">${esc(g.title)}</span></button>`;

  const rail = (title, items) => items.length
    ? `<section class="cnx-row" aria-label="${esc(title)}"><h2 class="cnx-h">${esc(title)}</h2>`
      + `<div class="cnx-rail" role="list">${items.map((g) => `<div role="listitem">${card(g)}</div>`).join("")}</div></section>`
    : "";

  function home(element, context) {
    const all = (context.state?.games || []).filter((g) => g.grp && g.id === g.grp.primary && !g.grp.hidden);
    if (!all.length) {
      element.innerHTML = `<div class="cnx"><div class="cnx-empty"><h1>Nessun gioco</h1><p>Aggiungi una libreria per vedere qui i tuoi titoli.</p></div></div>`;
      return;
    }
    const played = all.filter((g) => g.grp.last > 0).sort((a, b) => b.grp.last - a.grp.last);
    const recent = all.filter((g) => g.year).sort((a, b) => b.year - a.year).slice(0, 30);
    const hero = played[0] || recent[0] || all[0];
    const byGenre = new Map();
    for (const g of all) for (const n of genresOf(g)) (byGenre.get(n) || byGenre.set(n, []).get(n)).push(g);
    const genres = [...byGenre].sort((a, b) => b[1].length - a[1].length).slice(0, 6);

    element.innerHTML = `<div class="cnx">`
      + `<header class="cnx-hero" style="--cnx-img:url('${cover(hero)}')"><div class="cnx-hero-body">`
      + `<h1>${esc(hero.title)}</h1>`
      + `<p class="cnx-meta">${esc([hero.year, genresOf(hero).slice(0, 3).join(" · ")].filter(Boolean).join(" · "))}</p>`
      + `<button type="button" class="cnx-play" data-cnx-id="${esc(hero.id)}">▶ Apri</button></div></header>`
      + rail("Continua a giocare", played.slice(0, 30))
      + genres.map(([n, list]) => rail(n, list.slice(0, 30))).join("")
      + rail("Recenti", recent)
      + `</div>`;

    element.querySelector(".cnx")?.addEventListener("click", (e) => {
      const b = e.target.closest("[data-cnx-id]");
      if (b) GS.openGame(b.dataset.cnxId);
    });
  }

  GS.registerExperience({
    id: "cinema",
    name: "Cinema",
    description: "Home cinematografica: grande banner d'apertura e righe orizzontali di giochi.",
    nav: "top",
    home,
    card: null,
    detail: "modal",
    onFocus: null,
  });
})();