"use strict";
/* Controller, tastiera, suoni e lettura vocale condivisi da Big Picture e dalle esperienze desktop.
 * Pad.start({ input }) legge il pad e chiama input("left" | "a" | "lb" ...); i tasti da tastiera
 * si traducono con Pad.keyAction(e). Il resto (schermate, fuoco) e' di chi lo usa. */

const Pad = {
  kind: "xbox", sound: store.get("bpSound", true), talk: store.get("bpTalk", false),
  tones: { move: 520, ok: 880, back: 330, err: 180 }, // uno stile TV puo' sostituirli
};
const PAD_GLYPHS = {
  xbox: { a: "A", b: "B", x: "X", y: "Y", lb: "LB", rb: "RB", lt: "LT", rt: "RT", back: "View", start: "Menu" },
  ps: { a: "✕", b: "○", x: "□", y: "△", lb: "L1", rb: "R1", lt: "L2", rt: "R2", back: "Share", start: "Options" },
};
function glyph(k) {
  const t = PAD_GLYPHS[Pad.kind][k];
  return `<span class="btn-glyph ${k}${t.length > 1 ? " rect" : ""}">${esc(t)}</span>`;
}

/** Legge un messaggio con il lettore di schermo (regione aria-live, nessun effetto visivo). */
function announce(text) {
  if (!Pad.talk || !text) return;
  const live = $("#bp-live");
  if (!live) return;
  live.textContent = ""; // svuotare prima: alcuni lettori ignorano testi identici consecutivi
  setTimeout(() => { live.textContent = String(text).slice(0, 300); }, 60);
}

let actx = null;
function beep(kind) {
  if (!Pad.sound) return;
  try {
    actx = actx || new AudioContext();
    const o = actx.createOscillator(), g = actx.createGain();
    o.frequency.value = Pad.tones[kind] || 500;
    o.type = "sine"; g.gain.value = 0.035;
    o.connect(g); g.connect(actx.destination);
    o.start(); g.gain.exponentialRampToValueAtTime(0.0001, actx.currentTime + 0.07); o.stop(actx.currentTime + 0.08);
  } catch { /* audio non disponibile */ }
}

/** Annuncia con il lettore di schermo ciò che si sposta il fuoco (card, pulsanti, schede). */
let lastSpoken = "", lastSpokenAt = 0;
Pad.resetSpoken = () => { lastSpoken = ""; };
Pad.watchFocus = ({ focus, roots, section = ".shelf" }) => {
  const obs = new MutationObserver(() => {
    const el = document.querySelector(focus);
    if (!el) return;
    const txt = (el.querySelector(".lbl")?.textContent || el.getAttribute("aria-label") || el.textContent || "")
      .replace(/\s+/g, " ").trim();
    if (!txt || txt === lastSpoken) return;
    const now = Date.now();
    if (now - lastSpokenAt < 250) return;
    lastSpoken = txt;
    lastSpokenAt = now;
    const sec = el.closest(section)?.querySelector("h2")?.textContent?.trim() || "";
    announce(sec ? `${txt} — ${sec}` : txt);
  });
  for (const root of roots) {
    const node = $(root);
    if (node) obs.observe(node, { subtree: true, attributes: true, attributeFilter: ["class"] });
  }
};

/** Tasto di tastiera -> azione del pad ("" se non e' un tasto di navigazione). */
Pad.keyAction = (e) => {
  if (e.key === "Backspace") return "b";
  if (e.key === "Tab") return e.shiftKey ? "lb" : "rb";
  if (e.key === "f" || e.key === "F") return "x";
  return { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", Enter: "a", Escape: "b",
    PageUp: "lt", PageDown: "rt", "/": "y", q: "lb", Q: "lb", e: "rb", E: "rb", s: "start", S: "start", " ": "a" }[e.key] || "";
};

/** Gamepad: mappatura standard, ripetizione tenendo premuto; Select+Start tenuti premuti chiamano combo.run
 *  (solo se combo.when() e' vero). onKind(kind) avvisa quando cambia il tipo di pad (xbox/ps). */
Pad.start = ({ input, onKind, combo }) => {
  const st = { held: {}, next: {}, comboSince: 0 };
  const poll = (ts) => {
    const pads = navigator.getGamepads ? [...navigator.getGamepads()].filter(Boolean) : [];
    if (pads.length) {
      const p = pads[0];
      const kind = /054c|sony|dualsense|dualshock|wireless controller/i.test(p.id) ? "ps" : "xbox";
      if (kind !== Pad.kind) { Pad.kind = kind; onKind?.(kind); }
      const b = (i) => !!p.buttons[i]?.pressed;
      const ax = p.axes[0] || 0, ay = p.axes[1] || 0;
      const dirs = { left: b(14) || ax < -0.55, right: b(15) || ax > 0.55, up: b(12) || ay < -0.55, down: b(13) || ay > 0.55,
        a: b(0), b: b(1), x: b(2), y: b(3), lb: b(4), rb: b(5), lt: b(6), rt: b(7), start: b(9) };
      for (const [k, on] of Object.entries(dirs)) {
        const rep = ["left", "right", "up", "down", "lt", "rt"].includes(k);
        if (on && !st.held[k]) { st.held[k] = true; st.next[k] = ts + 380; input(k); }
        else if (on && rep && ts >= st.next[k]) { st.next[k] = ts + 85; input(k); }
        else if (!on) st.held[k] = false;
      }
      // Select/View (8) + Start/Menu (9) tenuti premuti
      if (combo && b(8) && b(9) && combo.when()) {
        if (!st.comboSince) st.comboSince = ts;
        if (ts - st.comboSince > 1800) { st.comboSince = Infinity; combo.run(); }
      } else st.comboSince = 0;
    }
    requestAnimationFrame(poll);
  };
  requestAnimationFrame(poll);
};
