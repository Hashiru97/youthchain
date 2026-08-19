// Service worker for the youth web portal (/portal/*) -- see the
// /manifest.json and /sw.js Flask routes' own docstrings in app.py for
// why this is served from the root and what it deliberately does NOT
// attempt (no offline auth, no offline form submission -- CSRF tokens
// are minted per server render, and every mutating /portal/* route
// re-validates the session live; caching a POST would either fail
// against a stale token or silently let an offline user believe an
// application/rating/etc. went through when it didn't). This only ever
// makes already-rendered GET pages available for read access after the
// network drops -- the same "last known state" contract a native app's
// own local cache gives for content it already fetched once.
//
// Bump CACHE_NAME on any change to this file's own caching logic (not
// on every content change -- individual static files are already
// cache-busted per-file via asset_version()'s ?v=<mtime> query string,
// see app.py) so a stale worker's old cache entries get cleared on
// activate rather than accumulating forever.
const CACHE_NAME = "youth-portal-v2";
const OFFLINE_URL = "/static/offline.html";

// Small and deliberate: only the shell assets every /portal/* page
// actually loads (see portal_shell.html) plus the offline fallback
// itself. Individual job/application pages are NOT precached here --
// they're cached opportunistically the first time a real visit fetches
// them (see the fetch handler below), so this list stays small and
// never goes stale relative to what a specific user has actually seen.
const PRECACHE_URLS = [
  OFFLINE_URL,
  "/static/style.css",
  "/static/shell_common.css",
  "/static/portal.css",
  "/static/shell_common.js",
  "/static/portal_shell.js",
  "/static/theme_init.js",
  "/static/img/youthchain-icon.svg",
  "/static/img/icon-192.png",
  "/static/img/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

// Real, user-controllable low-data mode, not just the automatic
// navigator.connection.saveData check below -- that API is unsupported
// on Firefox and Safari/iOS entirely, and even on Chrome/Android it only
// reflects an OS-level Data Saver setting a user may not have on. This
// lets a user override it explicitly from the nav toggle (see
// shell_common.js's [data-low-data-toggle] handler) regardless of
// browser/OS support for the automatic signal.
//
// Deliberately a plain in-memory variable, not persisted via Cache
// Storage/IndexedDB: a service worker can be terminated by the browser
// after ~30s idle and resurrected fresh on the next fetch, which would
// reset this to null again. portal_pwa.js re-sends the current
// preference on every page load specifically to cover that -- the only
// real gap this leaves is the very first navigation of a session
// immediately after the worker was resurrected, which could race the
// resend and fall back to the automatic saveData signal for that one
// request; every navigation after self-corrects. Accepted trade-off for
// a best-effort UX enhancement, not worth a heavier persistence layer.
let manualLowData = null; // null = "follow the automatic saveData signal", true/false = explicit override
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SET_LOW_DATA") {
    manualLowData = event.data.value;
  }
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  // Never intercept anything but a plain read -- every portal form POST
  // (apply, login, rate, revoke a device, etc.) must always hit the real
  // network with a live, server-minted CSRF token, not a cached response.
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(handleNavigate(request));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(cacheFirst(request));
  }
});

// manualLowData (explicit user choice, see above) always wins when set;
// otherwise falls back to navigator.connection.saveData, the user's own
// OS/browser-level "Data Saver" signal -- either way, "on" means prefer
// whatever's already cached over spending a fresh request, instead of
// this worker's normal network-first default. Falls back to
// network-first when neither is available/on, which is the safe
// default: fresher content over a maybe-stale cache.
async function handleNavigate(request) {
  const preferCache = manualLowData === null ? self.navigator?.connection?.saveData === true : manualLowData;
  const cache = await caches.open(CACHE_NAME);

  if (preferCache) {
    const cached = await cache.match(request);
    if (cached) return cached;
  }

  try {
    const response = await fetch(request);
    // Only cache a genuinely successful, same-origin response -- caching
    // a 4xx/5xx (e.g. an expired-session redirect target) would make a
    // real error page look like a valid offline fallback later.
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    return cached || cache.match(OFFLINE_URL);
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}
