"""
Coverage for GET /api/generate_cv/<id> -- the AI-polished HTML CV
(cv_html) added alongside the original plain-text one. See
cv_generator.py's own docstring for the three-layer security model this
locks in: the AI is never allowed to author HTML (only plain-text
content fields), every value is escaped before interpolation, and a
bleach allowlist pass runs on top of that. These tests exercise all
three failure modes that model is meant to catch, plus the CV-integrity
guard against a hallucinated skill and the revoked-credential exclusion.
"""
import json

from conftest import register_user, auth_headers


def _make_candidate(client, token, **overrides):
    payload = {
        "name": "Alice Youth", "email": "alice-cv@test.com", "location": "Freetown",
        "skills": "python, customer service, excel",
        "bio": "Recent graduate looking for entry-level work.",
        "preferred_industries": [],
    }
    payload.update(overrides)
    resp = client.post("/api/candidate", json=payload, headers=auth_headers(token))
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    return resp.get_json()["candidate_id"]


def _fake_claude_response(headline, summary, skills_ordered):
    return {
        "content": [
            {"type": "text", "text": json.dumps({
                "headline": headline, "summary": summary, "skills_ordered": skills_ordered,
            })}
        ]
    }


class _FakeResp:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._json


def test_generate_cv_without_an_api_key_still_returns_a_valid_cv_html(client, monkeypatch):
    """This dev environment actually has a real ANTHROPIC_API_KEY
    configured (same one scanner/ uses) -- forcing it absent here, rather
    than relying on ambient environment state, is what makes this test
    deterministic and what actually exercises the no-key fallback path."""
    import app as app_module

    monkeypatch.setattr(app_module, "_get_secret", lambda k: None)
    user = register_user(client)
    candidate_id = _make_candidate(client, user["access_token"])

    resp = client.get(f"/api/generate_cv/{candidate_id}", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ai_generated"] is False
    assert "<div class=\"cv-doc\">" in body["cv_html"]
    assert "Alice Youth" in body["cv_html"]
    assert "python" in body["cv_html"]
    assert "Recent graduate" in body["cv"]  # plain-text form unchanged


def test_bio_containing_a_script_tag_is_escaped_not_executed(client, monkeypatch):
    """The core XSS guard: a candidate's own bio is untrusted input. If
    escaping were ever dropped, this is what a real attack would look
    like -- a raw <script> tag reaching the client inside cv_html. Forces
    the no-AI-polish path (see the test above) so the raw bio is used
    verbatim as the summary, rather than an AI-paraphrased one that might
    not happen to quote the payload back at all."""
    import app as app_module

    monkeypatch.setattr(app_module, "_get_secret", lambda k: None)
    user = register_user(client)
    candidate_id = _make_candidate(
        client, user["access_token"],
        bio="<script>alert('xss')</script> Hardworking and reliable.",
    )

    resp = client.get(f"/api/generate_cv/{candidate_id}", headers=auth_headers(user["access_token"]))
    body = resp.get_json()
    assert "<script>" not in body["cv_html"]
    assert "&lt;script&gt;" in body["cv_html"]


def test_a_skill_the_ai_invents_is_rejected_not_added_to_the_cv(client, monkeypatch):
    """CV-integrity guard, not just a security one: skills_ordered must be
    exactly a reordering of what the candidate actually entered. If the
    model adds one, the whole skills_ordered answer is discarded and the
    candidate's own original list/order is used instead."""
    import app as app_module
    import cv_generator

    user = register_user(client)
    candidate_id = _make_candidate(
        client, user["access_token"], skills="python, excel",
    )

    def fake_post(url, timeout=None, headers=None, json=None):
        # Claude hallucinates an extra skill never entered by the candidate.
        return _FakeResp(_fake_claude_response(
            "Skilled professional", "A capable, motivated worker.",
            ["python", "excel", "project management"],
        ))

    monkeypatch.setattr(cv_generator.requests, "post", fake_post)
    monkeypatch.setattr(app_module, "_get_secret", lambda k: "fake-key" if k == "ANTHROPIC_API_KEY" else None)

    resp = client.get(f"/api/generate_cv/{candidate_id}", headers=auth_headers(user["access_token"]))
    body = resp.get_json()
    assert body["ai_generated"] is True  # headline/summary still used
    assert "Skilled professional" in body["cv_html"]
    assert "project management" not in body["cv_html"]  # hallucinated skill rejected
    assert "python" in body["cv_html"] and "excel" in body["cv_html"]


def test_a_valid_ai_reordering_of_the_same_skills_is_used(client, monkeypatch):
    import app as app_module
    import cv_generator

    user = register_user(client)
    candidate_id = _make_candidate(client, user["access_token"], skills="python, excel")

    def fake_post(url, timeout=None, headers=None, json=None):
        return _FakeResp(_fake_claude_response(
            "Data-savvy professional", "Confident with tools employers value.",
            ["excel", "python"],  # same two skills, just reordered
        ))

    monkeypatch.setattr(cv_generator.requests, "post", fake_post)
    monkeypatch.setattr(app_module, "_get_secret", lambda k: "fake-key" if k == "ANTHROPIC_API_KEY" else None)

    resp = client.get(f"/api/generate_cv/{candidate_id}", headers=auth_headers(user["access_token"]))
    body = resp.get_json()
    assert body["ai_generated"] is True
    assert "Data-savvy professional" in body["cv_html"]


def test_a_claude_network_failure_falls_back_gracefully(client, monkeypatch):
    import app as app_module
    import cv_generator
    import requests as real_requests

    user = register_user(client)
    candidate_id = _make_candidate(client, user["access_token"])

    def fake_post(*a, **k):
        raise real_requests.ConnectionError("simulated network failure")

    monkeypatch.setattr(cv_generator.requests, "post", fake_post)
    monkeypatch.setattr(app_module, "_get_secret", lambda k: "fake-key" if k == "ANTHROPIC_API_KEY" else None)

    resp = client.get(f"/api/generate_cv/{candidate_id}", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ai_generated"] is False
    assert "<div class=\"cv-doc\">" in body["cv_html"]


def test_revoked_credentials_are_excluded_from_the_cv(client, monkeypatch):
    """Real pre-existing gap found while rebuilding this endpoint: a
    revoked credential was still listed as verified on the CV. Not
    testing AI polish here, so it's disabled -- keeps this deterministic
    and avoids spending a real API call on an unrelated assertion."""
    import app as app_module

    monkeypatch.setattr(app_module, "_get_secret", lambda k: None)
    user = register_user(client)
    candidate_id = _make_candidate(client, user["access_token"])

    with app_module.app.app_context():
        active = app_module.Credential(
            user_id=user["user"]["id"], title="Active Cert", issuer="Test Institute",
            year=2025, hash="a" * 64,
        )
        revoked = app_module.Credential(
            user_id=user["user"]["id"], title="Revoked Cert", issuer="Test Institute",
            year=2024, hash="b" * 64, revoked_at=app_module.datetime.utcnow(),
        )
        app_module.db.session.add_all([active, revoked])
        app_module.db.session.commit()

    resp = client.get(f"/api/generate_cv/{candidate_id}", headers=auth_headers(user["access_token"]))
    body = resp.get_json()
    assert "Active Cert" in body["cv_html"]
    assert "Revoked Cert" not in body["cv_html"]
    assert "Active Cert" in body["cv"]
    assert "Revoked Cert" not in body["cv"]


def test_generate_cv_for_nonexistent_candidate_404s(client):
    user = register_user(client)
    resp = client.get("/api/generate_cv/999999", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 404


def test_generate_cv_for_someone_elses_candidate_is_forbidden(client):
    user_a = register_user(client, email="cv-a@test.com", phone="301")
    user_b = register_user(client, email="cv-b@test.com", phone="302")
    candidate_id = _make_candidate(client, user_a["access_token"], email="cv-owner@test.com")

    resp = client.get(f"/api/generate_cv/{candidate_id}", headers=auth_headers(user_b["access_token"]))
    assert resp.status_code == 403


def test_generate_cv_is_rate_limited(client, monkeypatch):
    """Real gap found and closed alongside this rewrite: every other
    action that spends real per-call money or resources (uploads,
    reports) already had a throttle -- generate_cv, which now spends a
    real Anthropic API call each time, didn't."""
    import app as app_module

    monkeypatch.setattr(app_module, "_get_secret", lambda k: None)
    user = register_user(client)
    candidate_id = _make_candidate(client, user["access_token"])
    headers = auth_headers(user["access_token"])

    for _ in range(app_module._CV_GENERATION_MAX_ATTEMPTS):
        resp = client.get(f"/api/generate_cv/{candidate_id}", headers=headers)
        assert resp.status_code == 200

    resp = client.get(f"/api/generate_cv/{candidate_id}", headers=headers)
    assert resp.status_code == 429
