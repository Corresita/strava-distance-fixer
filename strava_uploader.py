"""Strava upload helper: OAuth refresh + multipart upload + status poll.

Reuses Strava OAuth token state from .env. The access_token is refreshed
automatically when expired and the new values are written back to .env so
subsequent runs pick them up.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()


@dataclass
class UploadResult:
    upload_id: int
    activity_id: int | None
    status: str
    error: str | None


def _env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"{key} not set in .env")
    return val


def _persist_env(updates: dict[str, str]) -> None:
    """Rewrite .env in place if it exists, and always patch os.environ so the
    running process sees the new values immediately. On Railway there is no
    .env file (env vars come from the platform), so the file write is a no-op
    but os.environ still gets updated to avoid refreshing on every call."""
    # Always patch os.environ first — works for both local .env and Railway env vars.
    for k, v in updates.items():
        os.environ[k] = v

    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    seen = set()
    for i, line in enumerate(lines):
        for k, v in updates.items():
            if line.startswith(f"{k}="):
                lines[i] = f"{k}={v}"
                seen.add(k)
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    env_path.write_text("\n".join(lines) + "\n")


def get_access_token() -> str:
    expires_at = int(os.environ.get("EXPIRES_AT", "0") or 0)
    if time.time() < expires_at - 60:
        return _env("ACCESS_TOKEN")

    print("[strava] refreshing access token...")
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": _env("CLIENT_ID"),
            "client_secret": _env("CLIENT_SECRET"),
            "refresh_token": _env("REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Token refresh failed: {data}")
    _persist_env(
        {
            "ACCESS_TOKEN": data["access_token"],
            "REFRESH_TOKEN": data["refresh_token"],
            "EXPIRES_AT": str(data["expires_at"]),
        }
    )
    print(f"[strava] new token expires at {data['expires_at']}")
    return data["access_token"]


def upload_tcx(
    tcx_path: Path,
    name: str | None = None,
    description: str | None = None,
    external_id: str | None = None,
    poll_timeout_s: int = 120,
) -> UploadResult:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    files = {"file": (tcx_path.name, tcx_path.read_bytes(), "application/xml")}
    data = {"data_type": "tcx"}
    if name:
        data["name"] = name
    if description:
        data["description"] = description
    if external_id:
        data["external_id"] = external_id

    print(f"[strava] POST /uploads ({tcx_path.name}, {tcx_path.stat().st_size} bytes)...")
    resp = requests.post(
        "https://www.strava.com/api/v3/uploads",
        headers=headers,
        files=files,
        data=data,
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload POST failed: {resp.status_code} {resp.text}")
    payload = resp.json()
    upload_id = payload["id"]
    print(f"[strava] upload_id={upload_id}, status={payload.get('status')!r}")

    # Poll until ready / errored / duplicate detected
    deadline = time.time() + poll_timeout_s
    while time.time() < deadline:
        time.sleep(3)
        r = requests.get(
            f"https://www.strava.com/api/v3/uploads/{upload_id}",
            headers=headers,
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[strava] poll GET non-200: {r.status_code} {r.text}")
            continue
        p = r.json()
        status = p.get("status", "")
        activity_id = p.get("activity_id")
        error = p.get("error")
        print(f"[strava]   poll: status={status!r}, activity_id={activity_id}, error={error!r}")
        # Strava status strings: "Your activity is ready.", "There was an error...",
        # "deleted", "duplicate of activity ..."
        if activity_id or error or "duplicate" in (error or "").lower() or status.startswith("Your activity is ready"):
            return UploadResult(
                upload_id=upload_id,
                activity_id=activity_id,
                status=status,
                error=error,
            )
    raise TimeoutError(f"Upload poll timed out after {poll_timeout_s}s")


def find_activity_near(
    start_iso: str,
    expected_distance_m: float,
    window_minutes: int = 15,
) -> dict | None:
    """Find a Strava activity within ±window_minutes of start_iso whose distance
    is within 10% of expected_distance_m. Used to locate the Garmin auto-synced
    copy of a just-completed run so we can replace it."""
    from datetime import datetime, timedelta, timezone

    token = get_access_token()
    # Garmin gives ISO with a Z; Strava expects unix seconds for before/after.
    dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    after = int((dt - timedelta(minutes=window_minutes)).timestamp())
    before = int((dt + timedelta(minutes=window_minutes)).timestamp())

    print(f"[strava] searching activities: after={after}, before={before}, "
          f"expected={expected_distance_m:.0f}m")
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"after": after, "before": before, "per_page": 30},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"[strava] activity search failed: {resp.status_code} {resp.text}")
        return None

    candidates = resp.json()
    print(f"[strava]   found {len(candidates)} activities in window")
    best = None
    best_diff = None
    for a in candidates:
        d = a.get("distance", 0) or 0
        if expected_distance_m == 0:
            return a  # no constraint
        diff = abs(d - expected_distance_m) / expected_distance_m
        print(f"[strava]     candidate id={a.get('id')} dist={d:.0f}m diff={diff:.4f}")
        if diff <= 0.10 and (best_diff is None or diff < best_diff):
            best = a
            best_diff = diff
    if best:
        print(f"[strava]   picked best match: id={best['id']}")
    else:
        print(f"[strava]   no match within 10% distance tolerance")
    return best


def delete_activity(activity_id: int) -> bool:
    token = get_access_token()
    resp = requests.delete(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code in (200, 204):
        print(f"[strava] deleted activity {activity_id}")
        return True
    print(f"[strava] delete {activity_id} failed: {resp.status_code} {resp.text}")
    return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python strava_uploader.py <path.tcx> [name]", file=sys.stderr)
        sys.exit(1)
    p = Path(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else None
    r = upload_tcx(p, name=name)
    print(f"\nFinal: activity_id={r.activity_id}, status={r.status!r}, error={r.error!r}")
