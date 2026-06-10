"""Upload-forward behavior: the frontend proxies HRF uploads to the backend.

These tests pin the contract of the upload-forward code in `app.py`:

- An upload POSTs the augmented JSON to `UPLOAD_URL` with the `x-api-key` header.
- Service-token (`CF-Access-*`) headers are attached when configured.
- The forwarded body carries the `_hrf_submission` envelope of submitter metadata.
- Backend failures (non-200 or transport errors) surface to the user without
  raising — the request still ends in a redirect.
"""

from __future__ import annotations

import email.parser
import json

import responses
from responses import matchers

UPLOAD_URL = "https://backend.example/upload_json"
API_KEY = "backend-key-abc123"


def _patch_app_config(monkeypatch, flask_app):
    monkeypatch.setattr(flask_app, "UPLOAD_URL", UPLOAD_URL)
    monkeypatch.setattr(flask_app, "API_KEY", API_KEY)


@responses.activate
def test_upload_forwards_to_backend_with_api_key(monkeypatch, client, flask_app, sample_upload):
    """A successful upload makes exactly one call to UPLOAD_URL with the x-api-key."""
    _patch_app_config(monkeypatch, flask_app)
    responses.add(
        responses.POST, UPLOAD_URL,
        match=[matchers.header_matcher({"x-api-key": API_KEY})],
        json={"ok": True}, status=200,
    )

    response = client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")

    assert response.status_code == 302  # redirect back to upload page
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url.startswith(UPLOAD_URL)


@responses.activate
def test_cf_access_headers_propagated_to_backend(monkeypatch, client, flask_app, sample_upload):
    """When the service-token env vars are set, the CF-Access headers are
    attached to the forwarded request — they're the edge auth that gates the
    backend path."""
    _patch_app_config(monkeypatch, flask_app)
    monkeypatch.setattr(flask_app, "ACCESS_CLIENT_ID", "fake-id-123")
    monkeypatch.setattr(flask_app, "ACCESS_CLIENT_SECRET", "fake-secret-456")
    responses.add(responses.POST, UPLOAD_URL, json={"ok": True}, status=200)

    client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")

    assert len(responses.calls) == 1
    sent = responses.calls[0].request
    assert sent.headers["CF-Access-Client-Id"] == "fake-id-123"
    assert sent.headers["CF-Access-Client-Secret"] == "fake-secret-456"


def _extract_json_file_from_multipart(request_body_bytes: bytes, content_type: str) -> dict:
    """Parse the `jsonFile` part out of a multipart/form-data request body.

    Tests need to assert what JSON the frontend actually forwarded to the
    backend (e.g., that the `_hrf_submission` envelope is present). The
    `responses` library exposes the raw request body; we use stdlib's
    `email` module to parse the multipart since it's already in the stdlib.
    """
    # Build a synthetic email-style envelope so email.parser can split parts.
    header_blob = f"Content-Type: {content_type}\r\n\r\n".encode()
    parser = email.parser.BytesParser()
    msg = parser.parsebytes(header_blob + request_body_bytes)
    for part in msg.walk():
        # The jsonFile part has Content-Disposition: form-data; name="jsonFile"
        disposition = part.get("Content-Disposition", "")
        if 'name="jsonFile"' in disposition:
            payload = part.get_payload(decode=True)
            return json.loads(payload.decode("utf-8"))
    raise AssertionError("no jsonFile part found in multipart body")


@responses.activate
def test_backend_receives_hrf_submission_envelope(monkeypatch, client, flask_app, sample_upload):
    """The forwarded body carries the `_hrf_submission` envelope with submitter
    metadata, original_filename, stored_filename, and uploaded_at."""
    _patch_app_config(monkeypatch, flask_app)
    responses.add(responses.POST, UPLOAD_URL, json={"ok": True}, status=200)

    client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")

    assert len(responses.calls) == 1
    body = _extract_json_file_from_multipart(
        responses.calls[0].request.body, responses.calls[0].request.headers["Content-Type"]
    )

    envelope = body["_hrf_submission"]
    assert envelope["study"] == "test-study"
    assert envelope["email"] == "test@example.com"
    assert envelope["doi"] == "10.1000/test.0001"
    assert envelope["original_filename"].endswith(".json")
    assert envelope["stored_filename"].startswith("study_2026-05-11")
    assert envelope["uploaded_at"].startswith("20")  # ISO date with 4-digit year


def _flashes(client):
    """Read pending flash messages out of the session without rendering them.

    The view redirects without following, so flashes stay in the session as
    (category, message) tuples. Asserting on them pins that the 302 came from
    the backend-failure path specifically, not an earlier guard that also
    redirects (rate-limit, bad file, bad JSON)."""
    with client.session_transaction() as sess:
        return sess.get("_flashes", [])


@responses.activate
def test_backend_non_200_surfaces_to_user(monkeypatch, client, flask_app, sample_upload):
    """A backend rejection (non-200) is flashed to the user but doesn't raise —
    the request still ends in the normal redirect."""
    _patch_app_config(monkeypatch, flask_app)
    responses.add(responses.POST, UPLOAD_URL, body="Invalid API key", status=401)

    response = client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")

    assert response.status_code == 302  # frontend still redirects (with an error flash)
    assert len(responses.calls) == 1
    assert any(cat == "error" and "Upload failed" in msg for cat, msg in _flashes(client))


@responses.activate
def test_backend_transport_exception_handled(monkeypatch, client, flask_app, sample_upload):
    """If the backend call raises (e.g., connection refused), the error is
    caught, flashed, and the user sees the normal redirect rather than a 500."""
    _patch_app_config(monkeypatch, flask_app)
    # No mock registered for UPLOAD_URL → responses raises ConnectionError when
    # the forward fires. The view catches it and flashes an error.

    response = client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")

    assert response.status_code == 302  # user sees normal redirect, no exception escapes
    assert any(cat == "error" and "Error contacting API" in msg for cat, msg in _flashes(client))


@responses.activate
def test_unset_upload_url_fails_gracefully(monkeypatch, client, flask_app, sample_upload):
    """HRFUNC_UPLOAD_URL is now required (no default). When unset, the forward
    posts to None and raises, which the view catches and flashes — no 500."""
    monkeypatch.setattr(flask_app, "UPLOAD_URL", None)
    monkeypatch.setattr(flask_app, "API_KEY", API_KEY)

    response = client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")

    assert response.status_code == 302
    assert any(cat == "error" and "Error contacting API" in msg for cat, msg in _flashes(client))
