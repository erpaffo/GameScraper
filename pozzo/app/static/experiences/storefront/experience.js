(function () {
  "use strict";

  const escapeHtml = (value) => GS.esc(String(value ?? ""));
  const hasValue = (value) => value !== null && value !== undefined && String(value).trim() !== "";
  const valueOf = (game, key) => hasValue(game[key]) ? String(game[key]).trim() : "";

  function coverMarkup(game, className) {
    const title = valueOf(game, "title");
    const id = encodeURIComponent(String(game.id));
    const initial = title.slice(0, 1).toLocaleUpperCase("it");
    return `<span class="${className}">
      <span class="vetrina-cover__fallback" aria-hidden="true">${escapeHtml(initial)}</span>
      <img src="/img/${escapeHtml(id)}/cover?w=300" alt="" loading="lazy" data-vetrina-cover>
    </span>`;
  }

  function gameCard(game) {
    const title = valueOf(game, "title");
    const platform = valueOf(game, "platform");
    const year = valueOf(game, "year");
    const facts = [platform, year].filter(Boolean);
    return `<button class="vetrina-game" type="button" data-vetrina-game="${escapeHtml(game.id)}"
        data-vetrina-card data-vetrina-platform="${escapeHtml(platform)}"
        aria-label="Apri la scheda di ${escapeHtml(title)}">
      ${coverMarkup(game, "vetrina-game__cover")}
      <span class="vetrina-game__title">${escapeHtml(title)}</span>
      ${facts.length ? `<span class="vetrina-game__facts">${facts.map(escapeHtml).join(" · ")}</span>` : ""}
    </button>`;
  }

  function platformFilters(games) {
    const counts = new Map();
    games.forEach((game) => {
      const platform = valueOf(game, "platform");
      if (platform) counts.set(platform, (counts.get(platform) || 0) + 1);
    });

    return [...counts.entries()]
      .sort(([a], [b]) => a.localeCompare(b, "it", { sensitivity: "base", numeric: true }))
      .map(([platform, count]) => `<button class="vetrina-filter" type="button"
          data-vetrina-filter="${escapeHtml(platform)}" aria-pressed="false">
        <span>${escapeHtml(platform)}</span><span class="vetrina-filter__count">${count}</span>
      </button>`).join("");
  }

  function renderHome(element, context) {
    const games = Array.isArray(context?.state?.games)
      ? context.state.games.filter((game) => game && game.id !== null && game.id !== undefined
        && hasValue(game.title))
      : [];

    if (!games.length) {
      element.innerHTML = `<div class="vetrina vetrina--empty">
        <section class="vetrina-empty" aria-labelledby="vetrina-empty-title">
          <span class="vetrina-empty__mark" aria-hidden="true"></span>
          <h1 id="vetrina-empty-title">La tua libreria è ancora vuota</h1>
          <p>Quando aggiungi o importi giochi, li troverai qui.</p>
        </section>
      </div>`;
      element.onclick = null;
      return;
    }

    const featured = games.find((game) => valueOf(game, "description")) || games[0];
    const featuredTitle = valueOf(featured, "title");
    const description = valueOf(featured, "description");
    const platform = valueOf(featured, "platform");
    const year = valueOf(featured, "year");
    const facts = [
      platform ? `<div><dt>Piattaforma</dt><dd>${escapeHtml(platform)}</dd></div>` : "",
      year ? `<div><dt>Anno</dt><dd>${escapeHtml(year)}</dd></div>` : "",
    ].filter(Boolean).join("");
    const gameCount = games.length;
    const gameLabel = gameCount === 1 ? "gioco" : "giochi";

    element.innerHTML = `<div class="vetrina">
      <header class="vetrina-header">
        <div>
          <h1>La tua raccolta</h1>
          <p>Un posto per ritrovare i tuoi giochi.</p>
        </div>
        <p class="vetrina-total"><strong>${gameCount}</strong> ${gameLabel}</p>
      </header>

      <section class="vetrina-feature" aria-labelledby="vetrina-feature-heading">
        <div class="vetrina-feature__copy">
          <h2 id="vetrina-feature-heading">${escapeHtml(featuredTitle)}</h2>
          ${description ? `<p class="vetrina-feature__description">${escapeHtml(description)}</p>` : ""}
          ${facts ? `<dl class="vetrina-feature__facts">${facts}</dl>` : ""}
          <button class="vetrina-primary" type="button" data-vetrina-game="${escapeHtml(featured.id)}"
              aria-label="Apri la scheda di ${escapeHtml(featuredTitle)}">Apri scheda</button>
        </div>
        <div class="vetrina-feature__art">
          ${coverMarkup(featured, "vetrina-feature__cover")}
        </div>
      </section>

      <div class="vetrina-layout">
        <section class="vetrina-library" aria-labelledby="vetrina-library-heading">
          <div class="vetrina-section-head">
            <h2 id="vetrina-library-heading">Tutti i giochi</h2>
            <p data-vetrina-result-count role="status" aria-live="polite">${gameCount} ${gameLabel}</p>
          </div>
          <div class="vetrina-games">
            ${games.map(gameCard).join("")}
          </div>
        </section>

        <aside class="vetrina-aside" aria-labelledby="vetrina-platform-heading">
          <h2 id="vetrina-platform-heading">Esplora per piattaforma</h2>
          <div class="vetrina-filters">
            <button class="vetrina-filter" type="button" data-vetrina-filter=""
                aria-pressed="true"><span>Tutte</span><span class="vetrina-filter__count">${gameCount}</span></button>
            ${platformFilters(games)}
          </div>
        </aside>
      </div>
    </div>`;

    element.querySelectorAll("[data-vetrina-cover]").forEach((image) => {
      image.addEventListener("error", () => {
        image.hidden = true;
        image.closest(".vetrina-feature__cover, .vetrina-game__cover")?.classList.add("is-missing");
      }, { once: true });
    });

    element.onclick = (event) => {
      const target = event.target.closest("[data-vetrina-filter], [data-vetrina-game]");
      if (!target) return;

      if (target.hasAttribute("data-vetrina-filter")) {
        const selected = target.dataset.vetrinaFilter;
        element.querySelectorAll("[data-vetrina-filter]").forEach((button) => {
          button.setAttribute("aria-pressed", String(button === target));
        });
        const cards = element.querySelectorAll("[data-vetrina-card]");
        let visible = 0;
        cards.forEach((card) => {
          const matches = !selected || card.dataset.vetrinaPlatform === selected;
          card.hidden = !matches;
          if (matches) visible++;
        });
        const label = visible === 1 ? "gioco" : "giochi";
        const resultCount = element.querySelector("[data-vetrina-result-count]");
        if (resultCount) resultCount.textContent = `${visible} ${label}`;
        return;
      }

      const game = games.find((item) => String(item.id) === target.dataset.vetrinaGame);
      if (game) GS.openGame(game.id);
    };
  }

  GS.registerExperience({
    id: "storefront",
    name: "Vetrina",
    description: "Una vetrina di gioco per esplorare la tua raccolta.",
    nav: "top",
    home: renderHome,
    card: null,
    detail: "page",
    onFocus: null,
  });
})();
