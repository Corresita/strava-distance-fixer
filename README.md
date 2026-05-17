# Strava Distance Fixer

A small tool that rewrites each new running activity's distance on Strava to a repeating-digit form `N.NN km` (e.g. `19.200 km → 19.19 km`, `12.4 km → 12.12 km`). All other data — pace, HR, route, elevation, kudos, comments — stays intact. Garmin Connect's copy stays untouched.

## Why this exists

Strava re-derives distance from the GPS stream for any GPS activity. The API's `UpdatableActivity` schema has no `distance` field. The web edit form removed its distance input around 2024. Deleting a Garmin-synced activity via API returns 401 on default-tier apps. Every "change distance after upload" path is closed — except one: Strava's web **Crop** feature trims the GPS stream's start/end and recomputes distance, and it's reachable from outside the browser with a session cookie.

The whole `docs/journey.md` documents the four approaches that failed before this one worked.

## How it works

```
Garmin watch ──▶ Garmin Connect ──▶ Strava (auto-sync, original distance)
                                            │
                                            │ POST /activities/{id}/truncate
                                            ▼   (start_index=0, end_index=N)
                                    [you run sync.py]
                                            │
                                            ▼
                                    Strava activity now N.NN km
                                    (same activity, all kudos retained)
```

Steps inside `sync.py`:

1. Garmin: find latest running activity (cached OAuth token, no MFA after first run).
2. Compute the `N.NN` km target from the original distance.
3. Wait up to 3 minutes for Garmin Connect's auto-sync to push the activity to Strava (matched by start time ±15 min and distance ±10 %).
4. `GET /api/v3/activities/{id}/streams?keys=distance` → cumulative-meters array, one entry per GPS point.
5. Binary-search for the largest index whose cumulative distance is ≤ the target.
6. `GET /activities/{id}/truncate` (with session cookie) → scrape Rails CSRF token from the form.
7. `POST /activities/{id}/truncate` with `start_index=0, end_index=<computed>`. Strava re-derives distance from the trimmed range.

What changes:
- Activity's distance becomes ≤ target (typically within 2-3 m, since GPS points are ~3 m apart).
- A few GPS points at the end of the route are permanently dropped.

What stays untouched:
- Activity ID, kudos, comments, name, sport type — none of that is recreated.
- HR / cadence / power / altitude — Strava preserves these streams across crop.
- Garmin Connect — the script never writes to Garmin. The full original recording stays in Garmin Connect with all private analytics (Body Battery, Hill Score, Training Effect).

Strava's own UI warns "This action cannot be undone." Cropped GPS points really are gone. For a 19.200 km run trimmed to 19.19 km, that's ~10 m off the end of the route — invisible. For an 8.32 km run trimmed to 8.08 km, that's ~240 m — small but visible if you look at the map closely.

## Requirements

- Garmin Connect → Strava direct sync **enabled** (Garmin Connect → Settings → Connected Apps → Strava). The auto-synced activity is what we crop.
- Strava API app credentials (https://www.strava.com/settings/api) with `activity:read_all` scope (for the streams API). `activity:write` is not used by the crop path but is harmless to keep.
- A Strava web session cookie (`_strava4_session`) captured from a logged-in browser — see `.env.example`.
- Python 3.12+, dependencies in `requirements.txt`.

## Setup

```bash
# Install
conda activate strava-fixer    # or your venv
pip install -r requirements.txt

# Credentials
cp .env.example .env
# Fill in GARMIN_EMAIL / GARMIN_PASSWORD, Strava CLIENT_ID / CLIENT_SECRET,
# and STRAVA_SESSION_COOKIE (see .env.example for where to copy that from).

# Authorize Strava OAuth (opens browser, captures the code on localhost:8765)
python reauth_strava.py
```

The Strava app's "Authorization Callback Domain" in https://www.strava.com/settings/api must be `localhost` for `reauth_strava.py` to receive the OAuth callback.

## Usage

```bash
python sync.py                  # process latest Garmin running activity
python sync.py 22903731984      # process a specific Garmin activity ID
python sync.py --force          # re-process even if history.json has it
```

Wall-clock: a few seconds plus however long Garmin takes to push to Strava (typically ~1 minute after a run ends; the script polls for up to 3 minutes).

```
$ python sync.py
2026-05-16 19:00:00  INFO  Logging into Garmin...
2026-05-16 19:00:01  INFO  Looking up latest running activity...
2026-05-16 19:00:02  INFO  Activity 22903731984: 'Morning Run'  8.1240 km  start=2026-05-16T15:55:57Z
2026-05-16 19:00:02  INFO  Target: 8.08 km (8080 m)
2026-05-16 19:00:02  INFO    waiting up to 180s for Garmin → Strava auto-sync...
2026-05-16 19:00:03  INFO    found Strava activity 18532273416 (8.1240 km)
2026-05-16 19:00:03  INFO  Cropping Strava activity 18532273416 to 8.08 km...
[crop]   stream has 2672 points, original distance 8086.30m
[crop]   chosen end_index=2669 -> distance 8079.80m (dropping 2 points, 6.5m off the end)
[crop]   POST truncate -> 200
2026-05-16 19:00:05  INFO  ✓ DONE  cropped  https://www.strava.com/activities/18532273416  (8.1240 → 8.0798 km, dropped 2 points)
```

The first Garmin login is interactive: a 6-digit MFA code arrives by email, you paste it in. Subsequent runs reuse the cached OAuth token in `garmin_tokens/` (good for ~1 year) and skip the MFA dance entirely.

## How we got here

The first three versions of this project tried to modify activities **after** they reached Strava — via the Strava API, then via web-form simulation. Both hit walls. See [docs/journey.md](docs/journey.md) for the full story of what we tried (Strava API → web form → Garmin Developer API → garth) and why each one didn't survive contact with reality. The v1 webhook README is archived at [docs/README-v1-archive.md](docs/README-v1-archive.md).

## Files

```
sync.py              main entry point — finds latest Garmin run, waits for Strava sync, crops
strava_cropper.py    binary-search for end_index, POST truncate form via session cookie
garmin_client.py     Garmin Connect login + activity fetch (TCX download no longer used)
strava_uploader.py   Strava OAuth refresh + activity search helper
reauth_strava.py     one-shot Strava OAuth re-authorization helper
sync_server.py       optional Flask wrapper for iOS-Shortcut / Railway use
tcx_scaler.py        v2.0 GPS-path scaler — kept as reference, not wired up
history.json         per-run record (auto-created)
sync.log             append-only log (auto-created)
garmin_tokens/       cached Garmin OAuth1 token (auto-created)
docs/                journey + Strava API reference notes
```

## Failure modes

- **Strava auto-synced copy not found within 3 min** — the script logs failure with `pipeline_path=failed` and exits. Try again in a few minutes once Garmin Connect has pushed.
- **Crop returns HTTP 401 / login redirect** — your `_strava4_session` cookie expired. Recapture it from a browser (see `.env.example`) and update the env var.
- **Crop returns HTTP 5xx** — Strava-side issue, transient. `history.json` records the error. Re-run with `--force` later.
- **Garmin token cache expired (~1 year)** — first run after that needs a fresh interactive login with MFA. Re-run from a terminal with your email open, paste the code, done.
- **Activity is under 1 km** — skipped silently (N.NN with N=0 isn't a meaningful target).

In all failure modes, the Strava activity itself is left exactly as Garmin's auto-sync delivered it. Nothing is destroyed; the worst case is your distance doesn't get the cosmetic trim.

## history.json

Each `sync.py` run appends one record. Useful fields:

| Field | Meaning |
| --- | --- |
| `pipeline_path` | `cropped` (success) / `skipped_existing` / `skipped_short` / `no_activity` / `failed` |
| `original_km` / `target_km` | Original distance reported by Garmin and the `N.NN` target |
| `final_km` | Distance Strava ended up showing after the crop |
| `points_dropped` | How many GPS points we trimmed off the end |
| `strava_activity_id` | The Strava activity we modified (unchanged across runs) |
| `error` | Diagnostic string when something went wrong, else `null` |

## Security notes

`.env` is gitignored. Never commit it. The Garmin token cache in `garmin_tokens/` is also gitignored — it's an OAuth1 token, not your password, but treat it like a credential.

The Strava access token refreshes itself when expired; refreshed tokens are written back to `.env`.
