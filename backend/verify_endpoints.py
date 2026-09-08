import urllib.request
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def make_request(method, path, data=None, token=None):
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body_bytes = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            return resp.status, json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        content = e.read().decode("utf-8")
        return e.code, json.loads(content) if content else {}

def main():
    print("========================================")
    print("Testing ChatConnect Backend API Endpoints")
    print("========================================")

    # 1. Health Check
    status, body = make_request("GET", "/")
    print(f"[1] GET / -> Status {status}: {body}")
    assert status == 200, f"Expected 200, got {status}"
    assert body.get("message") == "Welcome to ChatConnect API"

    ts = int(time.time())
    user_a = f"endpoint_user_{ts}"
    pass_a = "StrongPass#2026"

    # 2. Register new user
    status, body = make_request("POST", "/api/auth/register", {"username": user_a, "password": pass_a})
    token_a = body.get("access_token")
    user_obj = body.get("user", {})
    print(f"[2] POST /api/auth/register -> Status {status}: Registered '{user_obj.get('username')}' (id={user_obj.get('id')})")
    assert status == 200, f"Registration failed with status {status}"
    assert token_a is not None, "Missing access_token"
    assert "password" not in user_obj and "hashed_password" not in user_obj

    # 3. Register duplicate user (Error handling)
    status, body = make_request("POST", "/api/auth/register", {"username": user_a, "password": pass_a})
    print(f"[3] POST /api/auth/register (duplicate) -> Status {status}: {body.get('detail')}")
    assert status == 400, f"Expected 400, got {status}"

    # 4. Login with correct credentials
    status, body = make_request("POST", "/api/auth/login", {"username": user_a, "password": pass_a})
    print(f"[4] POST /api/auth/login (valid) -> Status {status}: User '{body.get('user', {}).get('username')}' logged in")
    assert status == 200, f"Expected 200, got {status}"

    # 5. Login with invalid password
    status, body = make_request("POST", "/api/auth/login", {"username": user_a, "password": "WrongPassword"})
    print(f"[5] POST /api/auth/login (wrong password) -> Status {status}: {body.get('detail')}")
    assert status == 401, f"Expected 401, got {status}"

    # 6. Current user /me (Authorized)
    status, body = make_request("GET", "/api/auth/me", token=token_a)
    print(f"[6] GET /api/auth/me (authorized) -> Status {status}: Authenticated as '{body.get('username')}'")
    assert status == 200, f"Expected 200, got {status}"
    assert body.get("username") == user_a

    # 7. Current user /me (Unauthorized - Missing token)
    status, body = make_request("GET", "/api/auth/me")
    print(f"[7] GET /api/auth/me (missing token) -> Status {status}: {body.get('detail')}")
    assert status == 401, f"Expected 401, got {status}"

    # 8. List users /users (Authorized)
    status, body = make_request("GET", "/api/auth/users", token=token_a)
    print(f"[8] GET /api/auth/users (authorized) -> Status {status}: Found {len(body)} other users")
    assert status == 200, f"Expected 200, got {status}"
    assert all(u.get("username") != user_a for u in body), "Current user should not be in other users list"

    # 9. List users /users (Unauthorized)
    status, body = make_request("GET", "/api/auth/users")
    print(f"[9] GET /api/auth/users (unauthorized) -> Status {status}: {body.get('detail')}")
    assert status == 401, f"Expected 401, got {status}"

    print("\n========================================")
    print(" ALL 9 LIVE ENDPOINTS CHECKS PASSED!")
    print("========================================")

if __name__ == "__main__":
    main()
