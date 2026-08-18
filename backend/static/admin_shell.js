// Admin-console-specific shell behavior (sidebar nav toggle). Shared
// behaviors (confirm-on-destructive-submit, password-visibility toggle,
// table search/filter) moved to shell_common.js, loaded alongside this
// file, when the employer portal got its own equivalent shell.
//
// Externalized from onclick="..." attributes as part of a CSP hardening
// pass (OWASP A05) — a strict script-src 'self' with no 'unsafe-inline'
// blocks inline event handler attributes exactly the same way it blocks
// inline <script> blocks, so these had to become real listeners in a
// real file.
document.getElementById("admin-nav-scrim")?.addEventListener("click", () => {
  document.body.classList.remove("admin-nav-open");
});

document.getElementById("admin-nav-toggle")?.addEventListener("click", () => {
  document.body.classList.toggle("admin-nav-open");
});
