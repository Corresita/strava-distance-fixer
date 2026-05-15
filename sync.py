"""Main entry point.

Usage:
    python sync.py                 # process latest Garmin running activity
    python sync.py <activity_id>   # process a specific Garmin activity
    python sync.py --force         # re-process latest even if already in history

Pipeline:
    Garmin Connect → download TCX → scale GPS path to N.NN km
                                  → find the Strava activity that Garmin
                                    auto-synced and delete it
                                  → upload the scaled TCX
                                  → if scaled upload fails, fall back to
                                    uploading the original (un-scaled) TCX
                                    so the activity always lands on Strava

This requires Garmin Connect's direct sync to Strava to remain ON: that
sync pushes the original activity into Strava, we delete it, and upload our
modified copy. Garmin Connect data itself is never touched.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import garmin_client
import strava_uploader
import tcx_scaler

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
    pipeline_path: str  # scaled_uploaded | fallback_original | skipped_existing | failed
    scale_factor: float | None
    strava_deleted_id: int | None
    strava_new_id: int | None
    fallback_reason: str | None
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
        if e.get("garmin_activity_id") == garmin_id and e.get("strava_new_id"):
            return True
    return False


def target_km(distance_m: float) -> float | None:
    km = distance_m / 1000.0
    n = int(km)
    if n == 0:
        return None
    return float(f"{n}.{n:02d}")


def wait_for_garmin_sync_to_strava(
    start_iso: str, expected_m: float, deadline_s: int = 180
) -> dict | None:
    """Garmin auto-sync to Strava is typically <2 min after activity completion.
    Poll until we see the activity show up so we can delete it before uploading
    our scaled version. Returns the Strava activity dict or None on timeout."""
    log.info(f"  waiting up to {deadline_s}s for Garmin → Strava auto-sync...")
    end = time.time() + deadline_s
    while time.time() < end:
        found = strava_uploader.find_activity_near(start_iso, expected_m)
        if found:
            log.info(f"  found Strava activity {found['id']} ({found.get('distance', 0)/1000:.4f} km)")
            return found
        time.sleep(10)
    log.info(f"  no auto-synced Strava activity found within {deadline_s}s — uploading clean")
    return None


def run(activity_id: int | None, force: bool, no_delete: bool) -> dict:
    log.info("=" * 70)
    log.info(f"sync started  force={force}  activity_id={activity_id}  no_delete={no_delete}")
    ts_now = datetime.now().isoformat(timespec="seconds")

    log.info("Logging into Garmin...")
    client = garmin_client.login()

    if activity_id is None:
        log.info("Looking up latest running activity...")
        activity = garmin_client.latest_running_activity(client)
        if activity is None:
            log.error("No recent GPS running activity found.")
            return {"ok": False, "pipeline_path": "no_activity", "error": "no recent GPS running activity"}
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
        entry = HistoryEntry(
            ts=ts_now, garmin_activity_id=activity_id, garmin_name=garmin_name,
            original_km=original_km, target_km=0.0, pipeline_path="skipped_existing",
            scale_factor=None, strava_deleted_id=None, strava_new_id=None,
            fallback_reason=None, error=None,
        )
        append_history(entry)
        return {"ok": True, **asdict(entry), "strava_url": None}

    tgt_km = target_km(original_m)
    if tgt_km is None:
        log.info("Under 1 km, skipping.")
        return {"ok": True, "pipeline_path": "skipped_short", "garmin_activity_id": activity_id,
                "garmin_name": garmin_name, "original_km": original_km, "target_km": None,
                "error": None}
    target_m = tgt_km * 1000

    log.info(f"Target: {tgt_km} km")

    log.info("Downloading TCX from Garmin...")
    tcx_bytes = garmin_client.download_tcx(client, activity_id)
    log.info(f"  got {len(tcx_bytes)} bytes")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        in_tcx = td / f"{activity_id}.tcx"
        scaled_tcx = td / f"{activity_id}_scaled.tcx"
        in_tcx.write_bytes(tcx_bytes)

        # 1. Scale (may fail; record reason if so)
        scale_factor: float | None = None
        fallback_reason: str | None = None
        try:
            r = tcx_scaler.scale_tcx(in_tcx, scaled_tcx, target_m)
            scale_factor = r.scale_factor
            log.info(f"  scaled: {r.original_distance_m/1000:.4f} km -> "
                     f"{r.target_distance_m/1000:.4f} km  (k={r.scale_factor:.6f})")
        except Exception as e:
            fallback_reason = f"scale_failed: {type(e).__name__}: {e}"
            log.warning(f"  ⚠️  TCX scaling failed: {fallback_reason}")
            scaled_tcx.write_bytes(tcx_bytes)  # fall back at this stage too

        # 2. Find + delete the Garmin auto-synced copy on Strava (unless --no-delete)
        deleted_id: int | None = None
        if not no_delete and start_iso:
            existing = wait_for_garmin_sync_to_strava(start_iso, original_m)
            if existing:
                if strava_uploader.delete_activity(existing["id"]):
                    deleted_id = existing["id"]
                    time.sleep(3)  # let the delete propagate before re-upload

        # 3. Upload scaled. If that fails, upload the original (un-modified) TCX
        # as a safety net so the activity at least exists on Strava.
        result = None
        pipeline_path = ""
        try:
            log.info("Uploading scaled TCX to Strava...")
            result = strava_uploader.upload_tcx(
                scaled_tcx, name=garmin_name, external_id=f"garmin-{activity_id}-scaled",
            )
            if result.activity_id:
                pipeline_path = "scaled_uploaded" if fallback_reason is None else "fallback_original"
            else:
                raise RuntimeError(f"Strava processing error: {result.error}")
        except Exception as e:
            log.warning(f"  ⚠️  scaled upload failed: {e}; falling back to original TCX")
            fallback_reason = (fallback_reason + " | " if fallback_reason else "") + f"scaled_upload_failed: {e}"
            try:
                result = strava_uploader.upload_tcx(
                    in_tcx, name=garmin_name, external_id=f"garmin-{activity_id}-original",
                )
                pipeline_path = "fallback_original" if result.activity_id else "failed"
            except Exception as e2:
                log.error(f"  ✗  fallback upload also failed: {e2}")
                fallback_reason += f" | fallback_upload_failed: {e2}"
                pipeline_path = "failed"

        new_id = result.activity_id if result else None
        error_msg = None if (result and new_id) else (result.error if result else "no upload attempt succeeded")

        entry = HistoryEntry(
            ts=ts_now, garmin_activity_id=activity_id, garmin_name=garmin_name,
            original_km=original_km, target_km=tgt_km, pipeline_path=pipeline_path,
            scale_factor=scale_factor, strava_deleted_id=deleted_id,
            strava_new_id=new_id, fallback_reason=fallback_reason, error=error_msg,
        )
        append_history(entry)

        strava_url = f"https://www.strava.com/activities/{new_id}" if new_id else None
        ok = pipeline_path in ("scaled_uploaded", "fallback_original")

        if pipeline_path == "scaled_uploaded":
            log.info(f"✓ DONE  scaled  {strava_url}  ({tgt_km} km)")
        elif pipeline_path == "fallback_original":
            log.warning(f"⚠ DONE  original-fallback  {strava_url}  "
                        f"(distance NOT modified: {original_km:.2f} km)")
        else:
            log.error(f"✗ FAILED  {error_msg}")

        return {"ok": ok, **asdict(entry), "strava_url": strava_url}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("activity_id", nargs="?", type=int, default=None,
                   help="Garmin activity ID (default: latest running)")
    p.add_argument("--force", action="store_true",
                   help="re-process even if already in history.json")
    p.add_argument("--no-delete", action="store_true",
                   help="don't search for / delete the Strava auto-synced copy")
    args = p.parse_args()
    result = run(args.activity_id, args.force, args.no_delete)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
