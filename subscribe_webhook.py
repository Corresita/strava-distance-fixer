"""One-shot: register (or re-register) the Strava push subscription that
delivers activity events to our /strava-webhook endpoint.

Strava only allows one subscription per OAuth app, so we delete any existing
one first.

Run once after deploying sync_server.py with STRAVA_WEBHOOK_VERIFY_TOKEN set.
"""
from __future__ import annotations

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
CALLBACK_URL = os.environ.get(
    "STRAVA_WEBHOOK_CALLBACK_URL",
    "https://strava-fixer-production-9c84.up.railway.app/strava-webhook",
)
VERIFY_TOKEN = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN")

if not (CLIENT_ID and CLIENT_SECRET and VERIFY_TOKEN):
    sys.exit("CLIENT_ID, CLIENT_SECRET, and STRAVA_WEBHOOK_VERIFY_TOKEN must be set in .env.")

PARAMS = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}

# 1. List existing subscriptions
print("Checking for existing subscriptions...")
r = requests.get("https://www.strava.com/api/v3/push_subscriptions", params=PARAMS, timeout=10)
if r.status_code != 200:
    sys.exit(f"List failed: {r.status_code} {r.text}")
existing = r.json()
print(f"  found {len(existing)}: {existing}")

# 2. Delete each existing subscription
for sub in existing:
    sub_id = sub["id"]
    print(f"Deleting subscription {sub_id} (callback={sub.get('callback_url')})...")
    d = requests.delete(
        f"https://www.strava.com/api/v3/push_subscriptions/{sub_id}",
        params=PARAMS,
        timeout=10,
    )
    print(f"  -> {d.status_code} {d.text}")

# 3. Create new subscription. Strava will GET our callback to verify, expecting
# the challenge echoed back and the verify_token to match. sync_server's
# /strava-webhook GET handler does this.
print(f"Subscribing {CALLBACK_URL}...")
r = requests.post(
    "https://www.strava.com/api/v3/push_subscriptions",
    data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "callback_url": CALLBACK_URL,
        "verify_token": VERIFY_TOKEN,
    },
    timeout=15,
)
print(f"  -> {r.status_code} {r.text}")
if r.status_code in (200, 201):
    print(f"\n✓ Subscribed. New activity events will hit {CALLBACK_URL}")
else:
    sys.exit("Subscription failed.")
