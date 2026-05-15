import os
import json
import time
import threading
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)

CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "strava_fixer_token")
TOKEN_FILE = "/tmp/tokens.json"
RAILWAY_API_URL = "https://backboard.railway.app/graphql/v2"

token_store = {
    "access_token": os.environ.get("ACCESS_TOKEN", ""),
    "refresh_token": os.environ.get("REFRESH_TOKEN", ""),
    "expires_at": int(os.environ.get("EXPIRES_AT", "0"))
}
token_lock = threading.Lock()

web_session = None
web_session_lock = threading.Lock()


def load_tokens():
    try:
        with open(TOKEN_FILE) as f:
            data = json.load(f)
        token_store["access_token"] = data["access_token"]
        token_store["refresh_token"] = data["refresh_token"]
        token_store["expires_at"] = data["expires_at"]
        print("Loaded tokens from file.", flush=True)
    except FileNotFoundError:
        print("No token file found, using env vars.", flush=True)
    except Exception as e:
        print(f"Failed to load token file: {e}, using env vars.", flush=True)


def save_tokens():
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump({
                "access_token": token_store["access_token"],
                "refresh_token": token_store["refresh_token"],
                "expires_at": token_store["expires_at"]
            }, f)
        print("Tokens saved to file.", flush=True)
    except Exception as e:
        print(f"Failed to save tokens: {e}", flush=True)


def update_railway_vars():
    api_token = os.environ.get("RAILWAY_API_TOKEN", "")
    if not api_token:
        return
    project_id = os.environ.get("RAILWAY_PROJECT_ID", "")
    service_id = os.environ.get("RAILWAY_SERVICE_ID", "")
    environment_id = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
    if not all([project_id, service_id, environment_id]):
        print("Railway IDs not found, skipping var update.", flush=True)
        return
    try:
        resp = requests.post(
            RAILWAY_API_URL,
            headers={"Authorization": f"Bearer {api_token}"},
            json={
                "query": "mutation V($i: VariableCollectionUpsertInput!) { variableCollectionUpsert(input: $i) }",
                "variables": {
                    "i": {
                        "projectId": project_id,
                        "serviceId": service_id,
                        "environmentId": environment_id,
                        "variables": {
                            "ACCESS_TOKEN": token_store["access_token"],
                            "REFRESH_TOKEN": token_store["refresh_token"],
                            "EXPIRES_AT": str(token_store["expires_at"])
                        }
                    }
                }
            },
            timeout=10
        )
        result = resp.json()
        if "errors" in result:
            print(f"Railway var update failed: {result['errors']}", flush=True)
        else:
            print("Railway env vars updated automatically.", flush=True)
    except Exception as e:
        print(f"Railway var update error: {e}", flush=True)


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
        save_tokens()
        update_railway_vars()
        return token_store["access_token"]


def get_web_session():
    global web_session
    with web_session_lock:
        cookie_value = os.environ.get("STRAVA_SESSION_COOKIE", "")
        if not cookie_value:
            raise Exception("STRAVA_SESSION_COOKIE not set in env vars")

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36"
        })
        session.cookies.set("_strava4_session", cookie_value, domain=".strava.com")

        # verify session is valid by hitting dashboard
        resp = session.get("https://www.strava.com/dashboard", timeout=15, allow_redirects=False)
        if resp.status_code in (301, 302) and "/login" in resp.headers.get("Location", ""):
            raise Exception("Strava session cookie expired or invalid — refresh STRAVA_SESSION_COOKIE")
        if resp.status_code != 200:
            raise Exception(f"Strava session check failed: {resp.status_code}")

        print("Strava session cookie valid.", flush=True)
        web_session = session
        return session


def get_measurement_preference(token):
    """Return 'feet' (imperial) or 'meters' (metric). Defaults to 'meters' on failure."""
    try:
        resp = requests.get(
            "https://www.strava.com/api/v3/athlete",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("measurement_preference", "meters")
    except Exception as e:
        print(f"Failed to fetch measurement_preference: {e}, assuming metric.", flush=True)
    return "meters"


def fix_distance_web(activity_id, new_m):
    rounded_km = new_m / 1000

    # Strava's edit form displays distance in the user's preferred unit.
    # If imperial, submitting a km-valued string would be parsed as miles and write 1.609x the intended distance.
    token = get_access_token()
    unit_pref = get_measurement_preference(token)
    if unit_pref == "feet":
        display_value = f"{rounded_km / 1.609344:.4f}"  # km -> miles, 4 decimals keeps round-trip within ~1m
        print(f"Activity {activity_id}: web form approach, imperial units → {display_value} mi "
              f"(target {rounded_km} km)", flush=True)
    else:
        display_value = f"{rounded_km:.2f}"
        print(f"Activity {activity_id}: web form approach → {display_value} km", flush=True)

    session = get_web_session()

    resp = session.get(
        f"https://www.strava.com/activities/{activity_id}/edit",
        timeout=15
    )
    if resp.status_code != 200:
        raise Exception(f"Could not load edit page: {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")

    # Modern Strava (Rails/Turbo) requires CSRF in the X-CSRF-Token header,
    # not just the form hidden field. Pull a fresh token from this edit page.
    csrf_meta = soup.find("meta", {"name": "csrf-token"})
    if csrf_meta and csrf_meta.get("content"):
        session.headers["X-CSRF-Token"] = csrf_meta["content"]

    form = soup.find("form", {"id": "edit-activity"}) or soup.find("form")
    if not form:
        raise Exception("Edit form not found on page")

    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        value = inp.get("value", "")
        if name:
            data[name] = value
    for sel in form.find_all("select"):
        name = sel.get("name")
        selected = sel.find("option", {"selected": True})
        if name and selected:
            data[name] = selected.get("value", "")
    for textarea in form.find_all("textarea"):
        name = textarea.get("name")
        if name:
            data[name] = textarea.string or ""

    distance_key = next((k for k in data if "distance" in k.lower()), None)
    if not distance_key:
        raise Exception(f"Distance field not found in form. Fields: {list(data.keys())}")

    data[distance_key] = display_value
    data["_method"] = "put"

    action = form.get("action", f"/activities/{activity_id}")
    url = f"https://www.strava.com{action}" if not action.startswith("http") else action

    resp = session.post(url, data=data, timeout=15, allow_redirects=True)
    if resp.status_code not in (200, 302):
        raise Exception(f"Form submission failed: {resp.status_code}")

    # Verify the change persisted: web form POST 2xx alone doesn't prove it worked.
    # Imperial round-trip can introduce ~1m of float drift, so use 2m tolerance.
    time.sleep(5)
    verify = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10
    )
    if verify.status_code == 200:
        actual_m = verify.json().get("distance", 0)
        if abs(actual_m - new_m) < 2.0:
            print(f"Activity {activity_id}: web form persisted ({actual_m/1000:.4f} km)", flush=True)
            return True
        raise Exception(
            f"Web form submitted but distance not persisted: "
            f"target {new_m/1000:.4f} km, actual {actual_m/1000:.4f} km"
        )
    print(f"Activity {activity_id}: web form submitted (verify GET failed: {verify.status_code})", flush=True)
    return True


def fix_distance(activity_id, initial_wait=120):
    print(f"Activity {activity_id}: thread started", flush=True)
    if initial_wait > 0:
        time.sleep(initial_wait)

    max_retries = 5
    retry_interval = 60
    prev_distance = None
    api_reverted_count = 0

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

            if abs(new_m - original_m) < 0.01:
                print("Already correct, no update needed.", flush=True)
                return

            # GPS activities: API PUT will be silently reverted, route directly to web form.
            # Why: UpdatableActivity model has no `distance` field, so the server async-recomputes
            # from GPS streams. Manual activities (no GPS source) are the only case API can persist.
            is_manual = bool(activity.get("manual", False))
            has_gps = bool(activity.get("start_latlng"))
            if not is_manual and has_gps:
                print(f"Activity {activity_id}: GPS-recorded, routing directly to web form "
                      f"({original_km:.4f} km -> {rounded_km} km)", flush=True)
                try:
                    fix_distance_web(activity_id, new_m)
                except Exception as e:
                    print(f"Activity {activity_id}: web form fallback failed: {e}", flush=True)
                return

            # if API was reverted twice already, switch to web form
            if api_reverted_count >= 2:
                print(f"Activity {activity_id}: API reverted {api_reverted_count}x, switching to web form.", flush=True)
                fix_distance_web(activity_id, new_m)
                return

            # wait for GPS distance to stabilize before PUT
            if prev_distance is not None and abs(original_m - prev_distance) > 0.01:
                print(f"Activity {activity_id}: distance still changing "
                      f"({prev_distance/1000:.4f} -> {original_km:.4f} km), waiting...", flush=True)
                prev_distance = original_m
                if attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
            prev_distance = original_m

            print(f"Activity {activity_id}: {original_km:.4f} km -> {rounded_km} km", flush=True)

            resp = requests.put(
                f"https://www.strava.com/api/v3/activities/{activity_id}",
                headers=headers,
                json={"distance": new_m},
                timeout=10
            )
            if resp.status_code != 200:
                print(f"Update failed: {resp.status_code} {resp.text}", flush=True)
                return

            time.sleep(30)
            verify = requests.get(
                f"https://www.strava.com/api/v3/activities/{activity_id}",
                headers=headers,
                timeout=10
            )
            if verify.status_code == 200:
                actual_m = verify.json().get("distance", 0)
                if abs(actual_m - new_m) < 0.01:
                    print(f"Updated successfully via API: {rounded_km} km", flush=True)
                    return
                else:
                    api_reverted_count += 1
                    print(f"Strava reverted distance to {actual_m/1000:.4f} km "
                          f"(revert #{api_reverted_count}), will retry...", flush=True)
                    prev_distance = actual_m
                    if attempt < max_retries:
                        time.sleep(retry_interval)
                        continue
                    # last attempt: try web form
                    fix_distance_web(activity_id, new_m)
                    return
            else:
                print(f"Updated successfully (unverified): {rounded_km} km", flush=True)
                return

        except Exception as e:
            print(f"Activity {activity_id} error (attempt {attempt}/{max_retries}): {e}", flush=True)
            if attempt < max_retries:
                time.sleep(retry_interval)


load_tokens()


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


@app.route("/fix/<int:activity_id>", methods=["GET"])
def manual_fix(activity_id):
    t = threading.Thread(target=fix_distance, args=(activity_id,), kwargs={"initial_wait": 0})
    t.start()
    return f"Started fix for activity {activity_id}, check Railway logs.", 200


@app.route("/", methods=["GET"])
def index():
    return "Strava Distance Fixer is running!", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
