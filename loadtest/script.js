// Real load test against the backend — run with:
//   docker run --rm -i --network host grafana/k6 run - < loadtest/script.js
// (Linux/native Docker; on Docker Desktop for Windows/Mac, --network host
// doesn't work — instead point BASE_URL at host.docker.internal, see
// docs/load-testing.md for the exact command used to produce the numbers
// recorded there.)
//
// Exercises the two endpoints a real user's session hits most: the public
// job list (GET /jobs, unauthenticated, what a cold-start user sees first)
// and /healthz (what a load balancer/monitor polls constantly). Login is
// deliberately excluded from the main VU loop — OTP-gated registration and
// the OTP rate limiter (5 attempts/10min, see BL-09) make a scripted login
// loop either need real OTP codes or would just trip its own rate limit,
// which would measure the rate limiter, not the backend's real request
// throughput.
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:5000';

export const options = {
  scenarios: {
    steady_load: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 20),
      duration: __ENV.DURATION || '30s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<500'],
  },
};

export default function () {
  const jobsRes = http.get(`${BASE_URL}/jobs`);
  check(jobsRes, {
    'GET /jobs status 200': (r) => r.status === 200,
    'GET /jobs returns a JSON array': (r) => {
      try {
        return Array.isArray(JSON.parse(r.body));
      } catch (_) {
        return false;
      }
    },
  });

  const healthRes = http.get(`${BASE_URL}/healthz`);
  check(healthRes, { 'GET /healthz status 200': (r) => r.status === 200 });

  sleep(0.5);
}
