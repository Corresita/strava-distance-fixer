# Changelog

## [2.1.0] - 2026-05-16

Add HTTP wrapper so the sync can be triggered remotely from an iOS Shortcut — the original `python sync.py` CLI still works locally, but a Flask server (`sync_server.py`) exposes the same pipeline at `POST /sync` for phone-based invocation. Designed for Railway deployment.

### Added
- `sync_server.py` — Flask wrapper around `sync.run()`, gated by `X-Sync-Secret` header, bootstraps Garmin OAuth token from `GARMIN_TOKEN_B64` env var on startup so fresh containers don't trigger rate-limited SSO logins
- `Procfile` + `railway.toml` — gunicorn entrypoint for Railway (Railpack builder)
- `flask`, `gunicorn` back in `requirements.txt`
- `?no_delete=1` URL parameter on `/sync` to skip the Strava find-and-delete step
- Debug logging in `find_activity_near`: search window, candidate count, per-activity distance diff, final match decision
- Debug logging in `delete_activity`: token prefix, explicit `flush=True` to defeat gunicorn stdout buffering
- Browser-style `User-Agent` header on all Strava requests (attempt to work around suspected cloud-IP throttling)
- `_persist_env` now patches `os.environ` even when `.env` is absent (Railway has no `.env` file, only platform env vars) — avoids re-refreshing the Strava access token on every single API call

### Discovered (the wall this version hit)
End-to-end pipeline runs correctly on Railway, but Strava's `DELETE /activities/{id}` returns **`401 "Application internal invalid"`** for this OAuth app — for **any** real activity, from **any** origin (local laptop or Railway), regardless of User-Agent. Same token at the same instant can `PUT` the activity (200) and `DELETE` a nonexistent ID (404 — proving generic delete permission exists), but cannot `DELETE` a real activity. Strava appears to have restricted DELETE on apps in the default "Limited Access" tier.

Net effect for v2.1: the delete-and-reupload pipeline cannot complete. The script logs the failure, falls back to uploading the unmodified TCX, which then also dedup-fails against the original auto-synced copy. See `docs/journey.md` Approach 6 for the full diagnostic trail.

### Operational note
Until the DELETE block is worked around (Strava app tier upgrade, or a different attack vector — see journey.md), the current state on Railway is **non-functional for GPS runs**. Running locally with `python sync.py` has the same DELETE failure. The pipeline only fully works in test scenarios where no Strava auto-synced copy exists (e.g., the time-shifted upload test from 2.0.0 validation).

---

## [2.0.0] - 2026-05-15

Rewrite. The "modify Strava activity after upload" approach proved fundamentally unworkable — Strava's `UpdatableActivity` schema has no `distance` field, and the web edit form for GPS activities lost its distance input around 2024. The webhook-and-fallback architecture was abandoned.

The replacement is a local command-line tool that intercepts the data path **before** Strava sees it: pull the activity from Garmin Connect, scale the GPS track itself, upload the modified TCX to Strava as a fresh activity.

### Added
- `sync.py` — single entry point, processes the latest Garmin running activity (or one by ID) in ~30 seconds
- `garmin_client.py` — Garmin Connect login with cached OAuth1 token (good for ~1 year, MFA only on first run)
- `tcx_scaler.py` — uniform GPS-path scaling anchored at the first trackpoint; scales lat/lng, per-trackpoint distance and speed, lap distances. Times / HR / cadence / power / altitude untouched.
- `strava_uploader.py` — Strava OAuth refresh, multipart upload, processing-status poll
- `reauth_strava.py` — local-callback OAuth helper for one-shot token rotation
- `history.json` — per-run audit trail; `sync.log` — append-only log
- TCX scaling falls back to uploading the unmodified file on failure, so the activity always lands on Strava

### Removed
- Flask webhook server (`app.py`, `test_app.py`)
- Railway deployment config (`Procfile`)
- Web form simulation, session cookie management, CSRF handling — all dead ends with Strava's current GPS-activity policy
- `STRAVA_SESSION_COOKIE`, `RAILWAY_*` env vars

### Operational note
Keep Garmin Connect → Strava direct sync **enabled**. The script waits for that auto-synced copy to appear on Strava, deletes it, then uploads the scaled version. This keeps non-running activities (cycling, strength, swims) flowing untouched through the original auto-sync, while runs get the `N.NN` treatment.

If the scaled upload fails the script falls back to uploading the original (un-scaled) TCX, so a run never disappears from Strava — at worst its distance is unmodified.

---

## [1.x] (deprecated, see git history)

The 1.x series was a Flask webhook server that tried to rewrite Strava distances after upload. It worked only for manually-entered activities; GPS activities were silently reverted by Strava's stream-recomputation. Replaced wholesale in 2.0.

---

## [1.4.0] - 2026-05-10

### Added
- Verify distance after PUT: GET activity again after 5s, if Strava reverted, wait 60s and retry (up to 5 times)

---

## [1.3.0] - 2026-05-09

### Added
- Auto-update Railway env vars (ACCESS_TOKEN, REFRESH_TOKEN, EXPIRES_AT) via Railway GraphQL API after every token refresh, so tokens survive container restarts without needing a Volume

---

## [1.2.0] - 2026-05-08

### Added
- Persist tokens to /tmp/tokens.json after refresh; load on startup to avoid unnecessary re-auth
- `threading.Lock` for thread-safe token access

### Fixed
- Read EXPIRES_AT from env var on startup instead of hardcoding 0, preventing unnecessary token rotation on every container restart

---

## [1.1.0] - 2026-05-07

### Fixed
- Activities under 1 km no longer get zeroed out (n=0 guard)
- Check HTTP status on GET before processing activity data
- Add retry loop (5 attempts, 30s interval) for activities that haven't loaded distance yet
- Add timeout=10 to all requests to prevent hanging threads

---

## [1.0.0] - 2026-05-06

### Added
- Initial Flask webhook server
- Strava OAuth2 token refresh
- Distance formula: n = int(km), new = n.nn (e.g. 13.50 → 13.13)
- Background thread per activity to avoid blocking webhook response
