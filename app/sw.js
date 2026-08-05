const STATIC_CACHE = "kospi-shadow-decision-coach-v5-3-1-static";
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
      .then((cache) => cache.addAll(STATIC_ASSETS))
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
      const windows = await self.clients.matchAll({type:"window"});
      await Promise.all(windows.map((client) => client.navigate ? client.navigate(client.url) : null));
    })
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

async function networkFirst(request) {
  try {
    return await fetch(request, { cache: "no-store" });
  } catch (error) {
    const cached = await caches.match(request, { ignoreSearch: true });
    if (cached) return cached;
    throw error;
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request, { ignoreSearch: true });
  const networkPromise = fetch(request).then((response) => {
    if (response.ok) cache.put(request, response.clone());
    return response;
  }).catch(() => null);
  if (cached) {
    networkPromise.catch(() => null);
    return cached;
  }
  return (await networkPromise) || Response.error();
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  const isLiveData = url.pathname.endsWith("/data/dashboard.json") || url.pathname.endsWith("/data/history.json");
  const isNavigation = event.request.mode === "navigate";

  if (isLiveData || isNavigation) {
    event.respondWith(networkFirst(event.request));
    return;
  }
  event.respondWith(staleWhileRevalidate(event.request));
});
