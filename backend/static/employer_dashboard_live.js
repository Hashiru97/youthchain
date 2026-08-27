// Real-time dashboard refresh via Socket.IO. Externalized from an inline
// <script> block in employer_dashboard.html as part of a CSP hardening
// pass (OWASP A05) — a strict script-src 'self' with no 'unsafe-inline'
// can't allow an inline block at all, so this needed to be a real file
// regardless of how small it is.
// job_created is deliberately a global, unscoped broadcast (see its emit
// site in app.py) for the youth-facing Discover feed -- this dashboard only
// ever shows the logged-in employer's own jobs, so listening for it here
// used to reload this page every time ANY employer anywhere posted a job.
const socket = io(location.origin, { transports: ["websocket"] });
socket.on("application_created", () => location.reload());
socket.on("application_status_changed", () => location.reload());
