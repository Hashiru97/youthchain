import io

from conftest import register_user, auth_headers, FAKE_PDF_BYTES

import app as app_module

# Captured at module-load time, before conftest's autouse
# _no_real_blockchain_subprocess fixture (which stubs
# app_module._write_onchain_tx_for_credential to a no-op for every test)
# ever runs for any test in this session. Needed by the "skipped when
# already registered" test below, which is specifically about that
# function's own real internal logic -- calling through
# app_module._write_onchain_tx_for_credential there would just call the
# autouse stub instead and trivially "pass" for the wrong reason.
_real_write_onchain_tx_for_credential = app_module._write_onchain_tx_for_credential


def _issue(client, token, path="/issue_credential", content=FAKE_PDF_BYTES):
    return client.post(
        path,
        data={"title": "Cert", "issuer": "Inst", "file": (io.BytesIO(content), "c.pdf")},
        headers=auth_headers(token),
        content_type="multipart/form-data",
    )


def test_issue_credential_degrades_gracefully_when_onchain_write_fails(client):
    """
    Real test bug found running this suite against a real Postgres instance
    for the first time (CI only ever ran SQLite before this pass): this
    originally asserted credential_id == 1, which only ever held by
    coincidence of SQLite's rowid-reuse behavior after the client fixture's
    per-test DELETE FROM (an empty SQLite table reissues id 1 next insert).
    Postgres sequences never reset on DELETE, so the same assertion failed
    for real the moment a different id came out of a shared counter that
    prior tests in the run had already advanced. The actual thing this test
    is about is the onchain_tx degradation, not the specific id value.
    """
    a = register_user(client)
    resp = _issue(client, a["access_token"])
    assert resp.status_code == 201
    body = resp.get_json()
    assert isinstance(body["credential_id"], int)
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


def test_two_users_uploading_same_original_filename_do_not_collide_on_disk(client):
    """
    Real bug found via live reproduction, not assumed: _issue_credential_internal
    used to save files as plain secure_filename(original_name) with no
    uniqueness prefix (unlike every other upload path in this codebase —
    /apply, employer verification, message attachments). Two different
    users uploading a file with the same original name (e.g. both named
    "certificate.pdf") collided on the same disk path: the second save
    silently overwrote the first's file, while the first Credential row
    kept its *original* content hash — so the older credential's stored
    hash stopped matching what was actually on disk, and downloading it
    served a different user's document. Fixed with the same S-02
    unique-prefix pattern used everywhere else.
    """
    import os

    import app as app_module

    a = register_user(client, email="alice@test.com", phone="111", name="Alice")
    b = register_user(client, email="bob@test.com", phone="222", name="Bob")

    alice_content = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nALICE REAL CONTENT\n"
    bob_content = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nBOB DIFFERENT CONTENT ENTIRELY\n"

    alice_resp = _issue(client, a["access_token"], content=alice_content)
    bob_resp = _issue(client, b["access_token"], content=bob_content)
    assert alice_resp.status_code == 201
    assert bob_resp.status_code == 201

    alice_cred_id = alice_resp.get_json()["credential_id"]
    bob_cred_id = bob_resp.get_json()["credential_id"]
    assert alice_cred_id != bob_cred_id

    with app_module.app.app_context():
        alice_cred = app_module.Credential.query.get(alice_cred_id)
        bob_cred = app_module.Credential.query.get(bob_cred_id)
        # Different saved filenames despite the identical original upload
        # name ("c.pdf", from the shared _issue() helper) -- this is what
        # actually prevents the collision.
        assert alice_cred.file_path != bob_cred.file_path

        alice_path = os.path.join(app_module.app.config["UPLOAD_FOLDER"], alice_cred.file_path)
        with open(alice_path, "rb") as f:
            on_disk = f.read()
        assert on_disk == alice_content
        assert on_disk != bob_content

        assert alice_cred.hash == app_module.generate_file_hash(alice_path)


def test_repeated_uploads_are_rate_limited(client, monkeypatch):
    """
    Second real gap found in the same audit as the filename-collision bug
    above: no upload endpoint had any throttle at all, only the 16MB
    per-file size cap -- nothing stopped a burst of many uploads filling
    disk. _upload_rate_limited()/_record_upload_attempt() (app.py) close
    that; this exercises it end-to-end against a real endpoint rather than
    just unit-testing the limiter function in isolation.
    """
    import app as app_module

    # Use a small budget so the test doesn't need 20 real uploads to prove
    # the mechanism -- the mechanism itself (sliding-window key reuse) is
    # identical regardless of the configured limit.
    monkeypatch.setattr(app_module, "_UPLOAD_MAX_ATTEMPTS", 3)

    a = register_user(client)
    for i in range(3):
        resp = _issue(client, a["access_token"], content=FAKE_PDF_BYTES + str(i).encode())
        assert resp.status_code in (200, 201), f"upload {i} should succeed, got {resp.status_code}"

    fourth = _issue(client, a["access_token"], content=FAKE_PDF_BYTES + b"4")
    assert fourth.status_code == 429
    assert "Too many uploads" in fourth.get_json()["error"]


def test_two_users_uploading_byte_identical_content_each_get_their_own_credential(client):
    """
    Real bug found via a live reproduction while reviewing the blockchain
    integration (not just code reading): Credential.hash used to be
    globally unique, so two DIFFERENT users uploading files with the same
    content (a realistic case -- template-generated certificates from the
    same issuing institution) collided. The second user's upload silently
    returned the FIRST user's existing row: their own title was discarded,
    they got a 200 "already exists" response pointing at someone else's
    credential_id, and it never appeared in their own /passport. Reproduced
    live: Bob's passport stayed empty. Fixed by scoping uniqueness to
    (user_id, hash) -- see Credential.__table_args__'s docstring for the
    full reasoning, including why this doesn't change what the on-chain
    contract itself actually guarantees (it was never tracking per-user
    ownership in the first place, only hash existence).
    """
    alice = register_user(client, email="alice2@test.com", phone="311", name="Alice")
    bob = register_user(client, email="bob2@test.com", phone="322", name="Bob")

    identical_content = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nSAME TEMPLATE CERTIFICATE BYTES\n"

    def issue_with_title(token, title):
        return client.post(
            "/issue_credential",
            data={"title": title, "issuer": "Some Institute", "file": (io.BytesIO(identical_content), "cert.pdf")},
            headers=auth_headers(token),
            content_type="multipart/form-data",
        )

    alice_resp = issue_with_title(alice["access_token"], "Alice's Certificate")
    bob_resp = issue_with_title(bob["access_token"], "Bob's Certificate")

    # Both succeed as genuinely new credentials now, not one real + one
    # silent "already exists" pointing at the wrong owner.
    assert alice_resp.status_code == 201
    assert bob_resp.status_code == 201

    alice_cred_id = alice_resp.get_json()["credential_id"]
    bob_cred_id = bob_resp.get_json()["credential_id"]
    assert alice_cred_id != bob_cred_id

    alice_passport = client.get(
        f"/passport/{alice['user']['id']}", headers=auth_headers(alice["access_token"])
    ).get_json()
    bob_passport = client.get(
        f"/passport/{bob['user']['id']}", headers=auth_headers(bob["access_token"])
    ).get_json()

    assert [c["id"] for c in alice_passport] == [alice_cred_id]
    assert [c["id"] for c in bob_passport] == [bob_cred_id]
    assert alice_passport[0]["title"] == "Alice's Certificate"
    assert bob_passport[0]["title"] == "Bob's Certificate"
    # Same underlying content -> same hash, on purpose (that's the whole
    # point of hashing) -- just no longer the same row/owner.
    assert alice_passport[0]["hash"] == bob_passport[0]["hash"]


def test_onchain_write_is_skipped_when_hash_already_registered(monkeypatch):
    """
    Companion fix to the cross-user collision test above: the contract's
    registerCredential() is unique by hash GLOBALLY (see
    YouthChainRegistry.sol), so once Alice's row has registered a hash
    on-chain, attempting to register Bob's row for that same hash would
    just revert every single time -- a guaranteed-failure ~30-90s
    subprocess call for nothing. _write_onchain_tx_for_credential now
    checks _check_onchain_registered() first and skips the doomed write
    entirely. Tested directly against the function (bypassing the HTTP
    layer, and the autouse fixture that stubs this whole function out for
    every other test) so the subprocess call itself can be asserted never
    to happen.
    """
    monkeypatch.setattr(app_module, "_check_onchain_registered", lambda hash_hex: True)

    called = []
    monkeypatch.setattr(
        app_module,
        "_run_hardhat_script",
        lambda script, hash_hex, timeout=90: called.append(script) or "MINED_TX:0xshouldnothappen",
    )

    fake_credential = app_module.Credential(
        id=999, user_id=1, title="x", issuer="y", hash="a" * 64
    )
    # The real function, not app_module._write_onchain_tx_for_credential --
    # see the module-level comment on _real_write_onchain_tx_for_credential
    # for why (the autouse fixture stubs that attribute for every test).
    result = _real_write_onchain_tx_for_credential(fake_credential)

    assert result is None
    assert called == [], "registerCredential.js should never run when the hash is already registered"


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


def test_issue_credential_response_reports_pending_onchain_status(client):
    """
    BL-18 fix: the on-chain write moved off the request path (see
    _write_onchain_tx_for_credential_async's docstring for the full
    reasoning -- this used to block the whole request for up to ~90s). A
    freshly-issued credential's onchain_tx is genuinely unset at response
    time now, in every case, not just the "chain unreachable" case the
    other degrade-gracefully test covers -- the explicit onchain_status
    field says so rather than leaving the caller to guess what a null
    onchain_tx means immediately after a 201.
    """
    a = register_user(client)
    resp = _issue(client, a["access_token"])
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["onchain_tx"] is None
    assert body["onchain_status"] == "pending"


def test_onchain_write_completion_updates_credential_and_notifies_owner(client, monkeypatch):
    """
    The other half of the BL-18 fix: when the backgrounded write actually
    succeeds, _write_onchain_tx_for_credential_async must (a) persist the
    real tx hash onto the credential and (b) tell the owner via the same
    notify_user() channel every other real-time notification in this app
    already uses (new_message, application_status_changed, ...) -- a
    credential that's confirmed on-chain with nothing ever telling the
    owner it happened would be a real, silent regression from the old
    synchronous behavior, where the original HTTP response at least
    carried the tx hash directly.

    conftest's autouse fixture already runs the background write inline
    (deterministic, no real thread) -- this test only needs to make that
    inline write actually "succeed" instead of the default no-op stub.
    """
    monkeypatch.setattr(app_module, "_write_onchain_tx_for_credential", lambda credential: "0xdeadbeef")

    a = register_user(client)
    resp = _issue(client, a["access_token"])
    assert resp.status_code == 201
    cred_id = resp.get_json()["credential_id"]

    with app_module.app.app_context():
        cred = app_module.db.session.get(app_module.Credential, cred_id)
        assert cred.onchain_tx == "0xdeadbeef"

        notif = app_module.Notification.query.filter_by(
            user_id=a["user"]["id"], type="credential_onchain_confirmed"
        ).first()
        assert notif is not None
        assert "verified on-chain" in notif.title.lower()


def test_issue_credential_returns_before_onchain_write_completes(client, monkeypatch):
    """
    Proves the request path is genuinely non-blocking now, not just that
    the response happens to show a pending status (the test above) --
    without this, a bug that accidentally re-introduced a synchronous
    `.join()` or blocking call somewhere in the response path could still
    report "pending" while actually blocking the full ~90s, and none of
    the other tests here would catch it since they don't measure timing.

    Overrides conftest's default inline-synchronous stub back to the real
    threaded _spawn_background_onchain_write for this one test -- a slow
    fake on-chain write (gated on a threading.Event) proves the HTTP
    response returns well before that slow call finishes, then the test
    explicitly releases and waits for it before asserting the eventual
    DB update, rather than relying on real wall-clock sleeps for either
    side.
    """
    import threading
    import time

    release_write = threading.Event()
    write_started = threading.Event()

    def slow_write(credential):
        write_started.set()
        release_write.wait(timeout=5)
        return "0xslowtx"

    monkeypatch.setattr(app_module, "_write_onchain_tx_for_credential", slow_write)
    monkeypatch.setattr(
        app_module,
        "_spawn_background_onchain_write",
        lambda credential_id: threading.Thread(
            target=app_module._write_onchain_tx_for_credential_async,
            args=(app_module.app, credential_id),
            daemon=True,
        ).start(),
    )

    a = register_user(client)

    start = time.monotonic()
    resp = _issue(client, a["access_token"])
    elapsed = time.monotonic() - start

    assert resp.status_code == 201
    assert resp.get_json()["onchain_status"] == "pending"
    # The response came back without waiting on slow_write's 5s-capped
    # wait -- proves the request path doesn't block on it, not just that
    # the eventual value happens to be null.
    assert elapsed < 2, f"request took {elapsed:.2f}s -- appears to be blocking on the on-chain write again"
    assert write_started.wait(timeout=2), "background write never started at all"

    release_write.set()  # let the background thread finish
    cred_id = resp.get_json()["credential_id"]

    for _ in range(50):  # up to ~5s, polling rather than a fixed sleep
        with app_module.app.app_context():
            cred = app_module.db.session.get(app_module.Credential, cred_id)
            if cred.onchain_tx:
                break
        time.sleep(0.1)

    assert cred.onchain_tx == "0xslowtx"


def test_hardhat_network_is_configurable(monkeypatch):
    """
    Real gap found alongside the async-write fix, via the same
    full-blockchain-integration review: _run_hardhat_script used to
    hardcode `--network localhost` with no override anywhere, meaning the
    backend could never write to or verify against a real deployed
    network no matter how blockchain/.env or hardhat.config.js were
    configured. HARDHAT_NETWORK (app.py, .env.example) fixes that --
    this confirms the configured value actually reaches the constructed
    hardhat command, not just that the env var exists.
    """
    monkeypatch.setattr(app_module, "HARDHAT_NETWORK", "sepolia")

    captured = {}

    class FakeCompletedProcess:
        returncode = 0
        stdout = "REGISTERED:false"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompletedProcess()

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module.shutil, "which", lambda name: "npx")

    app_module._run_hardhat_script("checkRegistered.js", "a" * 64)

    assert "--network" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--network") + 1] == "sepolia"
