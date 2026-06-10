# hrfunc-web

Flask web app for the HRfunc HRF submission and collaboration platform.
The website guides users through using the HRfunc tool. It also forwards
HRF JSON uploads (augmented with submitter metadata) to a backend API so
the HRFs can be integrated into the HRtree for the wider neuroimaging
community.

## Environment variables

| Variable | Purpose |
|---|---|
| `HRFUNC_UPLOAD_URL` | Upload backend endpoint. Required — the app logs a startup warning and all uploads fail if unset. |
| `HRFUNC_API_KEY` | `x-api-key` value sent to the upload backend. |
| `HRFUNC_ACCESS_CLIENT_ID` | Service-token client id for a backend gated behind service-token auth. |
| `HRFUNC_ACCESS_CLIENT_SECRET` | Paired with the above. |
| `SECRET_KEY` | Flask session secret. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` | Confirmation-email delivery. |

## Local development

```bash
pip install -r requirements-dev.txt   # includes pytest + responses
pytest                                # run the test suite
python app.py                         # run dev server on :8000
```

## Tests

The test suite lives under `tests/` and uses `responses` to mock outbound
HTTP, so tests run without needing a live backend. Coverage focuses on the
upload-forward contract: the `x-api-key` header, service-token (`CF-Access`)
header propagation, and the `_hrf_submission` envelope sent to the backend.
