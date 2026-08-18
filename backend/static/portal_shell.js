// Youth portal shell behavior (mobile nav toggle) -- same pattern as
// admin_shell.js / employer_shell.js's equivalents. Shared behaviors
// (confirm dialogs, table search, password toggle) live in
// shell_common.js, loaded alongside this file.
document.getElementById("portal-nav-scrim")?.addEventListener("click", () => {
  document.body.classList.remove("portal-nav-open");
});
document.getElementById("portal-nav-toggle")?.addEventListener("click", () => {
  document.body.classList.toggle("portal-nav-open");
});
