# Strava Distance Fixer

A small command-line tool that pulls your latest Garmin run, rewrites its distance to a `N.NN km` repeating-digit form (e.g. `19.19 km`, `12.12 km`), and uploads the modified copy to Strava. Your Garmin Connect data is untouched.

## Why this exists

Strava silently re-derives distance from the GPS stream for any activity that has GPS data. The official API's `UpdatableActivity` model doesn't include a `distance` field, and the web edit form removed it sometime around 2024. There is no way to change the distance of an existing GPS activity on Strava.

The workaround: modify the GPS track itself before Strava sees it. Pull the activity from Garmin, scale every GPS point uniformly toward the start position so the total path length equals the target, then upload the modified copy as a new activity. Strava treats it as a fresh upload, recomputes distance from the (scaled) stream, and the number persists.

## How it works

```
Garmin watch ──▶ Garmin Connect ──▶ Strava (auto-sync, original distance)
                       │                       ▲
                       │                       │ (5) upload scaled TCX
                       ▼                       │
                  [you run sync.py] ───────────┘
                       │                       ▲
                       ├─ (1) download TCX     │
                       ├─ (2) scale GPS path   │
                       ├─ (3) find Strava ─────┘
                       │      auto-synced copy
                       └─ (4) delete it
```

The script keeps Garmin Connect's direct sync to Strava on, lets the original arrive there, then replaces it. Steps:

1. Pull the TCX from Garmin (cached OAuth, no MFA after the first time)
2. Scale the GPS path uniformly so the total path length equals the `N.NN` km target
3. Search Strava for the auto-synced copy of this same run (matched by start time ±15 min and distance ±10 %)
4. Delete the auto-synced copy
5. Upload the scaled TCX as a fresh activity
6. If step 5 fails for any reason, fall back to uploading the **un-scaled original** TCX so the activity still lands on Strava — you just don't get the `N.NN` cosmetics

Why steps 3–4 are needed: Strava deduplicates uploads by start time + GPS overlap, so without deleting the Garmin auto-synced copy first, our scaled upload is rejected as a duplicate.

What gets scaled in the TCX:
- `<LatitudeDegrees>` / `<LongitudeDegrees>` on every trackpoint (anchored to the first point so the route stays in the right place)
- `<DistanceMeters>` on every trackpoint and every lap
- `<Speed>` in the TPX extension (so pace stays consistent with the new distance)

What stays untouched:
- Timestamps — total duration is unchanged
- Heart rate, cadence, power — measured values, scaling them would be data fabrication
- Altitude — geometrically tied to position; for the ~0.05 % shrink we typically apply, the slope difference is invisible

For a 19.200 km activity the target is 19.19 km, a 0.05 % shrink. The map track is ~10 m shorter than the real route — invisible to the eye.

## Requirements

- Garmin account with the direct Garmin → Strava sync **enabled** (Garmin Connect → Settings → Connected Apps → Strava). The script relies on the auto-synced copy as a fallback target.
- Strava API app credentials (https://www.strava.com/settings/api) with `activity:write` scope.
- Python 3.12+, dependencies in `requirements.txt`.

## Setup

```bash
# Install
conda activate strava-fixer    # or your venv
pip install -r requirements.txt

# Credentials
cp .env.example .env
# Fill in GARMIN_EMAIL / GARMIN_PASSWORD and Strava CLIENT_ID / CLIENT_SECRET

# Authorize Strava (opens browser, captures the code on localhost:8765)
python reauth_strava.py
```

The Strava app's "Authorization Callback Domain" in https://www.strava.com/settings/api must be `localhost` for `reauth_strava.py` to receive the OAuth callback.

## Usage

```bash
python sync.py                  # process latest Garmin running activity
python sync.py 22883472799      # process a specific Garmin activity ID
python sync.py --force          # re-process even if history.json has it
python sync.py --no-delete      # skip the "find + delete Strava copy" step
                                # (use this if Garmin → Strava auto-sync is off)
```

End-to-end takes about 30 seconds:

```
$ python sync.py
2026-05-14 22:01:03  INFO  Logging into Garmin...
2026-05-14 22:01:04  INFO  Looking up latest running activity...
2026-05-14 22:01:05  INFO  Activity 22883472799: 'Trail Run'  19.1996 km
2026-05-14 22:01:05  INFO  Target: 19.19 km
2026-05-14 22:01:05  INFO  Downloading TCX from Garmin...
2026-05-14 22:01:07  INFO    got 11034421 bytes
2026-05-14 22:01:08  INFO    scaled: 19.1996 km -> 19.1900 km  (k=0.999502)
2026-05-14 22:01:08  INFO  Uploading to Strava...
2026-05-14 22:01:25  INFO  ✓ DONE  https://www.strava.com/activities/18512945963  (19.19 km)
```

The first Garmin login is interactive: a 6-digit MFA code arrives by email, you paste it in. Subsequent runs reuse the cached OAuth token in `garmin_tokens/` (good for ~1 year) and skip the MFA dance entirely.

## How we got here

The first three versions of this project tried to modify activities **after** they reached Strava — via the Strava API, then via web-form simulation. Both hit walls. See [docs/journey.md](docs/journey.md) for the full story of what we tried (Strava API → web form → Garmin Developer API → garth) and why each one didn't survive contact with reality. The v1 webhook README is archived at [docs/README-v1-archive.md](docs/README-v1-archive.md).

## Files

```
sync.py              main entry point
garmin_client.py     Garmin Connect login + activity fetch + TCX download
tcx_scaler.py        GPS-path scaling logic
strava_uploader.py   Strava OAuth refresh + multipart upload + status poll
reauth_strava.py     one-shot Strava re-authorization helper
history.json         per-run record (auto-created)
sync.log             append-only log (auto-created)
garmin_tokens/       cached Garmin OAuth1 token (auto-created)
docs/                Strava API reference notes
```

## Failure modes

- **TCX scaling fails** — the script falls back to uploading the un-scaled original TCX. The activity still lands on Strava, just at its real distance. `history.json` records `"pipeline_path": "fallback_original"` and the reason.
- **Scaled upload fails** — same fallback: upload the original. `history.json` records both failure reasons.
- **Both uploads fail** — `history.json` records `"pipeline_path": "failed"`. The original activity from Garmin's auto-sync is already gone (we deleted it earlier in the pipeline), so manually export GPX/TCX from Garmin Connect and drag it into Strava's upload page.
- **Strava auto-synced copy not found within 3 min** — script proceeds anyway and uploads the scaled TCX clean. If a duplicate ever shows up later, just delete one of the two manually.
- **Garmin token cache expired (~1 year)** — first run after that needs a fresh interactive login with MFA. Re-running with the live email open is enough.

## history.json

Each `sync.py` run appends one record. Useful fields:

| Field | Meaning |
| --- | --- |
| `pipeline_path` | `scaled_uploaded` (success) / `fallback_original` (uploaded un-scaled) / `skipped_existing` / `failed` |
| `original_km` / `target_km` | Original distance and the `N.NN` target |
| `scale_factor` | The multiplier applied (≈ 0.99 to 1.01 for typical runs) |
| `strava_deleted_id` | The Garmin-auto-synced Strava activity we replaced, or `null` |
| `strava_new_id` | The Strava activity we created, or `null` |
| `fallback_reason` / `error` | Diagnostic strings when something went wrong |

## Security notes

`.env` is gitignored. Never commit it. The Garmin token cache in `garmin_tokens/` is also gitignored — it's an OAuth1 token, not your password, but treat it like a credential.

The Strava access token refreshes itself when expired; refreshed tokens are written back to `.env`.
