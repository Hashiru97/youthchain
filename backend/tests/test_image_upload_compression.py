"""
Regression coverage for _compress_uploaded_image_if_needed() -- the
minor hardening item found while reviewing "lightweight image usage":
every upload path sharing ALLOWED_EXTENSIONS (certificates, application
CV/supporting documents, employer verification documents, message
attachments) previously stored a phone-camera photo exactly as uploaded,
even when it was several MB larger than anything actually needed to stay
legible. See the function's own docstring in app.py for the full
reasoning, including why this must run before generate_file_hash() at
the one call site (credential upload) that hashes its result.
"""
import io
import os

from PIL import Image

from conftest import register_user, auth_headers, FAKE_PDF_BYTES

import app as app_module


def _jpeg_bytes(width, height):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 40, 200)).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _png_bytes(width, height):
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), color=(10, 200, 120, 255)).save(buf, format="PNG")
    return buf.getvalue()


# ----------------- _compress_uploaded_image_if_needed() unit tests -----------------

def test_compress_downscales_an_oversized_jpeg(tmp_path):
    path = tmp_path / "big.jpg"
    path.write_bytes(_jpeg_bytes(3000, 2400))
    original_size = path.stat().st_size

    app_module._compress_uploaded_image_if_needed(str(path))

    with Image.open(path) as img:
        assert max(img.size) <= app_module._MAX_IMAGE_UPLOAD_DIMENSION
        # Aspect ratio preserved (3000x2400 = 5:4)
        assert abs(img.size[0] / img.size[1] - 3000 / 2400) < 0.01
    assert path.stat().st_size < original_size


def test_compress_downscales_an_oversized_png(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(_png_bytes(2500, 2500))

    app_module._compress_uploaded_image_if_needed(str(path))

    with Image.open(path) as img:
        assert max(img.size) <= app_module._MAX_IMAGE_UPLOAD_DIMENSION


def test_compress_leaves_a_small_image_untouched(tmp_path):
    path = tmp_path / "small.jpg"
    original_bytes = _jpeg_bytes(400, 300)
    path.write_bytes(original_bytes)

    app_module._compress_uploaded_image_if_needed(str(path))

    assert path.read_bytes() == original_bytes


def test_compress_never_touches_a_pdf(tmp_path):
    path = tmp_path / "cert.pdf"
    path.write_bytes(FAKE_PDF_BYTES)

    app_module._compress_uploaded_image_if_needed(str(path))

    assert path.read_bytes() == FAKE_PDF_BYTES


def test_compress_fails_open_on_a_corrupt_image(tmp_path):
    """A file with an image extension that Pillow can't actually decode
    (shouldn't normally reach here past content_matches_extension(), but
    this function has to be safe regardless) must be left exactly as-is,
    never raise, and never leave a truncated/corrupted file behind."""
    path = tmp_path / "corrupt.jpg"
    path.write_bytes(b"not a real jpeg")

    app_module._compress_uploaded_image_if_needed(str(path))  # must not raise

    assert path.read_bytes() == b"not a real jpeg"


# ----------------- Wired into the real /issue_credential upload path -----------------

def test_issue_credential_compresses_an_oversized_jpeg_certificate(client):
    user = register_user(client)
    resp = client.post(
        "/issue_credential",
        data={
            "title": "Cert",
            "issuer": "Inst",
            "file": (io.BytesIO(_jpeg_bytes(3200, 2400)), "cert.jpg"),
        },
        headers=auth_headers(user["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code in (200, 201)
    credential_id = resp.get_json()["credential_id"]

    with app_module.app.app_context():
        credential = app_module.db.session.get(app_module.Credential, credential_id)
        stored_path = os.path.join(app_module.UPLOAD_FOLDER, credential.file_path)
        with Image.open(stored_path) as img:
            assert max(img.size) <= app_module._MAX_IMAGE_UPLOAD_DIMENSION
        # The hash recorded (and what would be written on-chain) must
        # match the FINAL, compressed bytes actually on disk -- not the
        # original pre-compression upload -- or a verifier re-hashing the
        # downloaded file later would get a mismatch.
        assert credential.hash == app_module.generate_file_hash(stored_path)


def test_issue_credential_pdf_certificate_is_unaffected(client):
    """Same endpoint, non-image content -- confirms the compression step
    is a true no-op for the far more common PDF certificate upload."""
    user = register_user(client)
    resp = client.post(
        "/issue_credential",
        data={"title": "Cert", "issuer": "Inst", "file": (io.BytesIO(FAKE_PDF_BYTES), "cert.pdf")},
        headers=auth_headers(user["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code in (200, 201)
    credential_id = resp.get_json()["credential_id"]

    with app_module.app.app_context():
        credential = app_module.db.session.get(app_module.Credential, credential_id)
        stored_path = os.path.join(app_module.UPLOAD_FOLDER, credential.file_path)
        assert open(stored_path, "rb").read() == FAKE_PDF_BYTES
