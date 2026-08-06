// Previous cache marker retained for migration verification: kospi-shadow-decision-coach-v5-4-0-r1-static
const STATIC_CACHE = "kospi-shadow-decision-coach-v5-4-0-r2-static";
const STATIC_ASSETS = [
  "./",
  "index.html",
  "styles.css",
  "app.js",
  "operational-trust.js",
  "runtime-state-fix.js",
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
      await Promise.all(keys.filter((key) => key !== STATIC_CACHE).map((key) => caches.delete(key)));
      await self.clients.claim();
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

async function appWithOperationalTrust(request) {
  try {
    const trustUrl = new URL("operational-trust.js", request.url);
    const runtimeFixUrl = new URL("runtime-state-fix.js", request.url);
    const [appResponse, trustResponse, runtimeFixResponse] = await Promise.all([
      fetch(request, { cache: "no-store" }),
      fetch(new Request(trustUrl, { cache: "no-store" })),
      fetch(new Request(runtimeFixUrl, { cache: "no-store" }))
    ]);
    if (!appResponse.ok || !trustResponse.ok || !runtimeFixResponse.ok) {
      throw new Error("Operational trust bundle unavailable");
    }
    const appSource = (await appResponse.text()).replace(
      'const APP_SHELL_VERSION = "5.3.1";',
      'const APP_SHELL_VERSION = "5.4.0";'
    );
    const combined = `${appSource}\n;\n${await trustResponse.text()}\n;\n${await runtimeFixResponse.text()}\n`;
    const headers = new Headers(appResponse.headers);
    headers.set("Content-Type", "application/javascript; charset=utf-8");
    headers.set("Cache-Control", "no-store, max-age=0");
    const response = new Response(combined, {
      status: 200,
      statusText: "OK",
      headers
    });
    const cache = await caches.open(STATIC_CACHE);
    await cache.put(request, response.clone());
    return response;
  } catch (error) {
    const cached = await caches.match(request, { ignoreSearch: true });
    if (cached) return cached;
    return networkFirst(request);
  }
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  const sameOrigin = url.origin === self.location.origin;
  const isLiveData = url.pathname.endsWith("/data/dashboard.json") || url.pathname.endsWith("/data/history.json");
  const isNavigation = event.request.mode === "navigate";
  const isAppScript = sameOrigin && url.pathname.endsWith("/app.js");
  const isAppShell = sameOrigin && [
    "/operational-trust.js",
    "/runtime-state-fix.js",
    "/styles.css",
    "/manifest.webmanifest",
    "/sw.js",
    "/index.html",
    "/"
  ].some((suffix) => url.pathname.endsWith(suffix));

  if (isAppScript) {
    event.respondWith(appWithOperationalTrust(event.request));
    return;
  }

  if (isLiveData || isNavigation || isAppShell) {
    event.respondWith(networkFirst(event.request));
    return;
  }

  event.respondWith(
    caches.match(event.request, { ignoreSearch: true }).then((cached) => cached || fetch(event.request))
  );
});
