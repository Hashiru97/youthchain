import io

from conftest import register_user, auth_headers, FAKE_PDF_BYTES


def _issue(client, token, path="/issue_credential", content=FAKE_PDF_BYTES):
    return client.post(
        path,
        data={"title": "Cert", "issuer": "Inst", "file": (io.BytesIO(content), "c.pdf")},
        headers=auth_headers(token),
        content_type="multipart/form-data",
    )


def test_issue_credential_degrades_gracefully_when_onchain_write_fails(client):
    a = register_user(client)
    resp = _issue(client, a["access_token"])
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["credential_id"] == 1
    assert body["onchain_tx"] is None


def test_reuploading_same_file_does_not_create_a_duplicate_row(client):
    """
    Regression test for BL-16 (TD-07): /issue_credential and
    /api/certificate/upload used to have divergent dedup behavior. Now both
    share _issue_credential_internal and must behave identically.
    """
    a = register_user(client)
    first = _issue(client, a["access_token"], path="/issue_credential")
    second = _issue(client, a["access_token"], path="/api/certificate/upload")

    assert first.status_code == 201
    assert second.status_code == 200  # "already exists", not a new row
    assert first.get_json()["credential_id"] == second.get_json()["credential_id"]

    passport = client.get(f"/passport/{a['user']['id']}", headers=auth_headers(a["access_token"]))
    assert len(passport.get_json()) == 1


def test_rejects_file_whose_content_does_not_match_its_extension(client):
    """
    BL-23 / S-08: extension-only validation used to accept any file renamed
    to a .pdf — this confirms a plain-text file claiming to be a PDF is now
    rejected by magic-byte content sniffing.
    """
    a = register_user(client)
    resp = client.post(
        "/issue_credential",
        data={
            "title": "Cert",
            "issuer": "Inst",
            "file": (io.BytesIO(b"just plain text, not a real pdf"), "fake.pdf"),
        },
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_credential_issuance_is_self_service_only(client):
    """
    Until a real accredited-issuer model exists, a caller may only issue a
    credential to their own account (see the note in app.py on
    /issue_credential) — there is no code path today that even accepts a
    different target user_id, since it's derived from the token.
    """
    a = register_user(client)
    resp = _issue(client, a["access_token"])
    assert resp.status_code == 201
    passport = client.get(f"/passport/{a['user']['id']}", headers=auth_headers(a["access_token"]))
    assert passport.get_json()[0]["title"] == "Cert"
