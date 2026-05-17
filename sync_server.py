"""HTTP wrapper around sync.run() for remote triggering.

Designed for Railway deployment + iOS Shortcut invocation. Single endpoint
that runs the same Garmin → scale → Strava pipeline as `python sync.py` and
returns the result as JSON.

Endpoints
---------
GET  /              health check (no auth)
POST /sync          trigger sync on latest Garmin running activity
POST /sync?aid=<id> trigger sync on a specific activity ID
POST /sync?force=1  re-process even if it's in history.json

Auth
----
Every /sync request must include header `X-Sync-Secret: <SYNC_SECRET>` matching
the env var. Without this anyone who knows your Railway domain could trigger
uploads.

Token bootstrap
---------------
On startup, if env var GARMIN_TOKEN_B64 is set and the local
garmin_tokens/garmin_tokens.json doesn't exist, decode the base64 blob and
write it. This lets fresh Railway containers reuse a token captured locally
without ever hitting Garmin SSO again — critical because Garmin SSO is
aggressively rate-limited and a Railway redeploy that fell back to fresh
login could trigger a 48-hour account lock.

To capture the token blob for the env var, run locally after `python sync.py`
has succeeded once:

    python -c "import base64,pathlib; \
        print(base64.b64encode(pathlib.Path('garmin_tokens/garmin_tokens.json').read_bytes()).decode())"
"""
from __future__ import annotations

import base64
import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()


def _bootstrap_garmin_token() -> None:
    blob = os.environ.get("GARMIN_TOKEN_B64", "")
    if not blob:
        return
    target = Path(__file__).parent / "garmin_tokens" / "garmin_tokens.json"
    if target.exists():
        print(f"[sync_server] garmin_tokens.json exists, skipping bootstrap", flush=True)
        return
    try:
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(base64.b64decode(blob))
        print(f"[sync_server] wrote {target} from GARMIN_TOKEN_B64 ({len(blob)}b base64)", flush=True)
    except Exception as e:
        print(f"[sync_server] ERROR bootstrapping Garmin token: {e}", file=sys.stderr, flush=True)


_bootstrap_garmin_token()

# Import sync AFTER bootstrap so the cached token is in place before any
# Garmin call is attempted.
import sync  # noqa: E402

app = Flask(__name__)
SECRET = os.environ.get("SYNC_SECRET", "")


def _authorized() -> bool:
    if not SECRET:
        return False  # no secret configured = lock everything
    return request.headers.get("X-Sync-Secret") == SECRET


@app.route("/", methods=["GET"])
def health():
    return "Strava Distance Fixer (v2 sync server) is running.", 200


@app.route("/sync", methods=["POST"])
def trigger_sync():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401

    aid_raw = request.args.get("aid") or request.args.get("activity_id")
    if request.is_json:
        body = request.get_json(silent=True) or {}
        aid_raw = aid_raw or body.get("activity_id")
    try:
        aid = int(aid_raw) if aid_raw else None
    except (TypeError, ValueError):
        return jsonify({"error": f"invalid activity_id: {aid_raw!r}"}), 400

    force = (request.args.get("force") or "").lower() in ("1", "true", "yes")

    try:
        result = sync.run(aid, force=force)
        status = 200 if result.get("ok") else 500
        return jsonify(result), status
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
