GS.registerExperience({
  id: "console",
  name: "Console",
  description: "Una schermata immersiva per esplorare la raccolta con il focus.",
  nav: "none",
  home(element, context) {
    const root = document.createElement("section");
    root.className = "gs-console-home";
    root.setAttribute("aria-label", "Home della raccolta giochi");

    const backdrop = document.createElement("div");
    backdrop.className = "gs-console-backdrop";
    backdrop.setAttribute("aria-hidden", "true");
    const backdropImage = document.createElement("img");
    backdropImage.className = "gs-console-backdrop-image";
    backdropImage.alt = "";
    backdrop.append(backdropImage);

    const content = document.createElement("div");
    content.className = "gs-console-content";

    const header = document.createElement("header");
    header.className = "gs-console-header";
    const brand = document.createElement("div");
    brand.className = "gs-console-brand";
    brand.innerHTML = '<span class="gs-console-mark" aria-hidden="true"></span><span>RACCOLTA</span>';
    const count = document.createElement("span");
    count.className = "gs-console-count";
    header.append(brand, count);

    const library = document.createElement("section");
    library.className = "gs-console-library";
    library.setAttribute("aria-label", "I tuoi giochi");
    const libraryHeading = document.createElement("h2");
    libraryHeading.className = "gs-console-section-title";
    libraryHeading.textContent = "LA TUA RACCOLTA";
    const lane = document.createElement("div");
    lane.className = "gs-console-lane";
    lane.setAttribute("role", "group");
    lane.setAttribute("aria-label", "Giochi. Usa le frecce sinistra e destra per spostare il focus tra i titoli.");
    library.append(libraryHeading, lane);

    const feature = document.createElement("div");
    feature.className = "gs-console-feature";
    const title = document.createElement("h1");
    title.className = "gs-console-title";
    const subtitle = document.createElement("p");
    subtitle.className = "gs-console-subtitle";
    const actions = document.createElement("div");
    actions.className = "gs-console-actions";
    const openButton = document.createElement("button");
    openButton.className = "gs-console-open";
    openButton.type = "button";
    openButton.textContent = "Apri gioco";
    openButton.hidden = true;
    actions.append(openButton);
    feature.append(title, subtitle, actions);

    const MAX_LANE = 80; // il catalogo online conta migliaia di titoli: un solo DOM enorme blocca l'interfaccia
    const owned = Array.isArray(context?.state?.games)
      ? context.state.games.filter((game) => game && game.id != null && game.store !== "online"
        && (!game.grp || (game.id === game.grp.primary && !game.grp.hidden)))
      : [];
    const total = owned.length;
    const games = owned.slice().sort((a, b) => (b.grp?.last || 0) - (a.grp?.last || 0)).slice(0, MAX_LANE);
    const gameById = new Map(games.map((game) => [String(game.id), game]));
    const openGame = (game) => {
      if (game && typeof GS.openGame === "function") GS.openGame(game.id);
    };
    const setHero = (game) => {
      if (!game) return;
      const id = String(game.id);
      const gameTitle = typeof game.title === "string" && game.title.trim()
        ? game.title.trim()
        : "Gioco senza titolo";
      const coverAvailable = Boolean(game.cover || game.hero);

      root.dataset.focusedGame = id;
      title.textContent = gameTitle;
      const platformName = context?.state?.plats?.get?.(game.platform)?.name || game.platform;
      const details = [platformName]
        .filter((value) => typeof value === "string" && value.trim())
        .map((value) => value.trim());
      subtitle.textContent = details.join(" · ") || "La tua prossima avventura è a un pulsante di distanza.";
      openButton.hidden = false;
      openButton.dataset.consoleOpen = id;
      root.classList.remove("gs-console-has-art");
      backdropImage.onload = () => {
        if (root.dataset.focusedGame === id) root.classList.add("gs-console-has-art");
      };
      backdropImage.onerror = () => {
        if (root.dataset.focusedGame === id) root.classList.remove("gs-console-has-art");
      };
      if (coverAvailable) {
        backdropImage.src = `/img/${encodeURIComponent(game.id)}/cover?w=1600`;
      } else {
        backdropImage.removeAttribute("src");
      }
    };

    if (games.length) {
      count.textContent = `${total} ${total === 1 ? "GIOCO" : "GIOCHI"}`;
      title.textContent = "Scegli il tuo prossimo gioco";
      subtitle.textContent = "Sposta il focus su un titolo per metterlo in primo piano.";
      for (const game of games) {
        const button = document.createElement("button");
        button.className = "gs-console-game";
        button.type = "button";
        button.dataset.consoleGame = String(game.id);
        const gameTitle = typeof game.title === "string" && game.title.trim()
          ? game.title.trim()
          : "Gioco senza titolo";
        button.setAttribute("aria-label", `Apri ${gameTitle}`);

        const cover = document.createElement("span");
        cover.className = "gs-console-cover";
        const coverLabel = document.createElement("span");
        coverLabel.className = "gs-console-cover-label";
        coverLabel.textContent = gameTitle;
        if (game.cover || game.hero) {
          const image = document.createElement("img");
          image.src = `/img/${encodeURIComponent(game.id)}/cover?w=420`;
          image.alt = "";
          image.loading = "lazy";
          image.onerror = () => image.remove();
          cover.append(image);
        }
        cover.append(coverLabel);
        const label = document.createElement("span");
        label.className = "gs-console-game-title";
        label.textContent = gameTitle;
        button.append(cover, label);
        lane.append(button);
      }
      setHero(games[0]);
    } else {
      count.textContent = "SI COMINCIA";
      title.textContent = "La tua raccolta inizia da qui";
      subtitle.textContent = "Aggiungi o importa giochi per creare una raccolta tutta tua.";
      const libraryButton = document.createElement("button");
      libraryButton.className = "gs-console-open";
      libraryButton.type = "button";
      libraryButton.textContent = "Apri libreria";
      libraryButton.dataset.consoleLibrary = "true";
      actions.append(libraryButton);
      library.hidden = true;
    }

    root.append(backdrop, content);
    content.append(header, library, feature);

    root.addEventListener("focusin", (event) => {
      const button = event.target instanceof Element ? event.target.closest("[data-console-game]") : null;
      if (button && root.contains(button)) {
        const game = gameById.get(button.dataset.consoleGame);
        if (game) setHero(game);
      }
    });
    root.addEventListener("keydown", (event) => {
      if (!(event.target instanceof Element) || !event.target.matches("[data-console-game]")) return;
      const buttons = Array.from(lane.querySelectorAll("[data-console-game]"));
      const index = buttons.indexOf(event.target);
      let next = index;
      if (event.key === "ArrowRight") next = Math.min(buttons.length - 1, index + 1);
      else if (event.key === "ArrowLeft") next = Math.max(0, index - 1);
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = buttons.length - 1;
      else return;
      event.preventDefault();
      buttons[next]?.focus();
    });
    root.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (!target) return;
      const open = target.closest("[data-console-open]");
      if (open && root.contains(open)) {
        openGame(gameById.get(open.dataset.consoleOpen));
        return;
      }
      if (target.closest("[data-console-library]") && typeof context?.setView === "function") {
        context.setView({ type: "all" });
      }
      const gameButton = target.closest("[data-console-game]");
      if (gameButton && root.contains(gameButton)) {
        openGame(gameById.get(gameButton.dataset.consoleGame));
      }
    });

    element.replaceChildren(root);
    root._consoleSetHero = setHero;
    root._consoleGameById = gameById;
    activeRoot = root;
  },
  card: null,
  detail: "page",
  onFocus(game) {
    if (!activeRoot || !game) return;
    const focused = activeRoot._consoleGameById?.get(String(game.id)) || game;
    activeRoot._consoleSetHero?.(focused);
  },
});

let activeRoot = null;
