# Project journey

A record of the four approaches we tried before landing on what works. Kept so the next person (or future-me) doesn't have to repeat the same dead ends.

## The goal

Rewrite each new running activity's distance on Strava to a repeating-digit form `N.NN km` (e.g. 19.200 km → 19.19 km, 12.4 km → 12.12 km). All other data — pace, HR, route, elevation — should stay intact.

## Approach 1: Strava API webhook + `PUT /activities` (v1.0 – v1.4)

**Idea.** Subscribe to Strava activity-create webhooks. When a new activity arrives, call `PUT /api/v3/activities/{id}` with the rewritten `distance`.

**What we built.** A Flask service on Railway, OAuth token refresh, retry loop, persistent token cache.

**Why it failed.** Strava's public `UpdatableActivity` model has no `distance` field. The PUT request returns 200 OK and the response body shows the new value, but a few seconds later Strava re-runs distance computation from the GPS stream and reverts. The behavior is **silent** — no error, no warning, the field just resets.

For manually entered activities (no GPS stream) the PUT *did* persist, because there's no stream to recompute from. So this approach is only useful for manual activities, not real runs.

## Approach 2: Web form simulation via session cookie (v1.5 attempt)

**Idea.** The Strava website's edit page used to have a "Distance" input. Logging in with a session cookie, scraping the edit form, posting modified values would bypass the API restriction.

**What we built.** `requests` + BeautifulSoup form scraper, CSRF token capture, unit-aware (km vs miles) submission, post-submit verification via API.

**Why it failed.** Sometime around 2024 Strava removed the distance input from the GPS-activity edit form entirely. The form posts we sent never contained a distance field, so the edit had no effect. Confirmed empirically (Railway log shows `Fields: ['utf8', '_method', 'authenticity_token', 'activity[name]', ...]` — no `activity[distance]`) and by visiting the edit page in a browser. Strava community forum acknowledges this as a "known issue" with no ETA on a fix.

This killed every "modify after upload to Strava" approach. The only path left was to modify data *before* it reaches Strava.

## Approach 3: Garmin Connect Developer API (research, never built)

**Idea.** Subscribe to Garmin Connect webhooks. When a new Garmin activity is detected, download the FIT/TCX, scale the GPS path, upload to Strava as a fresh activity.

**Why it didn't work out.** Two blockers:

- The Garmin Connect Developer Program is described as "for enterprise use only" — the developer forum confirms only large partners (companies, fitness platforms) get approved. Individual / hobby use cases get denied.
- The Access Request Form at `garmin.com/en-US/forms/GarminConnectDeveloperAccess/` has been broken for at least a month (developers in the Garmin forum reporting "under construction" status). Emails to `connectdevservices@garmin.com` go unanswered.

So even if it would have been the cleanest path, the door is closed for our use case.

## Approach 4: Unofficial Garmin client (`garth`) (rejected)

**Idea.** Use the [garth](https://github.com/matin/garth) library, which logs into Garmin Connect using internal SSO endpoints (the same ones the mobile app uses), and downloads activities without going through the official developer program.

**Why we rejected it.**

- garth's README literally says `[DEPRECATED]` — Garmin changed their auth flow and broke garth's mobile login. Existing sessions still work but new logins do not.
- Sibling library `python-garminconnect` is still active, but issues like [#213](https://github.com/cyberjunky/python-garminconnect/issues/213) report 48+ hour account-level rate-limiting after a small number of failed login attempts. We hit the same 429s on our first three login attempts during this session — a sign Garmin is actively tightening this surface.

We almost went with python-garminconnect anyway, but the rate-limit risk for a polling architecture was too high.

## Approach 5: python-garminconnect + cached token + on-demand sync (v2.0, current)

**Idea.** Same as approach 4, but solve the rate-limit problem two ways: (a) cache the OAuth1 token after first login so subsequent runs skip the SSO entirely, and (b) trigger the sync **on demand** (you run a command after each run) rather than polling, so the login rate is effectively once per year.

**Why it works.**

- A single interactive login (with MFA) populates `garmin_tokens/`. The OAuth1 token there is good for ~1 year per garth/garminconnect docs.
- Every subsequent `python sync.py` reuses that token. Zero login traffic. Zero rate-limit risk.
- Once we have the TCX in memory, the modification is a straight XML transform: scale every trackpoint's lat/lng toward the start position by the same factor, scale per-trackpoint distance and speed by the same factor, scale per-lap distance by the same factor. HR / cadence / power / altitude / time stay untouched.
- Upload to Strava as a new activity. Because the GPS stream itself is now consistent with the target distance, Strava's own distance recomputation produces the target value. No revert.

End-to-end takes ~30 seconds per run. The entire 19.200 km → 19.19 km change is a 0.05 % geometric shrink — invisible on the map, exact on the dashboard.

**Tested end-to-end on 2026-05-14**: 19.200 km Garmin activity → scaled TCX uploaded with shifted timestamps (to bypass Strava's dedup against the auto-synced original) → resulting Strava activity 18512945963 reported `distance: 19190.00 m` and preserved all 14,522 HR samples, cadence, power, altitude. The scaling math works perfectly. The pipeline works perfectly on a "clean" upload (no existing Strava copy).

## Approach 6: Remote trigger via iOS Shortcut → Railway → sync.py (v2.1, attempted 2026-05-16)

**Goal.** Make Approach 5 phone-triggered instead of laptop-required. Wrap `sync.run()` in a Flask endpoint, deploy on Railway, call it from an iOS Shortcut. Same pipeline, but reachable from anywhere.

**What we built.** `sync_server.py` with `POST /sync` gated by an `X-Sync-Secret` header. Garmin OAuth1 token loaded from a `GARMIN_TOKEN_B64` env var on startup so containers never SSO. iOS Shortcut sends `POST` with the secret, reads `strava_url` from the JSON response, shows it in a notification.

**Where it broke.** The pipeline depends on a "find Strava's auto-synced copy of the Garmin activity, DELETE it, upload our scaled version" step. The DELETE call returns:

```
401 {"message":"Authorization Error",
     "errors":[{"resource":"Application","field":"internal","code":"invalid"}]}
```

**Diagnostic trail (all with the same token, same scopes — `read activity:read_all activity:write`):**

| Test                                                | Result                |
| --------------------------------------------------- | --------------------- |
| `GET /api/v3/athlete`                               | 200 ✓                 |
| `GET /api/v3/activities/{id}`                       | 200 ✓                 |
| `PUT /api/v3/activities/{id}` (changing description)| 200 ✓                 |
| `POST /api/v3/uploads`                              | 200, then dedup error |
| `DELETE /api/v3/activities/99999999999` (fake id)   | **404 "Record Not Found"** — proves DELETE permission exists at app level |
| `DELETE /api/v3/activities/{real id}`               | **401 "Application internal invalid"** |

The 401 is **not** about IP origin (same response from a laptop in Vancouver as from a Railway container), **not** about User-Agent (browser-spoofed UA returns the same 401), **not** about token scope (PUT with the same token works fine), and **not** about credential rotation (token was just refreshed; reauth was clean).

The likely root cause: Strava's anti-abuse / app-tiering system. Apps in the default "Limited Access" tier appear to be allowed full READ + write (`POST /uploads`, `PUT /activities`) but blocked from `DELETE /activities/{id}` on real activities. The "Application internal invalid" error is Strava's way of saying "your app isn't in a tier that's permitted to do this." This is undocumented and only visible by hitting the endpoint.

**Side improvements that did land in 2.1:**

- `_persist_env` bug: when `.env` doesn't exist (Railway), the function early-returned and `os.environ` never got the refreshed token, so every API call triggered a redundant token refresh. Now it always patches `os.environ`.
- `find_activity_near` got verbose logging (search window, candidates, distance diffs) — invaluable for diagnosing this.
- `delete_activity` now logs token prefix + uses `flush=True` so the failure line isn't lost to gunicorn stdout buffering (which is what happened in early diagnostic runs).

**Net state.** v2.1 deploys cleanly and survives the iOS Shortcut roundtrip, but the underlying delete-and-reupload pipeline cannot complete its goal on a real run. The pipeline only fully works in artificial tests where there's no Garmin auto-synced Strava copy to fight against.

## Full attack-surface map

After Approach 6 hit the DELETE wall, we mapped out every conceivable place in the chain where distance could be modified. Most of these were already covered, ruled out, or are open questions. Keeping the map here so future-us doesn't have to redo it.

```
┌────────────────────────────────────────────────────────────────────────┐
│ Step 1. Garmin watch — records run, computes distance                  │
├────────────────────────────────────────────────────────────────────────┤
│ A1. Connect IQ data field — can read sensors, cannot overwrite GPS-    │
│     derived distance                                                    │
│ A2. Footpod stride / wheel calibration — indoor only, not useful for   │
│     GPS runs                                                            │
│ A3. Manual pause/resume — requires user action every run, not auto     │
│ A4. Third-party recording app, watch as sensor only — complex, bad UX  │
└────────────────────────────────────────────────────────────────────────┘
                          ↓ BLE
┌────────────────────────────────────────────────────────────────────────┐
│ Step 2. Phone Garmin Connect app — receives BLE stream                 │
├────────────────────────────────────────────────────────────────────────┤
│ B1. App edit screen — GPS activity distance not editable               │
│ B2. Intercept BLE — needs root + offline-protocol RE                   │
└────────────────────────────────────────────────────────────────────────┘
                          ↓
┌────────────────────────────────────────────────────────────────────────┐
│ Step 3. Garmin Connect cloud — stores activity                         │
├────────────────────────────────────────────────────────────────────────┤
│ C1. Web UI distance edit — locked for GPS activities                   │
│ C2. python-garminconnect API — no set_distance endpoint                │
│ C3. Delete + re-upload TCX — loses Garmin's private analytics fields   │
│     (Body Battery, Hill Score, Training Effect, Running Dynamics)      │
│ C4. Delete + re-upload FIT (preserving unknown_X messages byte-for-    │
│     byte) — preserves analytics if a Python FIT writer can round-trip  │
│     unknown messages losslessly. 3-5 days work, unverified.            │
│ C5. Modify only record.distance and session.total_distance in FIT,     │
│     leaving position_lat/long untouched — preserves the map but        │
│     creates internal inconsistency, unclear if Strava/Garmin use the   │
│     declared distance vs re-derive from positions                      │
└────────────────────────────────────────────────────────────────────────┘
                          ↓ Garmin → Strava auto-sync push
┌────────────────────────────────────────────────────────────────────────┐
│ Step 4. Strava receives the push                                       │
├────────────────────────────────────────────────────────────────────────┤
│ D1. Intercept the Garmin → Strava push — impossible, server-to-server  │
│ D2. Beat Garmin to it (upload modified copy first, let Garmin's copy   │
│     get dedup-rejected) — timing window is 1-5 min, unreliable         │
└────────────────────────────────────────────────────────────────────────┘
                          ↓
┌────────────────────────────────────────────────────────────────────────┐
│ Step 5. Activity now lives on Strava                                   │
├────────────────────────────────────────────────────────────────────────┤
│ E1. PUT /activities/{id} with distance field — field not in            │
│     UpdatableActivity schema, silently ignored                         │
│ E2. POST /uploads with modified file — dedup rejects                   │
│ E3. DELETE /activities/{id} — 401 Application internal invalid for     │
│     Limited-tier apps (Approach 6)                                     │
│ E4. Strava web Crop feature — UI still exists, trims start/end of GPS  │
│     track to reduce distance. Web-only, no public API. Reachable via   │
│     session cookie? Unverified — next thing to investigate.            │
│ E5. Strava web distance edit — removed around 2024 (Approach 2)        │
│ E6. Request elevated app tier from Strava — 2-4 week wait, ~30%        │
│     approval odds based on community reports                           │
│ E7. Time-shifted upload (Plan B) — creates two activities for one      │
│     run, visible to friends. Functionally works but UX rejected.       │
└────────────────────────────────────────────────────────────────────────┘
                          ↓
                  Strava displays distance
```

## Approach 7: Strava web Crop / truncate via session cookie (v2.2, works ✓)

After Approach 6 hit the DELETE wall, mapping the attack surface (above) made E4 the obvious next thing to try: Strava's website still has a "Crop Activity" feature that trims the start/end of the GPS stream and recomputes distance. The OAuth API doesn't expose it, but the web form is reachable with a session cookie.

**What the form looks like.** Viewing source of `/activities/{id}/truncate`:

```html
<form action='/activities/{id}/truncate' method='post'>
  <input name='authenticity_token' type='hidden' value='…'>
  <input name='start_index' type='hidden' value='0'>
  <input name='end_index' type='hidden' value='2675'>
</form>
```

Three fields: a Rails CSRF token and two integer indices into the activity's GPS-point array. Cropping is a slider in the UI; the slider sets these two indices; submit POSTs them back. Strava re-derives distance from the trimmed point range.

**Implementation (`strava_cropper.py`).**

1. `GET /api/v3/activities/{id}/streams?keys=distance` (OAuth) → array of cumulative meters per GPS point.
2. Binary-search for the largest index where `distance[i] ≤ target_m`. That's the `end_index` that gets us to N.NN km without overshooting.
3. `GET /activities/{id}/truncate` (session cookie) → scrape the `authenticity_token` from the HTML.
4. `POST /activities/{id}/truncate` (session cookie + CSRF) with `start_index=0`, `end_index=<computed>`.

Strava processes synchronously and the activity is updated in place — same ID, all kudos and comments retained.

**End-to-end test result, 2026-05-16:** activity 18532273416 went from 8086.30 m → 8079.80 m (= 8.08 km) in under 5 seconds. 18 existing kudos preserved. HR / cadence / pace unchanged. Two GPS points (≈6 m) trimmed off the end.

**Why this beats every previous approach:**

| Property | API PUT (v1) | Web edit form (v1.5) | Delete + reupload (v2.1) | Crop (v2.2) |
| --- | --- | --- | --- | --- |
| Persists for GPS activities | ✗ (silently reverted) | ✗ (field removed) | ✗ (DELETE 401) | **✓** |
| Preserves kudos / comments | n/a | n/a | ✗ (delete loses them) | **✓** |
| Single Strava activity | ✓ | ✓ | needs `delete` | **✓** |
| Garmin data untouched | ✓ | ✓ | ✓ | **✓** |
| Works on Railway | ✓ | n/a | ✗ (DELETE 401) | **✓** |

**The costs we accept.**

- **Session cookie maintenance.** `_strava4_session` lives weeks-to-months. When it expires, the script logs an auth failure at the crop step and the user has to recapture from a browser. Same operational burden v1.5 had — and exactly the kind of fragility that killed v1.5 (Strava removed the distance field). The risk is Strava someday removing or restricting the crop form too.
- **GPS data is irreversibly trimmed.** Strava's own warning: "This action cannot be undone." We chop a few meters off the end of every run. Visible if you look hard at the map; invisible otherwise.
- **Crop isn't geometric scaling.** v2.0 scaled every GPS point uniformly toward the start — the route shape was preserved, just shrunk by 0.05 %. Crop just lops off the end. For a typical 19 km run trimmed to 19.19 km we lose ~10 m of route ending; for 8.32 km trimmed to 8.08 km we lose ~240 m. Bigger reductions = more visible map clip.

In exchange we get a feature that *actually works in production* on Strava's current platform.

## Lessons

- **Don't try to modify data Strava already owns.** Strava treats every GPS activity's distance as a derived value. There is no API and no UI surface to override it, and the company appears to be removing the few that existed.
- **Intercept before the source-of-truth.** Modifying TCX/FIT before upload is the only stable pattern.
- **Webhook architectures aren't worth building for personal use** when the underlying API is closed (Garmin) or hostile to your goal (Strava). A 30-second CLI you run yourself is more reliable and cheaper than a 24/7 service that fails silently.
- **Test with the actual page, not just the API.** We spent v1.5 building a web form scraper assuming the distance field still existed in the HTML. It didn't. One DevTools peek would have saved that work.
- **A working pipeline isn't a finished feature.** v2.1 deploys, the iOS Shortcut fires, the server responds — looks complete. But the Strava `DELETE` step it depends on silently 401s in production. Always probe each step's actual *outcome* on the real target, not just whether the code ran.
- **Token rotation doesn't mean token replacement.** After regenerating Strava's Client Secret and re-authorizing, the old refresh token was still active (Strava reuses refresh tokens across re-auths within the same grant context). We had to explicitly *revoke* the app in Strava settings before reauth would actually mint a new refresh token.

## Archive

The v1 webhook implementation's README is preserved at [README-v1-archive.md](README-v1-archive.md) for reference. All of the v1 code is in the git history (look for tags `1.0.0` through `1.4.0` and the merge commit `fef1064`).
