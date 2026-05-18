"""Strava OAuth token refresh + activity search.

Refresh is invoked transparently by `get_access_token()` when expired. Refreshed
values (and any other env updates passed through `_persist_env`) are written to
both `.env` (local) and Railway's project variables (when the four `RAILWAY_*`
env vars are configured), so tokens survive container restarts.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# Spoofing a real Chrome UA avoids a 401 some Strava endpoints return for the
# default `python-requests/X.Y` UA on cloud IPs.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "User-Agent": _UA}


def _env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"{key} not set in .env")
    return val


def _railway_upsert_vars(updates: dict[str, str]) -> None:
    """Push env updates to Railway so they survive container restarts.
    Silently noops without the four RAILWAY_* env vars configured."""
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
                "variables": {"i": {
                    "projectId": project_id,
                    "serviceId": service_id,
                    "environmentId": environment_id,
                    "variables": updates,
                }},
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
    """Patch os.environ, write to local .env if present, push to Railway if
    configured. On Railway containers .env doesn't exist — that branch noops
    and the Railway upsert is what makes the change survive restarts."""
    for k, v in updates.items():
        os.environ[k] = v

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
    _persist_env({
        "ACCESS_TOKEN": data["access_token"],
        "REFRESH_TOKEN": data["refresh_token"],
        "EXPIRES_AT": str(data["expires_at"]),
    })
    print(f"[strava] new token expires at {data['expires_at']}")
    return data["access_token"]


def find_activity_near(
    start_iso: str,
    expected_distance_m: float,
    window_minutes: int = 15,
) -> dict | None:
    """Find a Strava activity within ±window_minutes of start_iso whose
    distance is within 10% of expected_distance_m. Used to locate the
    Garmin auto-synced copy of a just-completed run."""
    token = get_access_token()
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
    best, best_diff = None, None
    for a in candidates:
        d = a.get("distance", 0) or 0
        if expected_distance_m == 0:
            return a
        diff = abs(d - expected_distance_m) / expected_distance_m
        print(f"[strava]     candidate id={a.get('id')} dist={d:.0f}m diff={diff:.4f}")
        if diff <= 0.10 and (best_diff is None or diff < best_diff):
            best, best_diff = a, diff
    if best:
        print(f"[strava]   picked best match: id={best['id']}")
    else:
        print(f"[strava]   no match within 10% distance tolerance")
    return best
