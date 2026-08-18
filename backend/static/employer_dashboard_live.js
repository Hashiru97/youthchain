// Real-time dashboard refresh via Socket.IO. Externalized from an inline
// <script> block in employer_dashboard.html as part of a CSP hardening
// pass (OWASP A05) — a strict script-src 'self' with no 'unsafe-inline'
// can't allow an inline block at all, so this needed to be a real file
// regardless of how small it is.
const socket = io(location.origin, { transports: ["websocket"] });
socket.on("job_created", () => location.reload());
socket.on("application_created", () => location.reload());
socket.on("application_status_changed", () => location.reload());
