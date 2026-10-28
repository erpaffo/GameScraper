"use strict";
/* GameScraper - service worker della pagina del telefono.
   Serve a due cose: rendere l'app installabile (Chrome vuole un service worker con un gestore di fetch) e
   lasciare la libreria consultabile quando il Wi-Fi va e viene. Non tocca il controller (WebSocket) ne' i POST. */
const V = "gs-m-2";                       // cambiare qui per forzare il rinnovo della cache
const SHELL = ["/m/", "/m/static/m.css", "/m/static/m.js", "/m/static/icon.png", "/m/static/icon-192.png",
  "/m/static/apple-touch-icon.png", "/m/manifest.webmanifest"];
const MAX_IMG = 400;                      // copertine tenute: oltre, le nuove non si salvano (niente cache infinita)

self.addEventListener("install", (e) => {
  // file per file: se uno manca l'installazione non salta (con addAll basta un errore per perdere tutta la copia)
  e.waitUntil(caches.open(V)
    .then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => {}))))
    .then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== V).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

/** Copia locale subito, rete in sottofondo (la prossima apertura avra' la versione nuova). */
async function swr(req, key) {
  const c = await caches.open(V);
  const hit = await c.match(key || req);
  const net = fetch(req).then((r) => {
    if (r.ok) c.put(key || req, r.clone());
    return r;
  });
  if (hit) {
    net.catch(() => {});  // offline o errore: la copia e' gia' servita
    return hit;
  }
  return net;
}

async function imgFirst(req) {
  const c = await caches.open(V);
  const hit = await c.match(req);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok && (await c.keys()).length < MAX_IMG) c.put(req, r.clone());
  return r;
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const u = new URL(req.url);
  if (u.origin !== location.origin) return;
  if (u.searchParams.has("fresh")) return;        // aggiornamento chiesto dalla pagina: sempre dalla rete
  if (u.pathname.startsWith("/m/api/")) return;   // abbinamento e impostazioni del pad: mai dalla cache
  if (req.mode === "navigate") return void e.respondWith(swr(req, "/m/"));
  if (u.pathname.startsWith("/m/") || u.pathname === "/api/library") return void e.respondWith(swr(req));
  if (u.pathname.startsWith("/img/") || u.pathname.startsWith("/shots/")) return void e.respondWith(imgFirst(req));
});
