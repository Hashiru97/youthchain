// Job Scanner page: auto-refresh toggle + interval selector, persisted in
// localStorage so it survives the reload it itself triggers. Plain
// setInterval + location.reload(), matching this admin console's existing
// server-rendered-Jinja style everywhere else -- no new JS framework for
// one diagnostics page.
//
// Externalized from an inline <script> in admin_scanner.html: this page's
// CSP is script-src 'self' with no 'unsafe-inline', so the inline version
// was silently blocked by the browser -- the checkbox and interval select
// rendered and looked interactive, but never actually did anything.
(function () {
  var toggle = document.getElementById("scanner-auto-refresh-toggle");
  var intervalSelect = document.getElementById("scanner-auto-refresh-interval");
  var STORAGE_KEY = "youthchain_scanner_auto_refresh";
  var timer = null;

  function stored() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    } catch (e) {
      return null;
    }
  }

  function save(enabled, seconds) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ enabled: enabled, seconds: seconds }));
    } catch (e) {}
  }

  function apply() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    if (toggle.checked) {
      var seconds = parseInt(intervalSelect.value, 10) || 30;
      timer = setInterval(function () {
        window.location.reload();
      }, seconds * 1000);
    }
    save(toggle.checked, parseInt(intervalSelect.value, 10) || 30);
  }

  var saved = stored();
  if (saved) {
    toggle.checked = !!saved.enabled;
    intervalSelect.value = String(saved.seconds || 30);
  }
  toggle.addEventListener("change", apply);
  intervalSelect.addEventListener("change", apply);
  apply();
})();
