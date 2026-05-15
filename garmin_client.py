"""Thin wrapper around python-garminconnect with cached OAuth tokens.

Cached tokens live in ./garmin_tokens/ — subsequent runs reuse them and skip
the full SSO + MFA dance. If the cache is invalid we fall through to a fresh
login (interactive MFA via prompt_mfa).
"""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

from garminconnect import Garmin

TOKENSTORE = Path(__file__).parent / "garmin_tokens"


def _prompt_mfa() -> str:
    code = os.environ.get("GARMIN_MFA_CODE")
    if code:
        return code.strip()
    return input("Garmin MFA code: ").strip()


def login() -> Garmin:
    email = os.environ.get("GARMIN_EMAIL", "")
    password = os.environ.get("GARMIN_PASSWORD", "")
    client = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
    if TOKENSTORE.exists():
        try:
            client.login(str(TOKENSTORE))
            return client
        except Exception:
            pass  # fall through to fresh login
    if not email or not password:
        raise RuntimeError("GARMIN_EMAIL / GARMIN_PASSWORD required for first-time login")
    client = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
    client.login()
    client.client.dump(str(TOKENSTORE))
    return client


def latest_running_activity(client: Garmin) -> dict | None:
    """Return the most recent GPS-running activity, or None."""
    for a in client.get_activities(0, 10):
        sport = (a.get("activityType") or {}).get("typeKey", "")
        if sport in ("running", "trail_running", "treadmill_running", "track_running"):
            if (a.get("distance") or 0) > 0 and a.get("startLatitude") is not None:
                return a
    return None


def get_activity(client: Garmin, activity_id: int) -> dict:
    return client.get_activity(activity_id)


def download_tcx(client: Garmin, activity_id: int) -> bytes:
    raw = client.download_activity(activity_id, dl_fmt=Garmin.ActivityDownloadFormat.TCX)
    # Garmin TCX downloads are sometimes returned as bare XML, sometimes zipped.
    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next(n for n in z.namelist() if n.endswith(".tcx"))
            return z.read(name)
    return raw
