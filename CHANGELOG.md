# Changelog

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
