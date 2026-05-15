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

## Lessons

- **Don't try to modify data Strava already owns.** Strava treats every GPS activity's distance as a derived value. There is no API and no UI surface to override it, and the company appears to be removing the few that existed.
- **Intercept before the source-of-truth.** Modifying TCX/FIT before upload is the only stable pattern.
- **Webhook architectures aren't worth building for personal use** when the underlying API is closed (Garmin) or hostile to your goal (Strava). A 30-second CLI you run yourself is more reliable and cheaper than a 24/7 service that fails silently.
- **Test with the actual page, not just the API.** We spent v1.5 building a web form scraper assuming the distance field still existed in the HTML. It didn't. One DevTools peek would have saved that work.

## Archive

The v1 webhook implementation's README is preserved at [README-v1-archive.md](README-v1-archive.md) for reference. All of the v1 code is in the git history (look for tags `1.0.0` through `1.4.0` and the merge commit `fef1064`).
