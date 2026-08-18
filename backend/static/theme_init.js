// Applies a previously-chosen dark/light theme before first paint, so a
// user who explicitly toggled dark mode doesn't see a flash of light mode
// on every subsequent page load (see shell_common.js's data-theme-toggle
// handler for the toggle itself, and style.css's [data-theme="dark"]
// block for the actual colors).
//
// Deliberately its own tiny external file rather than an inline <script>
// in <head>, which is the usual way this exact pattern is done elsewhere
// -- this app's CSP is script-src 'self' with no 'unsafe-inline', on
// purpose (see app.py's CSP comment), so an inline script here would
// just be silently blocked. And it can't live in shell_common.js either:
// that loads at the end of <body>, well after the browser has already
// painted the page in the wrong theme. Load this one first, before the
// CSS <link> tags, so it runs before anything renders.
(function () {
  try {
    var stored = localStorage.getItem("yc-theme");
    if (stored === "dark" || stored === "light") {
      document.documentElement.setAttribute("data-theme", stored);
    }
  } catch (e) {
    // localStorage can throw under some browser privacy settings --
    // falls back to whatever @media (prefers-color-scheme) resolves to.
  }
})();
