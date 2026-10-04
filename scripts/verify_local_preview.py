"""Verify available local preview logins and write browser state without logging secrets."""

import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PREVIEW_USERS = ("provider-test", "preview")


def verify_preview(username: str, email: str, password: str) -> None:
    with httpx.Client(base_url="http://127.0.0.1:7340", timeout=30) as client:
        response = client.post("/login", headers={"Origin": str(client.base_url).rstrip("/")}, data={
            "username": email, "password": password, "action": "login", "next": "/home",
        })
        assert response.status_code in (302, 303), f"Login failed: {response.status_code}"
        token = client.cookies.get("token")
        assert token, "Login did not set session cookie"
        state = {
            "cookies": [{
                "name": "token", "value": token, "domain": "127.0.0.1", "path": "/",
                "expires": int(time.time()) + 1800, "httpOnly": True, "secure": False, "sameSite": "Lax",
            }],
            "origins": [],
        }
        (ROOT / f".venv/{username}-browser.json").write_text(json.dumps(state), encoding="utf-8")
        for path in (
            "/home", f"/user/{username}/movies", f"/user/{username}/series",
            "/recent-events", f"/user/{username}/library", "/browse",
        ):
            page = client.get(path, follow_redirects=True)
            assert page.status_code == 200, f"{path}: {page.status_code}"
        for kind in ("movie", "series"):
            response = client.get(f"/api/proxy/tracking/profile/{username}/{kind}")
            response.raise_for_status()
            result = response.json()
            assert result["owner"]
            print(username, kind, "entries:", len(result["entries"]))
        connections = client.get("/api/proxy/auth/connections", headers={"Authorization": f"Bearer {token}"})
        connections.raise_for_status()
        assert all(
            not connection.get("token")
            for connection in connections.json()
            if connection.get("type") in ("stremio", "nuvio", "arvio")
        ), "Cloud connection secret leaked through the response model"
        print(username, "login and local page checks passed")


def main() -> None:
    lines = (ROOT / ".venv/LOCAL-LOGIN.txt").read_text(encoding="utf-8").splitlines()
    passwords = dict(line.split(": ", 1) for line in lines if ": " in line)
    users = [username for username in PREVIEW_USERS if username in passwords]
    if not users:
        raise SystemExit("No preview login details found in .venv/LOCAL-LOGIN.txt.")
    if any(f"{username} email" not in passwords for username in users):
        raise SystemExit("Run scripts/prepare_local_login.py to refresh email login details.")
    for username in users:
        verify_preview(username, passwords[f"{username} email"], passwords[username])


if __name__ == "__main__":
    main()
