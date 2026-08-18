// Specifically stresses SQLite's actual documented limitation (single-
// writer) — the read-path script.js above wouldn't show this at all,
// since /jobs and /healthz never write. Each VU registers a brand-new user
// every iteration (unique phone/email per iteration/VU), exercising a real
// write (INSERT into user, plus the OTP-related tables) under concurrency.
//
// Run with ENFORCE_EMAIL_OTP_REG=0 on the backend (see docs/load-testing.md)
// — otherwise every registration 400s waiting on an OTP code no load test
// script has, which would measure the OTP gate, not write concurrency.
import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:5000';

export const options = {
  scenarios: {
    write_load: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 20),
      duration: __ENV.DURATION || '20s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.05'],
  },
};

export default function () {
  const unique = `${__VU}-${__ITER}-${Date.now()}`;
  const res = http.post(
    `${BASE_URL}/register`,
    JSON.stringify({
      name: `Load Test ${unique}`,
      phone: `load-${unique}`,
      email: `load-${unique}@loadtest.example`,
      password: 'loadtest12345',
    }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  check(res, {
    'POST /register status 201': (r) => r.status === 201,
  });
}
