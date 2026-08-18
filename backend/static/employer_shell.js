// Employer-portal-specific shell behavior (mobile nav toggle) — the same
// pattern as admin_shell.js's equivalent. Shared behaviors (confirm
// dialogs, table search) live in shell_common.js, loaded alongside this
// file.
document.getElementById("employer-nav-scrim")?.addEventListener("click", () => {
  document.body.classList.remove("employer-nav-open");
});

document.getElementById("employer-nav-toggle")?.addEventListener("click", () => {
  document.body.classList.toggle("employer-nav-open");
});
