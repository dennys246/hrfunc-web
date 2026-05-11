"""Phase 3 dual-write behavior: shadow forwards to HRServ.

These tests pin the contract introduced by the shadow-write code in `app.py`:

- `SHADOW_URL` unset → only the primary backend is called.
- `SHADOW_URL` set → both backends are called.
- Primary's API key is `HRFUNC_API_KEY`; shadow's is `HRFUNC_API_KEY_HRSERV`.
- Shadow fires on EVERY primary outcome (success, failure, exception).
- Shadow failures never affect the user-visible response from the primary.
- Status-code divergence is logged at WARN level for operator visibility.

The shadow forward runs in a daemon thread, so each test joins on the
expected number of HTTP calls (via `responses.calls`) before asserting,
with a short timeout fallback.
"""

from __future__ import annotations

import time

import pytest
import responses
from responses import matchers

PRIMARY_URL = "https://primary.example/upload_json"
SHADOW_URL = "https://shadow.example/upload_json"
PRIMARY_KEY = "primary-key-abc123"
SHADOW_KEY = "shadow-key-xyz789"


def _wait_for_calls(expected: int, timeout: float = 2.0) -> None:
    """Poll responses.calls until we see `expected` requests or time out.

    The shadow forward is in a daemon thread; tests need to wait briefly
    for it to complete before asserting. 2s is comfortably more than
    the requests.post call needs against a `responses`-mocked endpoint.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(responses.calls) >= expected:
            return
        time.sleep(0.02)


def _patch_app_config(monkeypatch, flask_app, *, shadow_url=None, hrserv_key=None):
    monkeypatch.setattr(flask_app, "UPLOAD_URL", PRIMARY_URL)
    monkeypatch.setattr(flask_app, "SHADOW_URL", shadow_url)
    monkeypatch.setattr(flask_app, "API_KEY", PRIMARY_KEY)
    monkeypatch.setattr(flask_app, "HRSERV_API_KEY", hrserv_key)


@responses.activate
def test_no_shadow_url_means_no_shadow_call(monkeypatch, client, flask_app, sample_upload):
    """SHADOW_URL unset → only the primary backend is called, no second request."""
    _patch_app_config(monkeypatch, flask_app, shadow_url=None)
    responses.add(responses.POST, PRIMARY_URL, json={"ok": True}, status=200)

    response = client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")
    _wait_for_calls(1)

    assert response.status_code == 302  # redirect back to upload page
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url.startswith(PRIMARY_URL)


@responses.activate
def test_primary_200_shadow_200_both_called_with_correct_keys(
    monkeypatch, client, flask_app, sample_upload
):
    """Happy path: both backends called, each with its own API key."""
    _patch_app_config(monkeypatch, flask_app, shadow_url=SHADOW_URL, hrserv_key=SHADOW_KEY)
    responses.add(
        responses.POST, PRIMARY_URL,
        match=[matchers.header_matcher({"x-api-key": PRIMARY_KEY})],
        json={"ok": True}, status=200,
    )
    responses.add(
        responses.POST, SHADOW_URL,
        match=[matchers.header_matcher({"x-api-key": SHADOW_KEY})],
        json={"ok": True, "id": 1}, status=200,
    )

    response = client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")
    _wait_for_calls(2)

    assert response.status_code == 302
    assert len(responses.calls) == 2
    # Primary must come first; shadow is fired after primary returns.
    assert responses.calls[0].request.url.startswith(PRIMARY_URL)
    assert responses.calls[1].request.url.startswith(SHADOW_URL)


@responses.activate
def test_shadow_failure_does_not_affect_user_response(
    monkeypatch, client, flask_app, sample_upload
):
    """Shadow 500 must not change the primary's 200 outcome shown to the user."""
    _patch_app_config(monkeypatch, flask_app, shadow_url=SHADOW_URL, hrserv_key=SHADOW_KEY)
    responses.add(responses.POST, PRIMARY_URL, json={"ok": True}, status=200)
    responses.add(responses.POST, SHADOW_URL, body="Internal Server Error", status=500)

    response = client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")
    _wait_for_calls(2)

    # User still gets the success redirect — shadow's 500 is logged, not surfaced.
    assert response.status_code == 302
    assert len(responses.calls) == 2


@responses.activate
def test_primary_failure_still_fires_shadow(monkeypatch, client, flask_app, sample_upload):
    """Shadow must fire even when the primary rejects — the primary-rejected/
    shadow-accepted divergence case is the one we most want to learn about."""
    _patch_app_config(monkeypatch, flask_app, shadow_url=SHADOW_URL, hrserv_key=SHADOW_KEY)
    responses.add(responses.POST, PRIMARY_URL, body="Invalid API key", status=401)
    responses.add(responses.POST, SHADOW_URL, json={"ok": True, "id": 7}, status=200)

    response = client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")
    _wait_for_calls(2)

    # Both backends were called even though primary returned non-200.
    assert len(responses.calls) == 2
    assert response.status_code == 302  # frontend still redirects (with a flash)


@responses.activate
def test_shadow_transport_exception_does_not_affect_user(
    monkeypatch, client, flask_app, sample_upload
):
    """If shadow raises (e.g., connection refused), the user still sees the
    primary's response and no exception escapes the daemon thread."""
    _patch_app_config(monkeypatch, flask_app, shadow_url=SHADOW_URL, hrserv_key=SHADOW_KEY)
    responses.add(responses.POST, PRIMARY_URL, json={"ok": True}, status=200)
    # No mock registered for SHADOW_URL → responses raises ConnectionError
    # when the shadow thread's requests.post fires. _shadow_forward catches it.

    response = client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")
    # Wait for the primary call AND for the shadow thread to have raised + been caught.
    _wait_for_calls(1)
    time.sleep(0.2)

    assert response.status_code == 302  # user sees normal redirect
    # Primary still fired exactly once; shadow attempted (and failed in the
    # daemon thread). The exception path is exercised but produces no visible
    # impact on the user, which is the whole point.
    primary_calls = [c for c in responses.calls if c.request.url.startswith(PRIMARY_URL)]
    assert len(primary_calls) == 1


@responses.activate
def test_shadow_uses_hrserv_key_not_primary_key(monkeypatch, client, flask_app, sample_upload):
    """Sanity check that the shadow forward sends HRFUNC_API_KEY_HRSERV on
    x-api-key, not HRFUNC_API_KEY. The two backends maintain independent key
    registries (legacy has its own, HRServ has argon2-hashed api_keys)."""
    _patch_app_config(monkeypatch, flask_app, shadow_url=SHADOW_URL, hrserv_key=SHADOW_KEY)
    responses.add(responses.POST, PRIMARY_URL, json={"ok": True}, status=200)
    responses.add(responses.POST, SHADOW_URL, json={"ok": True, "id": 1}, status=200)

    client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")
    _wait_for_calls(2)

    shadow_calls = [c for c in responses.calls if c.request.url.startswith(SHADOW_URL)]
    assert len(shadow_calls) == 1
    assert shadow_calls[0].request.headers["x-api-key"] == SHADOW_KEY


@responses.activate
def test_shadow_skipped_when_hrserv_key_missing(monkeypatch, client, flask_app, sample_upload):
    """If SHADOW_URL is set but HRFUNC_API_KEY_HRSERV is not, shadow is skipped
    entirely. Firing with the primary key would 401 every time and pollute the
    shadow window's divergence signal — better to skip and log a startup
    warning so the operator notices the misconfig."""
    _patch_app_config(monkeypatch, flask_app, shadow_url=SHADOW_URL, hrserv_key=None)
    responses.add(responses.POST, PRIMARY_URL, json={"ok": True}, status=200)
    # If shadow fires despite the missing key, this URL would be hit and the
    # call would land in responses.calls. We expect it NOT to.
    responses.add(responses.POST, SHADOW_URL, json={"ok": True}, status=200)

    client.post("/upload_json", data=sample_upload(), content_type="multipart/form-data")
    _wait_for_calls(1)
    time.sleep(0.2)  # give the shadow thread time to NOT fire

    # Only the primary call landed. Shadow stayed home.
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url.startswith(PRIMARY_URL)
