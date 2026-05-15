# Changelog

## [Unreleased]

### Added
- `/fix/<activity_id>` endpoint for manually triggering distance fix on a specific activity (skips initial wait, useful for debugging and fixing past activities)
- Web form fallback: if Strava API reverts the distance 2+ times (GPS activity protection), automatically switch to simulating Strava web edit form via `requests` + BeautifulSoup, which bypasses the GPS protection
- `STRAVA_EMAIL` and `STRAVA_PASSWORD` env vars required for web form fallback
- GPS activity early-routing: after the initial GET, check `manual` and `start_latlng` — if it's a GPS activity, skip the API PUT loop entirely and go straight to the web form. Saves ~150s and 4 wasted requests per GPS activity. Manual activities (no GPS source) still use API since it persists for them.
- Web form fallback hardening (three fixes that prevent silent failures):
  - Unit conversion: read `measurement_preference` via `GET /api/v3/athlete`; if user is on imperial, convert km → miles before submitting (previously submitted km value into miles-labeled field, writing 1.609× the intended distance)
  - CSRF token in `X-CSRF-Token` header (pulled fresh from edit page `<meta>` tag), not just in form body — required by Rails/Turbo
  - Post-submit verification: sleep 5s, re-GET via API, compare distance with 2 m tolerance; raise if not persisted (previously a 200/302 response was treated as success even if Strava silently dropped the change)

### Changed
- Wait for GPS distance to stabilize before PUT: if distance changed since last attempt, wait another 60s before trying
- Verify wait reduced back to 30s (60s was unnecessary)
- Max retries reduced back to 5 (web form fallback handles persistent reverts)

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
