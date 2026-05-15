"""One-shot Strava OAuth re-authorization.

Spins up a local http server, prints an authorization URL, waits for the
redirect with the auth code, exchanges it for tokens, writes the new tokens
to .env. Requires the Strava app's 'Authorization Callback Domain' to be set
to 'localhost' (in https://www.strava.com/settings/api).
"""
from __future__ import annotations

import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}/exchange"
SCOPES = "read,activity:read_all,activity:write"

if not CLIENT_ID or not CLIENT_SECRET:
    sys.exit("CLIENT_ID and CLIENT_SECRET must be set in .env first.")

AUTH_URL = (
    "https://www.strava.com/oauth/authorize"
    f"?client_id={CLIENT_ID}"
    "&response_type=code"
    f"&redirect_uri={REDIRECT_URI}"
    "&approval_prompt=force"
    f"&scope={SCOPES}"
)

captured = {"code": None, "error": None}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        if "code" in qs:
            captured["code"] = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>OK</h1><p>Authorization captured. You can close this tab.</p>")
        elif "error" in qs:
            captured["error"] = qs["error"][0]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Error: {captured['error']}".encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass


def _persist_env(updates: dict[str, str]) -> None:
    env_path = Path(__file__).parent / ".env"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
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


def main() -> None:
    print(f"\n1) Make sure Strava app's 'Authorization Callback Domain' is 'localhost'")
    print(f"   at https://www.strava.com/settings/api\n")
    print(f"2) Open this URL in your browser (will auto-open in 2s):\n   {AUTH_URL}\n")
    print(f"3) Approve → browser redirects to http://localhost:{PORT}/exchange?code=...")
    print(f"   This script captures the code automatically.\n")

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    import threading
    threading.Timer(2, lambda: webbrowser.open(AUTH_URL)).start()

    while captured["code"] is None and captured["error"] is None:
        server.handle_request()

    if captured["error"]:
        sys.exit(f"OAuth failed: {captured['error']}")

    print(f"Code captured. Exchanging for tokens...")
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": captured["code"],
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    data = resp.json()
    if "access_token" not in data:
        sys.exit(f"Token exchange failed: {data}")

    _persist_env({
        "ACCESS_TOKEN": data["access_token"],
        "REFRESH_TOKEN": data["refresh_token"],
        "EXPIRES_AT": str(data["expires_at"]),
    })
    print(f"\n✓ New tokens saved to .env")
    print(f"  access_token expires at {data['expires_at']}")
    print(f"  athlete: {data.get('athlete', {}).get('username') or data.get('athlete', {}).get('firstname')}")


if __name__ == "__main__":
    main()
