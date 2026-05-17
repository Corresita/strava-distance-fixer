"""Modify an existing Strava activity's distance by replaying its web 'truncate'
(crop) form. This is how Strava's website itself lets users chop the start/end
off an activity. We piggyback on it to trim just enough off the end to land on
a target N.NN km distance, without deleting the activity or losing kudos.

How it works:
  1. Fetch the activity's distance stream via OAuth API.
  2. Binary-search for the GPS-point index whose cumulative distance is closest
     to the target (without exceeding it).
  3. Fetch the /activities/{id}/truncate web page with a session cookie, parse
     the authenticity_token (Rails CSRF) out of the form.
  4. POST the same form with start_index=0 and end_index=<computed>.

The session cookie expires periodically (weeks to months) and has to be
re-captured from a logged-in browser. Strava can change this form at any time
and silently break us. See docs/journey.md "Approach 7" for context.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

import requests
from dotenv import load_dotenv

import strava_uploader

load_dotenv()

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")


@dataclass
class CropResult:
    activity_id: int
    original_distance_m: float
    target_distance_m: float
    chosen_end_index: int
    final_distance_m: float
    points_dropped: int
    success: bool
    error: Optional[str]


def _session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.cookies.set("_strava4_session", cookie, domain=".strava.com")
    s.headers.update({
        "User-Agent": _UA,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def _csrf_from_truncate_page(html: str) -> Optional[str]:
    # The form has: <input name='authenticity_token' type='hidden' value='...'>
    m = re.search(
        r"name=['\"]authenticity_token['\"]\s+type=['\"]hidden['\"]\s+value=['\"]([^'\"]+)['\"]",
        html,
    )
    if m:
        return m.group(1)
    # Some Strava pages render attrs in different order — fall back to a looser pattern
    m = re.search(r"authenticity_token[^>]*value=['\"]([^'\"]+)['\"]", html)
    return m.group(1) if m else None


def _distance_stream(activity_id: int, oauth_token: str) -> list[float]:
    r = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}/streams",
        params={"keys": "distance", "key_by_type": "true"},
        headers={"Authorization": f"Bearer {oauth_token}", "User-Agent": _UA},
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"streams GET failed: {r.status_code} {r.text[:200]}")
    body = r.json()
    # API can return either dict-of-streams (key_by_type) or list-of-streams
    if isinstance(body, dict) and "distance" in body:
        return body["distance"]["data"]
    if isinstance(body, list):
        for stream in body:
            if stream.get("type") == "distance":
                return stream["data"]
    raise RuntimeError(f"no distance stream in response: {body!r}")


def _pick_end_index(distances: list[float], target_m: float) -> int:
    """Largest index i such that distances[i] <= target_m. distances is
    monotonically non-decreasing (cumulative meters per GPS point)."""
    lo, hi = 0, len(distances) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if distances[mid] <= target_m:
            lo = mid
        else:
            hi = mid - 1
    return lo


def crop_to_distance(
    activity_id: int,
    target_m: float,
    session_cookie: Optional[str] = None,
    oauth_token: Optional[str] = None,
) -> CropResult:
    session_cookie = session_cookie or os.environ.get("STRAVA_SESSION_COOKIE", "")
    if not session_cookie:
        raise RuntimeError("STRAVA_SESSION_COOKIE not set")
    # Use strava_uploader's refresh-aware token getter so an expired token is
    # transparently rotated before we hit /streams.
    oauth_token = oauth_token or strava_uploader.get_access_token()

    print(f"[crop] activity {activity_id}, target {target_m:.2f}m")

    distances = _distance_stream(activity_id, oauth_token)
    original_m = distances[-1]
    print(f"[crop]   stream has {len(distances)} points, original distance {original_m:.2f}m")

    if target_m >= original_m:
        return CropResult(
            activity_id=activity_id,
            original_distance_m=original_m,
            target_distance_m=target_m,
            chosen_end_index=len(distances) - 1,
            final_distance_m=original_m,
            points_dropped=0,
            success=False,
            error=f"target {target_m} >= original {original_m}, nothing to crop",
        )

    end_idx = _pick_end_index(distances, target_m)
    chosen_m = distances[end_idx]
    print(f"[crop]   chosen end_index={end_idx} -> distance {chosen_m:.2f}m "
          f"(dropping {len(distances) - 1 - end_idx} points, "
          f"{original_m - chosen_m:.1f}m off the end)")

    s = _session(session_cookie)

    truncate_url = f"https://www.strava.com/activities/{activity_id}/truncate"
    page = s.get(truncate_url, timeout=15)
    if page.status_code != 200:
        return CropResult(
            activity_id=activity_id,
            original_distance_m=original_m,
            target_distance_m=target_m,
            chosen_end_index=end_idx,
            final_distance_m=chosen_m,
            points_dropped=0,
            success=False,
            error=f"GET truncate page: {page.status_code} (cookie expired?)",
        )

    csrf = _csrf_from_truncate_page(page.text)
    if not csrf:
        return CropResult(
            activity_id=activity_id,
            original_distance_m=original_m,
            target_distance_m=target_m,
            chosen_end_index=end_idx,
            final_distance_m=chosen_m,
            points_dropped=0,
            success=False,
            error="couldn't parse authenticity_token from truncate page",
        )
    print(f"[crop]   got CSRF token (len={len(csrf)})")

    # Strava's Rails forms accept the CSRF in either the body or the header.
    # Send both to be safe.
    resp = s.post(
        truncate_url,
        data={
            "authenticity_token": csrf,
            "start_index": "0",
            "end_index": str(end_idx),
        },
        headers={"X-CSRF-Token": csrf, "Referer": truncate_url},
        timeout=20,
        allow_redirects=False,
    )
    if resp.status_code not in (200, 302):
        return CropResult(
            activity_id=activity_id,
            original_distance_m=original_m,
            target_distance_m=target_m,
            chosen_end_index=end_idx,
            final_distance_m=chosen_m,
            points_dropped=0,
            success=False,
            error=f"POST truncate failed: {resp.status_code} {resp.text[:200]}",
        )
    print(f"[crop]   POST truncate -> {resp.status_code}")

    return CropResult(
        activity_id=activity_id,
        original_distance_m=original_m,
        target_distance_m=target_m,
        chosen_end_index=end_idx,
        final_distance_m=chosen_m,
        points_dropped=len(distances) - 1 - end_idx,
        success=True,
        error=None,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python strava_cropper.py <activity_id> <target_km>", file=sys.stderr)
        sys.exit(1)
    aid = int(sys.argv[1])
    target_km = float(sys.argv[2])
    r = crop_to_distance(aid, target_km * 1000)
    print()
    print(f"Result: {r}")
