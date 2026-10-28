"use strict";
/* GameScraper - service worker della pagina del telefono.
   Serve a due cose: rendere l'app installabile (Chrome vuole un service worker con un gestore di fetch) e
   lasciare la libreria consultabile quando il Wi-Fi va e viene. Non tocca il controller (WebSocket) ne' i POST. */
const V = "gs-m-1";                       // cambiare qui per forzare il rinnovo della cache
const SHELL = ["/m/", "/m/static/m.css", "/m/static/m.js", "/m/static/icon.png", "/m/static/icon-192.png",
  "/m/static/apple-touch-icon.png", "/m/manifest.webmanifest"];
const MAX_IMG = 400;                      // copertine tenute: oltre, le nuove non si salvano (niente cache infinita)

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(V).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== V).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function netFirst(req, key) {
  try {
    const r = await fetch(req);
    if (r.ok) (await caches.open(V)).put(key || req, r.clone());
    return r;
  } catch (err) {
    const hit = await caches.match(key || req);
    if (hit) return hit;
    throw err;
  }
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
  if (u.pathname === "/m/api/hello" || u.pathname === "/m/api/me") return;  // stato dell'abbinamento: mai dalla cache
  if (req.mode === "navigate") return void e.respondWith(netFirst(req, "/m/"));
  if (u.pathname.startsWith("/m/") || u.pathname === "/api/library") return void e.respondWith(netFirst(req));
  if (u.pathname.startsWith("/img/") || u.pathname.startsWith("/shots/")) return void e.respondWith(imgFirst(req));
});
