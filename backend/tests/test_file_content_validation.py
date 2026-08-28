"""
Regression coverage for content_matches_extension() (app.py, S-08) --
specifically the .doc content-sniffing bug found and fixed in this pass:
the function only read the first 261 bytes of an upload before handing
them to filetype.guess(), but filetype's own Doc matcher
(filetype/types/document.py) needs buf[512:516] (and a fallback
signature at buf[2075:2142]) to recognize a real Office 97-2003 .doc
file. With only 261 bytes, every real .doc file's signature check was
unreachable, so match() always returned False and every legitimate .doc
upload -- CV, credential, employer verification document, all three
routes advertise .doc as accepted -- was rejected with "file content
does not match its extension." No test in this suite exercised this
path with real .doc bytes before this file existed.
"""
import io

from werkzeug.datastructures import FileStorage

from conftest import register_user, auth_headers, FAKE_PDF_BYTES

# Minimal bytes satisfying filetype's Doc matcher (filetype/types/
# document.py): the OLE/CFBF container magic at the very start, plus the
# Word-specific sub-signature at offset 512-516 -- the exact signature
# that a 261-byte read can never reach (515 > 261).
FAKE_DOC_BYTES = (
    b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
    + b"\x00" * (512 - 8)
    + b"\xEC\xA5\xC1\x00"
    + b"\x00" * 50
)


def test_content_matches_extension_accepts_a_real_doc_file():
    import app as app_module

    fs = FileStorage(stream=io.BytesIO(FAKE_DOC_BYTES), filename="resume.doc")
    assert app_module.content_matches_extension(fs, "resume.doc") is True


def test_content_matches_extension_still_rejects_a_mislabeled_doc():
    """A non-.doc file renamed to claim the extension must still be rejected --
    guards against a fix that reads more bytes but stops actually checking them."""
    import app as app_module

    fs = FileStorage(stream=io.BytesIO(b"not actually a word document"), filename="resume.doc")
    assert app_module.content_matches_extension(fs, "resume.doc") is False


def test_content_matches_extension_still_accepts_a_real_pdf():
    """Guards the fix (261 -> 2200 byte read) against accidentally breaking
    the formats that already worked."""
    import app as app_module

    fs = FileStorage(stream=io.BytesIO(FAKE_PDF_BYTES), filename="cert.pdf")
    assert app_module.content_matches_extension(fs, "cert.pdf") is True


def test_issue_credential_accepts_a_real_doc_upload_end_to_end(client):
    """Same bug, exercised through the real upload route rather than the
    unit function directly -- proves the fix is actually wired in, not
    just correct in isolation."""
    a = register_user(client)
    resp = client.post(
        "/issue_credential",
        data={"title": "Cert", "issuer": "Inst", "file": (io.BytesIO(FAKE_DOC_BYTES), "cert.doc")},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
