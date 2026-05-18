"""Main entry point.

Usage:
    python sync.py                 # process latest Garmin running activity
    python sync.py <activity_id>   # process a specific Garmin activity
    python sync.py --force         # re-process even if already in history

Pipeline:
    1. Find the latest Garmin running activity (or one by ID).
    2. Compute the N.NN km target.
    3. Wait for Garmin Connect's auto-sync to push the activity to Strava.
    4. Crop the Strava activity (via web `truncate` form) down to N.NN km.

The Strava activity stays in place — same ID, same kudos, same comments.
We just trim a handful of GPS points off the end so its distance matches
the target. Nothing is deleted, no re-upload, no duplicates.

The crop requires `_strava4_session` cookie (Strava web session) since the
public OAuth API doesn't expose this operation. Capture the cookie from a
logged-in browser and put it in env var `STRAVA_SESSION_COOKIE`. Cookies
last weeks-to-months; refresh when the script reports a 401 / login redirect.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import garmin_client
import strava_cropper
import strava_uploader

load_dotenv()

ROOT = Path(__file__).parent
HISTORY = ROOT / "history.json"
LOG_FILE = ROOT / "sync.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("sync")


@dataclass
class HistoryEntry:
    ts: str
    garmin_activity_id: int
    garmin_name: str
    original_km: float
    target_km: float
    pipeline_path: str  # cropped | skipped_existing | skipped_short | no_activity | failed
    strava_activity_id: int | None
    final_km: float | None
    points_dropped: int | None
    error: str | None


def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    try:
        return json.loads(HISTORY.read_text())
    except json.JSONDecodeError:
        return []


def append_history(entry: HistoryEntry) -> None:
    h = load_history()
    h.append(asdict(entry))
    HISTORY.write_text(json.dumps(h, indent=2))


def already_synced(garmin_id: int) -> bool:
    for e in load_history():
        if e.get("garmin_activity_id") == garmin_id and e.get("pipeline_path") == "cropped":
            return True
    return False


def target_km(distance_m: float) -> float | None:
    """Round to the repeating-digit N.NN km form. Return None if < 1 km."""
    km = distance_m / 1000.0
    n = int(km)
    if n == 0:
        return None
    return float(f"{n}.{n:02d}")


def wait_for_garmin_sync_to_strava(
    start_iso: str, expected_m: float, deadline_s: int = 180
) -> dict | None:
    """Garmin auto-sync to Strava is typically <2 min after activity completion.
    Poll until we see the activity show up on Strava."""
    log.info(f"  waiting up to {deadline_s}s for Garmin → Strava auto-sync...")
    end = time.time() + deadline_s
    while time.time() < end:
        found = strava_uploader.find_activity_near(start_iso, expected_m)
        if found:
            log.info(f"  found Strava activity {found['id']} ({found.get('distance', 0)/1000:.4f} km)")
            return found
        time.sleep(10)
    log.info(f"  no auto-synced Strava activity found within {deadline_s}s")
    return None


def run(activity_id: int | None, force: bool) -> dict:
    log.info("=" * 70)
    log.info(f"sync started  force={force}  activity_id={activity_id}")
    ts_now = datetime.now().isoformat(timespec="seconds")

    log.info("Logging into Garmin...")
    client = garmin_client.login()

    if activity_id is None:
        log.info("Looking up latest running activity...")
        activity = garmin_client.latest_running_activity(client)
        if activity is None:
            log.error("No recent GPS running activity found.")
            return {"ok": False, "pipeline_path": "no_activity",
                    "error": "no recent GPS running activity"}
        activity_id = activity["activityId"]
    else:
        activity = garmin_client.get_activity(client, activity_id)

    garmin_name = activity.get("activityName", "?")
    original_m = activity.get("distance") or 0
    original_km = original_m / 1000.0
    start_iso = activity.get("startTimeGMT") or activity.get("startTimeLocal")
    if start_iso and "T" not in start_iso:
        start_iso = start_iso.replace(" ", "T") + "Z"
    log.info(f"Activity {activity_id}: {garmin_name!r}  {original_km:.4f} km  start={start_iso}")

    if not force and already_synced(activity_id):
        log.info("Already synced (use --force to redo). Done.")
        return {"ok": True, "pipeline_path": "skipped_existing",
                "garmin_activity_id": activity_id, "garmin_name": garmin_name,
                "original_km": original_km, "error": None}

    tgt_km = target_km(original_m)
    if tgt_km is None:
        log.info("Under 1 km, skipping.")
        return {"ok": True, "pipeline_path": "skipped_short",
                "garmin_activity_id": activity_id, "garmin_name": garmin_name,
                "original_km": original_km, "error": None}
    target_m = tgt_km * 1000

    log.info(f"Target: {tgt_km} km ({target_m:.0f} m)")

    if not start_iso:
        return {"ok": False, "pipeline_path": "failed",
                "error": "no startTimeGMT/Local on Garmin activity"}

    strava = wait_for_garmin_sync_to_strava(start_iso, original_m)
    if strava is None:
        entry = HistoryEntry(
            ts=ts_now, garmin_activity_id=activity_id, garmin_name=garmin_name,
            original_km=original_km, target_km=tgt_km, pipeline_path="failed",
            strava_activity_id=None, final_km=None, points_dropped=None,
            error="Strava auto-sync did not arrive within 180s",
        )
        append_history(entry)
        log.error("✗ FAILED  no Strava activity to crop")
        return {"ok": False, **asdict(entry), "strava_url": None}

    strava_id = strava["id"]
    log.info(f"Cropping Strava activity {strava_id} to {tgt_km} km...")

    strava_url = f"https://www.strava.com/activities/{strava_id}"
    try:
        result = strava_cropper.crop_to_distance(strava_id, target_m)
    except Exception as e:
        entry = HistoryEntry(
            ts=ts_now, garmin_activity_id=activity_id, garmin_name=garmin_name,
            original_km=original_km, target_km=tgt_km, pipeline_path="failed",
            strava_activity_id=strava_id, final_km=None, points_dropped=None,
            error=f"{type(e).__name__}: {e}",
        )
        append_history(entry)
        log.error(f"✗ FAILED  crop raised: {e}")
        return {"ok": False, **asdict(entry), "strava_url": strava_url}

    entry = HistoryEntry(
        ts=ts_now, garmin_activity_id=activity_id, garmin_name=garmin_name,
        original_km=original_km, target_km=tgt_km, pipeline_path="cropped",
        strava_activity_id=strava_id,
        final_km=result["final_distance_m"] / 1000.0,
        points_dropped=result["points_dropped"],
        error=None,
    )
    append_history(entry)

    log.info(f"✓ DONE  cropped  {strava_url}  "
             f"({original_km:.4f} → {result['final_distance_m']/1000:.4f} km, "
             f"dropped {result['points_dropped']} points)")
    return {"ok": True, **asdict(entry), "strava_url": strava_url}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("activity_id", nargs="?", type=int, default=None,
                   help="Garmin activity ID (default: latest running)")
    p.add_argument("--force", action="store_true",
                   help="re-process even if already in history.json")
    args = p.parse_args()
    result = run(args.activity_id, args.force)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
