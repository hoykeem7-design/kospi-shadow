const STATIC_CACHE = "kospi-shadow-decision-coach-v5-3-1-r2-static";
const STATIC_ASSETS = [
  "./",
  "index.html",
  "styles.css",
  "app.js",
  "manifest.webmanifest",
  "icons/icon-192.png",
  "icons/icon-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(STATIC_ASSETS.map((asset) => new Request(asset, { cache: "reload" }))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then(async (keys) => {
      const replacedAppShell = keys.some((key) => key.startsWith("kospi-shadow-decision-coach-") && key !== STATIC_CACHE);
      await Promise.all(keys.filter((key) => key !== STATIC_CACHE).map((key) => caches.delete(key)));
      await self.clients.claim();
      if (!replacedAppShell) return;
      const windows = await self.clients.matchAll({ type: "window" });
      await Promise.all(windows.map((client) => client.navigate ? client.navigate(client.url) : null));
    })
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
  if (event.data?.type === "CLEAR_CACHES") {
    event.waitUntil(caches.keys().then((keys) => Promise.all(keys.map((key) => caches.delete(key)))));
  }
});

async function networkFirst(request) {
  try {
    const response = await fetch(request, { cache: "no-store" });
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request, { ignoreSearch: true });
    if (cached) return cached;
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  const sameOrigin = url.origin === self.location.origin;
  const isLiveData = url.pathname.endsWith("/data/dashboard.json") || url.pathname.endsWith("/data/history.json");
  const isNavigation = event.request.mode === "navigate";
  const isAppShell = sameOrigin && [
    "/app.js",
    "/styles.css",
    "/manifest.webmanifest",
    "/sw.js",
    "/index.html",
    "/"
  ].some((suffix) => url.pathname.endsWith(suffix));

  if (isLiveData || isNavigation || isAppShell) {
    event.respondWith(networkFirst(event.request));
    return;
  }

  event.respondWith(
    caches.match(event.request, { ignoreSearch: true }).then((cached) => cached || fetch(event.request))
  );
});
