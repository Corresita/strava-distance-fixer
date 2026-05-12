# Changelog

## [Unreleased]

### Changed
- Increase initial wait from 30s to 120s before first distance fix attempt, to allow Strava to finish processing GPS data from Garmin sync before PUT (prevents Strava from reverting the distance)

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
