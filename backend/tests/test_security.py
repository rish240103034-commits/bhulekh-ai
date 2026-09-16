"""Security controls: token typing, refresh rotation, password policy, auth, headers.

These codify controls that were verified by hand during the hardening pass. They run
against the real app (in-memory-ish SQLite, rate limiting disabled by conftest) so a
regression in any of them fails CI rather than shipping silently.
"""
import os
from pathlib import Path

os.environ["BHULEKH_DATABASE_URL"] = "sqlite:///./test_security.db"
os.environ["BHULEKH_STORAGE_DIR"] = "./test_storage"
os.environ["BHULEKH_UPLOAD_DIR"] = "./test_storage/uploads"
os.environ["BHULEKH_PROCESSED_DIR"] = "./test_storage/processed"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402

PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def client():
    Path("./test_security.db").unlink(missing_ok=True)
    with TestClient(app) as c:
        yield c


def _login(client, user="admin", pw="Admin@12345"):
    r = client.post(f"{PREFIX}/auth/login", data={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- token typing
def test_access_token_rejected_at_refresh_endpoint(client):
    """A confused-deputy access token must not be accepted where a refresh token belongs."""
    access = _login(client)["access_token"]
    r = client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401


def test_refresh_token_rejected_as_bearer(client):
    """A refresh token must not authenticate a protected endpoint (only access tokens do)."""
    refresh = _login(client)["refresh_token"]
    r = client.get(f"{PREFIX}/auth/me", headers=_auth(refresh))
    assert r.status_code == 401


def test_tampered_token_rejected(client):
    """A token signed with a different key (or mangled) is rejected."""
    access = _login(client)["access_token"]
    r = client.get(f"{PREFIX}/auth/me", headers=_auth(access[:-4] + "AAAA"))
    assert r.status_code == 401


# --------------------------------------------------------------------------- refresh rotation
def test_refresh_rotates_and_old_token_is_single_use(client):
    """Refreshing issues a new pair and revokes the presented token (theft detection)."""
    first = _login(client, "verifier", "Verify@12345")
    rotated = client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200, rotated.text
    new_refresh = rotated.json()["refresh_token"]
    assert new_refresh != first["refresh_token"]

    # Replaying the now-rotated token fails.
    replay = client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401

    # The freshly issued token still works.
    again = client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": new_refresh})
    assert again.status_code == 200


def test_logout_revokes_refresh_token(client):
    """After logout the presented refresh token can no longer be exchanged."""
    session = _login(client, "operator", "Operate@12345")
    r = client.post(f"{PREFIX}/auth/logout", headers=_auth(session["access_token"]),
                    json={"refresh_token": session["refresh_token"]})
    assert r.status_code == 204
    r = client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert r.status_code == 401


def test_deactivating_user_kills_existing_refresh_tokens(client):
    """Toggling a user off revokes their live refresh tokens immediately."""
    admin = _login(client)["access_token"]
    created = client.post(f"{PREFIX}/auth/users", headers=_auth(admin), json={
        "username": "revoke_me", "password": "Str0ng@Pass1", "role": "viewer",
        "full_name": "Temp"})
    assert created.status_code == 201, created.text
    victim = _login(client, "revoke_me", "Str0ng@Pass1")

    users = client.get(f"{PREFIX}/auth/users", headers=_auth(admin)).json()
    uid = next(u["id"] for u in users if u["username"] == "revoke_me")
    assert client.patch(f"{PREFIX}/auth/users/{uid}/toggle", headers=_auth(admin)).status_code == 200

    r = client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": victim["refresh_token"]})
    assert r.status_code == 401


# --------------------------------------------------------------------------- password policy
@pytest.mark.parametrize("weak", ["short1A", "alllowercase1", "ALLUPPERCASE1", "NoDigitsHere"])
def test_password_policy_rejects_weak_passwords(client, weak):
    admin = _login(client)["access_token"]
    r = client.post(f"{PREFIX}/auth/users", headers=_auth(admin), json={
        "username": f"weak_{abs(hash(weak))}", "password": weak, "role": "viewer"})
    assert r.status_code == 422, r.text


def test_password_policy_accepts_strong_password(client):
    admin = _login(client)["access_token"]
    r = client.post(f"{PREFIX}/auth/users", headers=_auth(admin), json={
        "username": "strong_user", "password": "Str0ng@Pass1", "role": "operator"})
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------- authn / authz
def test_protected_endpoints_require_authentication(client):
    for method, path in [("get", "/auth/users"), ("get", "/auth/me"),
                         ("get", "/documents"), ("get", "/dashboard/stats")]:
        r = getattr(client, method)(f"{PREFIX}{path}")
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_forged_token_for_nonexistent_user_is_rejected(client):
    """A validly signed token for a user who does not exist must not authenticate."""
    forged = create_access_token("ghost_user_who_does_not_exist", "admin")
    r = client.get(f"{PREFIX}/auth/me", headers=_auth(forged))
    assert r.status_code == 401


# --------------------------------------------------------------------------- headers
def test_security_headers_present(client):
    r = client.get("/livez")
    h = r.headers
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("X-Frame-Options") == "DENY"
    assert h.get("Referrer-Policy") == "no-referrer"
    assert "default-src 'self'" in h.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in h.get("Content-Security-Policy", "")
    assert "X-Request-ID" in h
    assert "server" not in {k.lower() for k in h}


def test_body_size_limit_rejects_oversized_content_length(client):
    """A declared Content-Length over the cap is refused before the body is read."""
    admin = _login(client)["access_token"]
    huge = str(10 * 1024 * 1024 * 1024)  # 10 GB declared
    r = client.post(f"{PREFIX}/documents/upload", headers={**_auth(admin), "Content-Length": huge},
                    files={"files": ("x.png", b"1", "image/png")})
    assert r.status_code == 413
