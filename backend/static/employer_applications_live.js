// Real-time applicants-list refresh via Socket.IO. See
// employer_dashboard_live.js's own comment for why this is a separate
// file rather than an inline <script> block.
const socket = io(location.origin, { transports: ["websocket"] });
socket.on("application_created", () => location.reload());
socket.on("application_status_changed", () => location.reload());
