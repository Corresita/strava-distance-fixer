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

# Some Strava endpoints (notably DELETE) appear to silently 401 requests with
# the default `python-requests/X.Y` User-Agent when issued from cloud IPs.
# Pretending to be a real desktop Chrome works around it. Cosmetic.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "User-Agent": _UA}


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


def _railway_upsert_vars(updates: dict[str, str]) -> None:
    """If running on Railway and a service account token is configured, push
    the env var updates back to Railway so they survive container restarts.
    Silently noops without the four RAILWAY_* env vars."""
    api_token = os.environ.get("RAILWAY_API_TOKEN", "")
    project_id = os.environ.get("RAILWAY_PROJECT_ID", "")
    service_id = os.environ.get("RAILWAY_SERVICE_ID", "")
    environment_id = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
    if not all((api_token, project_id, service_id, environment_id)):
        return
    try:
        r = requests.post(
            "https://backboard.railway.app/graphql/v2",
            headers={"Authorization": f"Bearer {api_token}", "User-Agent": _UA},
            json={
                "query": "mutation V($i: VariableCollectionUpsertInput!) { variableCollectionUpsert(input: $i) }",
                "variables": {
                    "i": {
                        "projectId": project_id,
                        "serviceId": service_id,
                        "environmentId": environment_id,
                        "variables": updates,
                    }
                },
            },
            timeout=10,
        )
        body = r.json()
        if "errors" in body:
            print(f"[railway] var upsert failed: {body['errors']}", flush=True)
        else:
            print(f"[railway] updated {list(updates.keys())}", flush=True)
    except Exception as e:
        print(f"[railway] var upsert error: {e}", flush=True)


def _persist_env(updates: dict[str, str]) -> None:
    """Rewrite .env in place if it exists, always patch os.environ so the
    running process sees the new values immediately, and push the values back
    to Railway via its GraphQL API if RAILWAY_* env vars are configured.

    On Railway containers there's no .env file (env vars come from the platform),
    so the file write is a no-op but the Railway-side upsert keeps the values
    alive across restarts."""
    # 1. Patch in-process os.environ immediately.
    for k, v in updates.items():
        os.environ[k] = v

    # 2. Persist to local .env if present.
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
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

    # 3. Push to Railway env vars so they survive container restarts.
    _railway_upsert_vars(updates)


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
        headers={"User-Agent": _UA},
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
    headers = _h(token)

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
        headers=_h(token),
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
    print(f"[strava] DELETE /activities/{activity_id} (token prefix: {token[:6]}...)", flush=True)
    resp = requests.delete(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers=_h(token),
        timeout=15,
    )
    if resp.status_code in (200, 204):
        print(f"[strava] deleted activity {activity_id}", flush=True)
        return True
    print(f"[strava] delete {activity_id} failed: {resp.status_code} {resp.text}", flush=True)
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
