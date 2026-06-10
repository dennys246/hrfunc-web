"""Shared pytest fixtures for hrfunc-web tests.

Tests load `app` (the Flask app) and use Flask's `app.test_client()` to drive
requests. The module-level env-driven constants (`UPLOAD_URL`, `API_KEY`) are
patched per-test via `monkeypatch.setattr` so each test exercises specific
config without polluting the others.

`responses` mocks `requests.post` so we can verify the upload forward happened
end-to-end without needing real network calls.
"""

from __future__ import annotations

import io

import pytest


@pytest.fixture
def flask_app(monkeypatch):
    """Import (or reload) the Flask app with rate-limiting disabled and a
    secret_key set, so the test client can POST repeatedly within seconds."""
    import app as app_module

    # Reset the in-memory rate-limit registry so tests don't trip the 5s window.
    app_module._last_upload_attempt.clear()
    # Make RATE_LIMIT_SECONDS effectively zero for tests; the rate-limit branch
    # still runs (so its logic is covered) but always passes.
    monkeypatch.setattr(app_module, "RATE_LIMIT_SECONDS", 0)
    # Sessions need a secret key even in tests.
    monkeypatch.setattr(app_module.app, "secret_key", "test-secret-not-used-in-prod")

    return app_module


@pytest.fixture
def client(flask_app):
    """Flask test client tied to the patched app module."""
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


@pytest.fixture
def sample_upload():
    """Builds a multipart form payload identical in shape to what the real
    upload form submits."""

    def _build(body: bytes | None = None):
        if body is None:
            body = b'{"some": "hrf-content"}'
        return {
            "jsonFile": (io.BytesIO(body), "study_2026-05-11.json"),
            # A subset of the real form fields — enough to exercise the
            # envelope-extraction path in app.py.
            "name": "Test Researcher",
            "study": "test-study",
            "email": "test@example.com",
            "doi": "10.1000/test.0001",
        }

    return _build
