import os
import time
import threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

CLIENT_ID = os.environ.get("CLIENT_ID", "236875")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "b1816d956db3f38e72611d7c79a63e575a033698")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "strava_fixer_token")

token_store = {
    "access_token": os.environ.get("ACCESS_TOKEN", ""),
    "refresh_token": os.environ.get("REFRESH_TOKEN", ""),
    "expires_at": 9999999999
}
token_lock = threading.Lock()


def get_access_token():
    with token_lock:
        if time.time() < token_store["expires_at"] - 60:
            return token_store["access_token"]
        print("Refreshing access token...", flush=True)
        resp = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": token_store["refresh_token"],
            "grant_type": "refresh_token"
        }, timeout=10)
        data = resp.json()
        print(f"Token refresh response: {data}", flush=True)
        if "access_token" not in data:
            raise Exception(f"Token refresh failed: {data}")
        token_store["access_token"] = data["access_token"]
        token_store["refresh_token"] = data["refresh_token"]
        token_store["expires_at"] = data["expires_at"]
        return token_store["access_token"]


def fix_distance(activity_id):
    print(f"Activity {activity_id}: thread started", flush=True)
    time.sleep(30)

    max_retries = 5
    retry_interval = 30

    for attempt in range(1, max_retries + 1):
        try:
            print(f"Activity {activity_id}: attempt {attempt}/{max_retries}", flush=True)
            token = get_access_token()
            headers = {"Authorization": f"Bearer {token}"}

            resp = requests.get(
                f"https://www.strava.com/api/v3/activities/{activity_id}",
                headers=headers,
                timeout=10
            )

            if resp.status_code != 200:
                print(f"Activity {activity_id} fetch failed (attempt {attempt}/{max_retries}): "
                      f"{resp.status_code} {resp.text}", flush=True)
                if resp.status_code in (401, 403, 404):
                    return
                if attempt < max_retries:
                    time.sleep(retry_interval)
                return

            activity = resp.json()

            if "distance" not in activity or activity["distance"] == 0:
                print(f"Activity {activity_id} not ready yet (attempt {attempt}/{max_retries}), retrying...", flush=True)
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                print(f"Activity {activity_id}: gave up after {max_retries} attempts.", flush=True)
                return

            original_m = activity["distance"]
            original_km = original_m / 1000
            n = int(original_km)

            if n == 0:
                print(f"Activity {activity_id} is under 1 km ({original_km:.4f} km), skipping.", flush=True)
                return

            rounded_km = float(f"{n}.{n:02d}")
            new_m = rounded_km * 1000

            print(f"Activity {activity_id}: {original_km:.4f} km -> {rounded_km} km", flush=True)

            if abs(new_m - original_m) < 0.01:
                print("Already correct, no update needed.", flush=True)
                return

            resp = requests.put(
                f"https://www.strava.com/api/v3/activities/{activity_id}",
                headers=headers,
                json={"distance": new_m},
                timeout=10
            )
            if resp.status_code == 200:
                print(f"Updated successfully: {rounded_km} km", flush=True)
            else:
                print(f"Update failed: {resp.status_code} {resp.text}", flush=True)
            return

        except Exception as e:
            print(f"Activity {activity_id} error (attempt {attempt}/{max_retries}): {e}", flush=True)
            if attempt < max_retries:
                time.sleep(retry_interval)


@app.route("/webhook", methods=["GET"])
def webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return jsonify({"hub.challenge": challenge})
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook_receive():
    data = request.json
    print(f"Received event: {data}", flush=True)
    if data.get("object_type") == "activity" and data.get("aspect_type") == "create":
        activity_id = data["object_id"]
        t = threading.Thread(target=fix_distance, args=(activity_id,))
        t.start()
    return "OK", 200


@app.route("/", methods=["GET"])
def index():
    return "Strava Distance Fixer is running!", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
