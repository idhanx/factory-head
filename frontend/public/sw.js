/*
 * FactoryManager AI — service worker. Hand-written; no build step touches it.
 *
 * The shop floor loses Wi-Fi behind a press, in the stores shed, whenever the
 * access point reboots. The worker's job is narrow: keep the app shell and the
 * last good copy of the grid and pick-lists on the tablet, so a supervisor can
 * still tap "Stopped" with no connection. The entries themselves are kept by
 * the page (IndexedDB outbox, src/lib/offline) — never here. Nothing that
 * writes is ever cached or replayed by this file.
 *
 * Bump SHELL_VERSION when this file's caching rules change. The API cache has
 * its own version so a shell update does not throw away the last good grid.
 */

const SHELL_VERSION = "v1";
const API_VERSION = "v1";

const PRECACHE = `fm-precache-${SHELL_VERSION}`;
const STATIC_CACHE = `fm-static-${SHELL_VERSION}`;
const PAGE_CACHE = `fm-pages-${SHELL_VERSION}`;
const API_CACHE = `fm-api-${API_VERSION}`;
const KEEP = [PRECACHE, STATIC_CACHE, PAGE_CACHE, API_CACHE];

const OFFLINE_URL = "/offline.html";
const PRECACHE_URLS = [OFFLINE_URL, "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

// Hashed build assets pile up across deploys; keep the newest few hundred.
const STATIC_LIMIT = 300;
const PAGE_LIMIT = 40;

// How long a read waits for the network before the last good copy is shown.
const API_PATIENCE_MS = 3500;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(PRECACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      // A tablet should not run yesterday's worker until every tab is closed —
      // on a shared bench tablet that never happens.
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((name) => name.startsWith("fm-") && !KEEP.includes(name)).map((name) => caches.delete(name)),
      );
      await self.clients.claim();
      // The page decides whether this is worth mentioning (it is only an
      // update if the page was already controlled by an older worker).
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) client.postMessage({ type: "sw-activated", version: SHELL_VERSION });
    })(),
  );
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  // Sign-out, or a switch to another plant: the cached pick-lists belong to
  // the previous login and must not be shown to the next one.
  if (data.type === "clear-api-cache") {
    event.waitUntil(caches.delete(API_CACHE));
  }
});

// ---------------------------------------------------------------------------
// Fetch routing
// ---------------------------------------------------------------------------

/** Reads the grid and dropdowns need offline. Everything else under /api is network-only. */
function isCachedApiRead(url) {
  return (
    url.pathname.startsWith("/api/v1/masters/") ||
    url.pathname === "/api/v1/shifts/current" ||
    url.pathname === "/api/v1/grid"
  );
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  // Writes go straight to the network, always. A replayed POST is a
  // duplicated stop; the outbox owns retries, with a client id the API dedupes.
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/api/")) {
    if (isCachedApiRead(url)) event.respondWith(apiRead(event));
    return; // any other API call: untouched, never cached
  }

  if (request.mode === "navigate") {
    event.respondWith(navigation(request));
    return;
  }

  if (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/")) {
    event.respondWith(cacheFirst(request, STATIC_CACHE, STATIC_LIMIT));
  }
});

/** Content-hashed files never change under the same URL, so the cache is the truth. */
async function cacheFirst(request, cacheName, limit) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok && response.type === "basic") {
    await cache.put(request, response.clone());
    trim(cacheName, limit);
  }
  return response;
}

/** Pages: the network when there is one, the last copy of this page when not. */
async function navigation(request) {
  const cache = await caches.open(PAGE_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok && response.type === "basic") {
      await cache.put(request, response.clone());
      trim(PAGE_CACHE, PAGE_LIMIT);
    }
    return response;
  } catch {
    const page = await cache.match(request, { ignoreVary: true });
    if (page) return page;
    const offline = await caches.match(OFFLINE_URL);
    return offline || new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } });
  }
}

/**
 * The grid, the current shift and the master lists: stale-while-revalidate,
 * with one change that matters on a working floor.
 *
 * Plain SWR answers from the cache first. Right after a supervisor taps
 * "Running" the page refetches the grid — and plain SWR would hand back the
 * copy from before the tap, flipping the tile back to red. So the network gets
 * a short head start: a prompt answer is used (and stored); a slow or failed
 * one falls back to the last good copy while the request carries on in the
 * background and refreshes the cache for next time.
 */
async function apiRead(event) {
  const request = event.request;
  const cache = await caches.open(API_CACHE);

  const network = fetch(request).then(async (response) => {
    // Only a good answer is worth keeping; a 401 or a proxy error page is not
    // "the last good copy" of anything.
    if (response.ok) await cache.put(request, response.clone());
    return response;
  });
  // Keep the refresh alive even after the page has been answered from cache.
  event.waitUntil(network.then(() => undefined, () => undefined));

  let timer;
  const patience = new Promise((resolve) => {
    timer = setTimeout(() => resolve("slow"), API_PATIENCE_MS);
  });

  try {
    const winner = await Promise.race([network, patience]);
    if (winner !== "slow") return winner;
    const cached = await cache.match(request, { ignoreVary: true });
    return cached || (await network);
  } catch (error) {
    const cached = await cache.match(request, { ignoreVary: true });
    if (cached) return cached;
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function trim(cacheName, limit) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  // Cache keys come back in insertion order: the oldest go first.
  for (let i = 0; i < keys.length - limit; i += 1) await cache.delete(keys[i]);
}

// ---------------------------------------------------------------------------
// Push: escalations, shift reports, RCA assignments
// ---------------------------------------------------------------------------

self.addEventListener("push", (event) => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    message = { body: event.data ? event.data.text() : "" };
  }
  const title = message.title || "FactoryManager AI";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: message.body || "",
      tag: message.tag || undefined,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: message.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin);

  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      // Already open on that page: bring it forward.
      const exact = windows.find((client) => new URL(client.url).pathname === target.pathname);
      if (exact) return exact.focus();
      // The app is open elsewhere: reuse that tab rather than stacking another.
      const any = windows.find((client) => new URL(client.url).origin === target.origin);
      if (any) {
        await any.focus();
        if ("navigate" in any) return any.navigate(target.href).catch(() => self.clients.openWindow(target.href));
        return undefined;
      }
      return self.clients.openWindow(target.href);
    })(),
  );
});
