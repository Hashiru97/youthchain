// PWA-specific behavior for the youth web portal (/portal/*) -- service
// worker registration, the low-data mode toggle, and the install prompt.
// Deliberately its own small file loaded only by portal_shell.html, not
// shell_common.js (shared with the admin and employer shells too), so
// visiting the admin or employer portal alone never registers a service
// worker, shows a low-data toggle, or prompts to install an app that
// only the youth portal actually is one -- see sw.js/manifest.json's own
// docstrings in app.py for what this is standing on.

// ----------------- Service worker registration + low-data sync -----------------

// Current low-data preference, read the same way the toggle handler below
// writes it -- "on"/"off" is an explicit user override, absent means "let
// the worker follow the automatic navigator.connection.saveData signal
// instead" (see sw.js's own docstring on manualLowData for why this gets
// re-sent on every load rather than trusted to persist inside the worker
// itself, which the browser can silently terminate and resurrect).
function _lowDataPreference() {
  try {
    const stored = localStorage.getItem("yc-low-data");
    return stored === "on" ? true : stored === "off" ? false : null;
  } catch (e) {
    return null;
  }
}

function _sendLowDataPreference(worker) {
  if (worker) worker.postMessage({ type: "SET_LOW_DATA", value: _lowDataPreference() });
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .then((registration) => {
        // Sync now if a worker is already controlling this page (the
        // common case, every load after the first) -- and again the
        // moment a new worker takes control (the first-ever install, or
        // right after an updated sw.js activates), since that's a fresh
        // worker instance with manualLowData back at its default.
        _sendLowDataPreference(registration.active);
        navigator.serviceWorker.addEventListener("controllerchange", () => {
          _sendLowDataPreference(navigator.serviceWorker.controller);
        });
      })
      .catch(() => {
        // Best-effort: a failed registration (unsupported browser, blocked
        // by an extension, etc.) must never break a page that works
        // perfectly well without offline support.
      });
  });
}

// ----------------- Low-data mode toggle -----------------

// <button data-low-data-toggle> in portal_shell.html's nav -- mirrors
// shell_common.js's [data-theme-toggle] shape (flip an attribute on
// <html>, persist to localStorage under a "yc-*" key) but lives here
// instead since it's meaningless outside the youth portal. Sets the
// <html> attribute immediately on load too (not just on click) so the
// button's own icon/active styling (see shell_common.css) reflects a
// previously-saved choice on every page load, not just the one where it
// was set -- same "reflect current state on load" reasoning as
// theme_init.js, just without that file's flash-of-wrong-color urgency
// (this only affects one small nav icon, not the whole page's colors),
// so it's fine for this to run here rather than needing its own
// before-first-paint file.
(function () {
  const stored = _lowDataPreference();
  if (stored !== null) {
    document.documentElement.setAttribute("data-low-data", stored ? "on" : "off");
  }
})();

document.querySelectorAll("[data-low-data-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const root = document.documentElement;
    const next = root.getAttribute("data-low-data") === "on" ? "off" : "on";
    root.setAttribute("data-low-data", next);
    try {
      localStorage.setItem("yc-low-data", next);
    } catch (e) {
      // Best-effort persistence only -- the toggle still works for this
      // page view even if storage is unavailable.
    }
    // Immediate effect for this session too, not just the next page
    // load -- if a worker isn't controlling this page yet (e.g. the
    // very first visit, before install/activate has finished), there's
    // nothing to tell yet; the load handler above will sync it once one
    // takes control.
    if ("serviceWorker" in navigator) {
      _sendLowDataPreference(navigator.serviceWorker.controller);
    }
  });
});

// ----------------- Install prompt -----------------

// Chrome/Edge/Android suppress their own native install UI once a page
// calls preventDefault() on beforeinstallprompt, handing control to this
// page instead -- without a replacement UI, that would leave a user with
// NO way to install at all rather than just losing the default one, so
// this exists specifically to give them one back (see the
// data-install-app-button element in portal_shell.html, hidden by
// default via shell_common.css and only revealed once this fires,
// proving the browser actually considers the page installable right
// now). iOS Safari never fires this event at all (it has no equivalent
// API) -- the button simply never appears there, which is correct: its
// "Add to Home Screen" is a manual Share-sheet action this page cannot
// trigger or detect, only apple-touch-icon/apple-mobile-web-app-* (see
// portal_shell.html) can make it look right once a user finds it
// themselves.
let _deferredInstallPrompt = null;

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  _deferredInstallPrompt = event;
  document.querySelectorAll("[data-install-app-button]").forEach((button) => {
    button.hidden = false;
  });
});

document.querySelectorAll("[data-install-app-button]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!_deferredInstallPrompt) return;
    button.hidden = true;
    _deferredInstallPrompt.prompt();
    // The resolved userChoice isn't branched on -- whether the user
    // accepts or dismisses, the browser's own one-shot prompt is spent
    // either way (it cannot be reused), so the button stays hidden for
    // this page load regardless. appinstalled below is what persists
    // "never show it again" across future loads for an actual accept.
    await _deferredInstallPrompt.userChoice;
    _deferredInstallPrompt = null;
  });
});

// Fires on a real successful install (whether reached via this page's
// own button or the browser's native UI on a page that didn't
// preventDefault) -- persisted so an already-installed user never sees
// the button again on a future browser-tab visit, not just this session.
window.addEventListener("appinstalled", () => {
  document.querySelectorAll("[data-install-app-button]").forEach((button) => {
    button.hidden = true;
  });
  try {
    localStorage.setItem("yc-app-installed", "1");
  } catch (e) {
    // Best-effort -- worst case the button could reappear on a future
    // load for an already-installed user, which beforeinstallprompt
    // itself won't even fire for anyway on most browsers.
  }
});
