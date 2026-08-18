// Behaviors shared by the admin console AND the employer portal shells
// (both server-rendered, both CSP-strict script-src 'self') -- factored
// out of admin_shell.js when the employer portal got its own equivalent
// shell, rather than duplicating the same three helpers into a second
// file. Nothing here assumes which shell loaded it; every hook is opt-in
// via a data-* attribute on the specific element that wants it.

// Confirmation on genuinely destructive actions (Suspend Employer, Revoke
// Credential, Deactivate Admin, Reject Verification on the admin side --
// real usability/safety gap found in a full console review: these all
// fired immediately on click, with no "are you sure," so a slow
// double-click or a misclick had no recovery short of manually reversing
// the action through a second page). Scoped to the individual BUTTON, not
// the whole form, via the submit event's `submitter` -- several of these
// forms have a second, non-destructive button (e.g. Approve alongside
// Reject) that must never be gated behind a confirm dialog of its own.
// Add data-confirm="..." to a <button> to opt it in; nothing else changes.
document.addEventListener("submit", (event) => {
  const submitter = event.submitter;
  const message = submitter?.dataset?.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

// Password visibility toggle -- a <button data-toggle-password="#field-id">
// flips that field between type="password"/"text" and swaps its own two
// child icons (a real usability win over guessing whether caps lock
// mangled what was typed). The button holds two pre-rendered icons
// (eye / eye-off) as children, toggled via a CSS class rather than
// swapping innerHTML, so nothing here needs to know the icons' own markup.
document.querySelectorAll("[data-toggle-password]").forEach((button) => {
  const field = document.querySelector(button.dataset.togglePassword);
  if (!field) return;
  button.addEventListener("click", () => {
    const showing = field.type === "text";
    field.type = showing ? "password" : "text";
    button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
    button.classList.toggle("is-showing", !showing);
  });
});

// Dark/light theme toggle -- <button data-theme-toggle> flips data-theme
// on <html> between "light"/"dark" and persists the choice to
// localStorage under the same "yc-theme" key theme_init.js reads on the
// NEXT page load (before this file has even loaded) to avoid a flash of
// the wrong theme. Falls back to the OS's own prefers-color-scheme (see
// style.css's @media query) until a user has explicitly picked one --
// reads that same media query here so the very first click always
// flips AWAY from whatever's currently showing, not just "away from
// dark" when no explicit choice exists yet.
document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const root = document.documentElement;
    const current = root.getAttribute("data-theme");
    const isDark = current
      ? current === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = isDark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem("yc-theme", next);
    } catch (e) {
      // Best-effort persistence only -- the toggle still works for this
      // page view even if storage is unavailable.
    }
  });
});

// Registration channel choice (portal_register.html's "contact" step) --
// a radio pair (data-otp-channel-radio) toggles the single identifier
// field's type/autocomplete/placeholder and its label between email and
// phone shape, without needing two separate fields only one of which
// gets submitted. Purely a same-page UX nicety: the server independently
// validates whichever channel was actually posted, this just keeps the
// input's keyboard/autofill hints honest as the user picks.
document.querySelectorAll("[data-otp-channel-radio]").forEach((radio) => {
  radio.addEventListener("change", () => {
    if (!radio.checked) return;
    const field = document.querySelector("[data-otp-identifier-field]");
    const label = document.getElementById("identifier_label");
    if (!field) return;
    if (radio.value === "sms") {
      field.type = "tel";
      field.autocomplete = "tel";
      field.placeholder = "e.g. 076123456";
      if (label) label.textContent = "Phone Number";
    } else {
      field.type = "email";
      field.autocomplete = "email";
      field.placeholder = "you@example.com";
      if (label) label.textContent = "Email Address";
    }
  });
});

// Lightweight client-side filter for tables that can grow long (admin
// credentials/accounts/employer lists, the employer messages inbox) --
// real usability gap found alongside the same review: every list here was
// a static, unfilterable table, fine at a handful of rows and genuinely
// hard to use once a queue has dozens. No framework, no server round-trip:
// filters whatever <table> the input's data-table-filter selector points
// at by plain substring match against each row's own text, hides
// non-matching rows, and shows a "no matches" row it manages itself.
document.querySelectorAll("[data-table-filter]").forEach((input) => {
  const table = document.querySelector(input.dataset.tableFilter);
  if (!table) return;
  const tbody = table.tBodies[0];
  if (!tbody) return;
  const rows = Array.from(tbody.rows);

  const emptyRow = document.createElement("tr");
  emptyRow.className = "shell-table-filter-empty";
  emptyRow.hidden = true;
  const emptyCell = document.createElement("td");
  emptyCell.colSpan = table.tHead ? table.tHead.rows[0].cells.length : 1;
  emptyCell.textContent = "No matching rows.";
  emptyRow.appendChild(emptyCell);
  tbody.appendChild(emptyRow);

  input.addEventListener("input", () => {
    const query = input.value.trim().toLowerCase();
    let visibleCount = 0;
    for (const row of rows) {
      const matches = !query || row.textContent.toLowerCase().includes(query);
      row.hidden = !matches;
      if (matches) visibleCount += 1;
    }
    emptyRow.hidden = visibleCount !== 0;
  });
});
