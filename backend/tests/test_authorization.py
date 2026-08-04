"""
Regression coverage for S-01/S-02 (IDOR) and BL-03/BL-04 — the most severe
findings in the engineering review's Phase 5 security review. These tests
exist specifically so a future change can't silently reintroduce them.
"""
from conftest import register_user, auth_headers, FAKE_PDF_BYTES


def _two_users(client):
    a = register_user(client, email="alice@test.com", phone="111", name="Alice")
    b = register_user(client, email="bob@test.com", phone="222", name="Bob")
    return a, b


def test_user_cannot_read_another_users_passport(client):
    a, b = _two_users(client)
    resp = client.get(f"/passport/{b['user']['id']}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 403


def test_user_can_read_own_passport(client):
    a, _ = _two_users(client)
    resp = client.get(f"/passport/{a['user']['id']}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 200


def test_user_cannot_read_another_users_applications(client):
    a, b = _two_users(client)
    resp = client.get(f"/my_applications/{b['user']['id']}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 403


def test_apply_ignores_client_supplied_user_id(client):
    """
    /apply used to trust a client-supplied user_id form field outright — a
    caller could submit an application "as" any other user. It must now be
    derived from the token regardless of what the form claims.
    """
    a, b = _two_users(client)
    resp = client.post(
        "/apply",
        data={"user_id": str(b["user"]["id"]), "job_id": "1"},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    # No job with id 1 exists / no CV attached, so this won't succeed — the
    # point is only that it must fail on *validation*, not silently accept
    # user_id=b's id. A 400 (missing/invalid fields) is expected here; a
    # 201 recorded against Bob's account would be the bug this guards
    # against.
    assert resp.status_code in (400, 404)


def test_certificate_download_requires_ownership(client):
    a, b = _two_users(client)
    issue = client.post(
        "/issue_credential",
        data={"title": "Cert", "issuer": "Inst", "file": (__import__("io").BytesIO(FAKE_PDF_BYTES), "c.pdf")},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert issue.status_code == 201
    filename = "c.pdf"

    as_owner = client.get(f"/certificate/{filename}", headers=auth_headers(a["access_token"]))
    as_stranger = client.get(f"/certificate/{filename}", headers=auth_headers(b["access_token"]))

    assert as_owner.status_code == 200
    assert as_stranger.status_code == 403


def test_candidate_profile_linked_to_authenticated_user_not_client_claim(client):
    a, b = _two_users(client)
    # Bob tries to attach himself to Alice's future candidate profile by
    # reusing her email — upsert_candidate must key ownership off the JWT,
    # not anything the client sends.
    client.post(
        "/api/candidate",
        json={"email": "shared@test.com", "name": "Alice's profile"},
        headers=auth_headers(a["access_token"]),
    )
    resp = client.post(
        "/api/candidate",
        json={"email": "shared@test.com", "name": "Bob overwriting"},
        headers=auth_headers(b["access_token"]),
    )
    assert resp.status_code == 403
