# hrfunc-web

Flask web app for the HRfunc HRF submission and collaboration platform.
The website guides users through using the HRfunc tool. It also forwards
HRF JSON uploads (augmented with submitter metadata) to a backend API so
the HRFs can be integrated into the HRtree for the wider neuroimaging
community.

## Environment variables

| Variable | Purpose |
|---|---|
| `HRFUNC_UPLOAD_URL` | Primary (authoritative) upload backend. Defaults to `https://flask.jib-jab.org/upload_json`. |
| `HRFUNC_API_KEY` | `x-api-key` value sent to the primary backend. |
| `HRFUNC_SHADOW_URL` | (Phase 3 dual-write) When set, every upload is also forwarded here for shadow validation. Failures never affect the user. |
| `HRFUNC_API_KEY_HRSERV` | `x-api-key` value sent to the shadow backend (HRServ) — distinct from the primary key because HRServ has its own argon2-hashed `api_keys` table. |
| `HRFUNC_ACCESS_CLIENT_ID` | (Phase 1.5) Cloudflare Access service token id. Sent to whichever backend is gated by Cloudflare Access. |
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
dual-write contract (Phase 3): API-key routing, shadow-on-all-statuses,
and shadow-failure isolation from the user response.
