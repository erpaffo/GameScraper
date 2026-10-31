GS.registerExperience({
  id: "shelves",
  name: "Scaffali",
  description: "Home a scaffali e navigazione principale superiore.",
  nav: "top",
  home: (element, context) => context.renderShelvesHome(element),
  card: null,
  detail: "panel",
  onFocus: null,
});
