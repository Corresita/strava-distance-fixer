# Strava Distance Fixer

A small Flask service that listens for Strava activity-created webhooks and rewrites each new activity's distance to a specific preference form `N.NN` km (e.g. `7.07 km`, `12.12 km`). Designed to run as a long-lived web service on Railway.

## What it does

1. Strava pushes a webhook event when a new activity is created.
2. The service waits for the activity to finish processing on Strava's side, then fetches it via the v3 API.
3. It computes the target distance: take the integer kilometer part `N`, round to `N.NN km`.
4. It writes the new distance back to Strava and verifies that the change persisted.

If the change does not persist, the service falls back to a different write path automatically (see below).

## The distance-revert problem

Strava's public `PUT /activities/{id}` endpoint accepts a `distance` field, but for any **GPS-recorded** activity the server silently recomputes distance from the GPS stream after the write — so the API update appears to succeed and is then reverted within seconds.

This service handles both cases:

- **Manual activities** (no GPS stream): updated through the official v3 API.
- **GPS activities**: routed directly to Strava's web edit form (`/activities/{id}/edit`) using a logged-in session cookie, which is the only path the server actually persists.

Imperial-unit accounts are detected via `GET /athlete` and the value is converted to miles before submission, since the web form parses the field according to the athlete's display preference.

Full root-cause analysis lives in [docs/distance-revert-issue.md](docs/distance-revert-issue.md).

## Architecture

```
Strava webhook ──▶ POST /webhook ──▶ background thread per activity
                                         │
                                         ├─ GET  /api/v3/activities/{id}     (poll until ready)
                                         ├─ GET  /api/v3/athlete              (unit preference)
                                         │
                                         ├─ manual activity ──▶ PUT /api/v3/activities/{id}
                                         │
                                         └─ GPS activity    ──▶ GET  /activities/{id}/edit
                                                                POST /activities/{id}        (form submit + CSRF)
                                                                GET  /api/v3/activities/{id} (verify persisted)
```

OAuth tokens auto-refresh and are persisted to `/tmp/tokens.json`. If Railway API credentials are configured, refreshed tokens are also pushed back into the service's environment variables so they survive container restarts.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/` | Health check |
| `GET`  | `/webhook` | Strava subscription verification (hub challenge) |
| `POST` | `/webhook` | Strava activity events |
| `GET`  | `/fix/<activity_id>` | Manually trigger a fix (useful for testing / backfill) |

## Configuration

All configuration is via environment variables. See [.env.example](.env.example) for the full list.

Required:

- `CLIENT_ID`, `CLIENT_SECRET` — Strava OAuth app credentials
- `ACCESS_TOKEN`, `REFRESH_TOKEN`, `EXPIRES_AT` — initial OAuth tokens (the app refreshes them after that)
- `VERIFY_TOKEN` — any random string, must match the value registered with the Strava webhook subscription
- `STRAVA_SESSION_COOKIE` — value of the `_strava4_session` cookie from a logged-in browser session; required for the GPS-activity web-form fallback

Optional (for token persistence on Railway):

- `RAILWAY_API_TOKEN`, `RAILWAY_PROJECT_ID`, `RAILWAY_SERVICE_ID`, `RAILWAY_ENVIRONMENT_ID`

The session cookie expires periodically — when the logs show `Strava session cookie expired or invalid`, grab a fresh `_strava4_session` from your browser DevTools and update the env var.

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in values
export $(grep -v '^#' .env | xargs)
python app.py         # serves on :8080
```

To register the webhook (one-time, after deploying):

```bash
curl -X POST https://www.strava.com/api/v3/push_subscriptions \
  -F client_id=$CLIENT_ID \
  -F client_secret=$CLIENT_SECRET \
  -F callback_url=https://<your-host>/webhook \
  -F verify_token=$VERIFY_TOKEN
```

## Deployment

The repository ships with a `Procfile` for Railway / Heroku-style platforms:

```
web: gunicorn app:app --workers 1 --threads 4 --timeout 120
```

A single worker is intentional — token state and the web session are held in-process and protected by locks; scaling out would require moving that state to shared storage.

## Project layout

```
app.py              Flask app, webhook handlers, distance-fix logic
test_app.py         Tests
requirements.txt    Python dependencies
Procfile            Gunicorn entry point
.env.example        Environment variable template
docs/               Strava API reference notes + distance-revert RCA
CHANGELOG.md        Release notes
```

## References

The `docs/` directory contains an offline copy of the relevant Strava API documentation and the full investigation of the GPS-distance-revert behavior that motivated the web-form fallback.
