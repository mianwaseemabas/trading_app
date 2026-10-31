/* Qivynex service worker: offline shell + push notifications. API calls are never cached. */
const CACHE = "qivynex-shell-v1";
const SHELL = ["/", "/index.html", "/assets/app.js", "/assets/app.css", "/manifest.json", "/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // network first (always fresh after a deploy), cached copy only when offline
  e.respondWith(
    fetch(e.request).then((r) => {
      if (r.ok && (url.pathname.startsWith("/assets/") || url.pathname === "/" || url.pathname === "/index.html")) {
        const copy = r.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return r;
    }).catch(() => caches.match(e.request).then((m) => m || caches.match("/index.html")))
  );
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: "Qivynex", body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || "Qivynex", {
    body: d.body || "", icon: "/icons/icon-192.png", badge: "/icons/badge-96.png", data: { link: d.link || "#/alerts" }, tag: d.tag,
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = "/" + (e.notification.data && e.notification.data.link || "#/alerts");
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
    for (const c of cs) { if ("focus" in c) { c.navigate(target).catch(() => {}); return c.focus(); } }
    return self.clients.openWindow(target);
  }));
});
