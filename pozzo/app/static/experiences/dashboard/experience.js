function renderDashboard(element, context) {
  const esc = GS.esc;
  const source = Array.isArray(context.state?.games) ? context.state.games : [];
  const games = source.filter((game) => {
    if (!game || game.id == null) return false;
    if (!game.grp) return true;
    const isPrimary = game.grp.primary == null || String(game.id) === String(game.grp.primary);
    return isPrimary && !game.grp.hidden;
  });
  const byId = new Map(games.map((game) => [String(game.id), game]));
  const titleOf = (game) => String(game.title || "Gioco senza titolo");
  const playedAt = (game) => Number(game.grp?.last || game.last || 0);
  const recent = games.filter((game) => playedAt(game) > 0).sort((a, b) => playedAt(b) - playedAt(a));
  const favorites = games.filter((game) => game.grp?.favorite || game.fav);
  const featured = recent[0] || favorites[0] || games[0] || null;
  const seen = new Set(featured ? [String(featured.id)] : []);
  const sections = [];

  const addSection = (label, items, view) => {
    const unique = items.filter((game) => {
      const id = String(game.id);
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    }).slice(0, 12);
    if (unique.length) sections.push({ label, items: unique, view });
  };

  addSection("Continua a giocare", recent, "all");
  addSection("I tuoi preferiti", favorites, "favorites");
  addSection("Dalla tua libreria", games, "all");

  const cover = (game, eager = false) => game.cover
    ? `<img data-dashboard-cover src="/img/${encodeURIComponent(String(game.id))}/cover?w=300" alt="" loading="${eager ? "eager" : "lazy"}" decoding="async">`
    : "";
  const pin = `<span class="gs-dashboard__pin" aria-hidden="true"><svg viewBox="0 0 16 16" focusable="false"><path d="M5 2.5h6l-.8 3.2 1.8 2v1H8.5v4.8l-.5.8-.5-.8V8.7H4v-1l1.8-2L5 2.5Z"/></svg></span>`;
  const tile = (game) => {
    const title = titleOf(game);
    return `<li><button class="gs-dashboard__tile" type="button" data-dashboard-game="${esc(String(game.id))}" aria-label="Apri ${esc(title)}">
      <span class="gs-dashboard__cover"><span class="gs-dashboard__cover-fallback" aria-hidden="true">${esc(title.slice(0, 1))}</span>${cover(game)}${pin}</span>
      <span class="gs-dashboard__tile-title">${esc(title)}</span>
      <span class="gs-dashboard__tile-meta">${esc(game.year || "Nella tua raccolta")}</span>
    </button></li>`;
  };

  element.innerHTML = `<div class="gs-dashboard">
    <aside class="gs-dashboard__guide" id="gs-dashboard-guide" role="region" aria-label="Guida rapida" aria-hidden="true" inert>
      <div class="gs-dashboard__guide-head">
        <h2>Guida rapida</h2>
        <button class="gs-dashboard__icon-button" type="button" data-dashboard-guide-close aria-label="Chiudi la guida">
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m5 5 10 10M15 5 5 15"/></svg>
        </button>
      </div>
      <p>Vai direttamente alla parte della raccolta che ti interessa.</p>
      <nav class="gs-dashboard__guide-nav" aria-label="Viste della raccolta">
        <button type="button" data-dashboard-view="home"><span>Home</span><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7 4 6 6-6 6"/></svg></button>
        <button type="button" data-dashboard-view="all"><span>Libreria</span><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7 4 6 6-6 6"/></svg></button>
        <button type="button" data-dashboard-view="favorites"><span>Preferiti</span><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7 4 6 6-6 6"/></svg></button>
        <button type="button" data-dashboard-view="shop"><span>Store</span><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7 4 6 6-6 6"/></svg></button>
      </nav>
    </aside>
    <div class="gs-dashboard__stage">
      <header class="gs-dashboard__header">
        <div>
          <h1>La tua raccolta</h1>
          <p>${games.length.toLocaleString("it")} ${games.length === 1 ? "gioco" : "giochi"} · Tutto quello che hai voglia di giocare, a portata di mano.</p>
        </div>
        <button class="gs-dashboard__guide-toggle" type="button" data-dashboard-guide-toggle aria-controls="gs-dashboard-guide" aria-expanded="false" aria-label="Apri la guida rapida">
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M3 5h14M3 10h14M3 15h14"/></svg>
          <span data-dashboard-guide-label>Apri guida</span>
        </button>
      </header>
      ${featured ? `<section class="gs-dashboard__hero" aria-labelledby="gs-dashboard-featured-title">
        <div class="gs-dashboard__hero-art" aria-hidden="true">
          <span class="gs-dashboard__hero-fallback"></span>${cover(featured, true)}
        </div>
        <div class="gs-dashboard__hero-copy">
          <h2 id="gs-dashboard-featured-title">${esc(titleOf(featured))}</h2>
          <p class="gs-dashboard__hero-meta">${featured.year ? `Anno ${esc(String(featured.year))} · ` : ""}Apri la scheda per ripartire.</p>
          <div class="gs-dashboard__hero-actions">
            <button class="gs-dashboard__primary-button" type="button" data-dashboard-game="${esc(String(featured.id))}">
              Apri gioco
              <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7 4 6 6-6 6"/></svg>
            </button>
            <button class="gs-dashboard__text-button" type="button" data-dashboard-view="all">Vai alla libreria</button>
          </div>
        </div>
      </section>` : `<section class="gs-dashboard__hero gs-dashboard__hero--empty" aria-labelledby="gs-dashboard-empty-title">
        <div class="gs-dashboard__empty-art" aria-hidden="true">
          <svg viewBox="0 0 180 140" focusable="false"><rect x="22" y="26" width="82" height="106" rx="8"/><rect x="76" y="10" width="82" height="106" rx="8"/><path d="M95 53h44M95 68h34M41 83h37M41 98h37"/></svg>
        </div>
        <div class="gs-dashboard__hero-copy">
          <h2 id="gs-dashboard-empty-title">La tua prossima partita inizia qui.</h2>
          <p>Aggiungi giochi alla libreria per ritrovarli tutti in un unico posto.</p>
          <div class="gs-dashboard__hero-actions">
            <button class="gs-dashboard__primary-button" type="button" data-dashboard-view="all">Apri la libreria</button>
          </div>
        </div>
      </section>`}
      ${sections.map((section, index) => `<section class="gs-dashboard__section" aria-labelledby="gs-dashboard-section-${index}">
        <div class="gs-dashboard__section-head">
          <div><h2 id="gs-dashboard-section-${index}">${esc(section.label)}</h2><span>${section.items.length} ${section.items.length === 1 ? "gioco" : "giochi"}</span></div>
          <button type="button" class="gs-dashboard__section-link" data-dashboard-view="${section.view}">Vedi tutto</button>
        </div>
        <ul class="gs-dashboard__tiles" aria-label="${esc(section.label)}">${section.items.map(tile).join("")}</ul>
      </section>`).join("")}
    </div>
  </div>`;

  const dashboard = element.querySelector(".gs-dashboard");
  const guide = dashboard.querySelector(".gs-dashboard__guide");
  const guideToggle = dashboard.querySelector("[data-dashboard-guide-toggle]");
  const guideLabel = dashboard.querySelector("[data-dashboard-guide-label]");
  let guideOpen = false;

  const closeGuide = (restoreFocus = true) => {
    if (!guideOpen) return;
    guideOpen = false;
    dashboard.classList.remove("gs-dashboard--guide-open");
    guide.setAttribute("aria-hidden", "true");
    guide.inert = true;
    guideToggle.setAttribute("aria-expanded", "false");
    guideToggle.setAttribute("aria-label", "Apri la guida rapida");
    guideLabel.textContent = "Apri guida";
    if (restoreFocus) guideToggle.focus();
  };

  const openGuide = () => {
    guideOpen = true;
    dashboard.classList.add("gs-dashboard--guide-open");
    guide.setAttribute("aria-hidden", "false");
    guide.inert = false;
    guideToggle.setAttribute("aria-expanded", "true");
    guideToggle.setAttribute("aria-label", "Chiudi la guida rapida");
    guideLabel.textContent = "Chiudi guida";
    guide.querySelector("[data-dashboard-guide-close]")?.focus();
  };

  dashboard.onclick = (event) => {
    const toggle = event.target.closest("[data-dashboard-guide-toggle]");
    if (toggle) {
      if (guideOpen) closeGuide(); else openGuide();
      return;
    }
    if (event.target.closest("[data-dashboard-guide-close]")) {
      closeGuide();
      return;
    }
    const viewButton = event.target.closest("[data-dashboard-view]");
    if (viewButton) {
      const type = viewButton.dataset.dashboardView;
      closeGuide(false);
      if (typeof context.setView === "function") context.setView({ type });
      return;
    }
    const gameButton = event.target.closest("[data-dashboard-game]");
    if (gameButton) {
      const game = byId.get(gameButton.dataset.dashboardGame);
      if (game) GS.openGame(game.id);
    }
  };

  dashboard.onkeydown = (event) => {
    if (event.key === "Escape" && guideOpen) {
      event.preventDefault();
      closeGuide();
    }
  };

  dashboard.querySelectorAll("[data-dashboard-cover]").forEach((image) => {
    image.addEventListener("error", () => image.remove());
  });
}

GS.registerExperience({
  id: "dashboard",
  name: "Dashboard",
  description: "Una home dashboard per riprendere i giochi e sfogliare la raccolta.",
  nav: "top",
  home: renderDashboard,
  card: null,
  detail: "panel",
  onFocus: null,
});
