/* AutoApply AI service worker: offline shell + notification clicks (PWA). */
const CACHE = "autoapply-shell-v2";
const SHELL = ["/dashboard", "/icon.svg", "/icon-192.png", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => undefined));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  // Never cache API calls; network-first for navigations with the cached shell as fallback.
  if (request.method !== "GET" || new URL(request.url).pathname.startsWith("/api/")) return;
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/dashboard")));
  }
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "notify" && self.registration.showNotification) {
    self.registration.showNotification(data.title || "AutoApply AI", {
      body: data.body || "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      data: { link: data.link || "/dashboard" },
      // A burst summary replaces the previous one instead of stacking ("5 new notifications", then "6 …")
      ...(data.tag ? { tag: data.tag } : {}),
    });
  }
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const link = (event.notification.data && event.notification.data.link) || "/dashboard";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.navigate(link);
          return client.focus();
        }
      }
      return self.clients.openWindow(link);
    }),
  );
});
