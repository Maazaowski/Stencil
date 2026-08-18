"""Role boundaries on the high-blast-radius endpoints.

Regression cover for a privilege-escalation chain: `debug` is a DB-persisted
runtime setting, and the purge endpoint is gated on it. While settings writes
were open to any signed-in user, a `role="user"` account could flip debug on
and then delete every intake record and work-directory artifact — and could
also overwrite the stored LLM provider key.

Same DB-hygiene rules as test_auth.py: every test leaves the users table empty
so the rest of the suite keeps running in open mode.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from stencil import auth
from stencil.config import settings
from stencil.db.models import AuthSession, User
from stencil.db.session import SessionLocal
from stencil.main import app

ADMIN = ("boss@temforce.com", "admin-pass-1")
MEMBER = ("member@temforce.com", "member-pass-1")


@pytest.fixture(autouse=True)
def _isolated_auth_env(monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "admin_email", "")
    monkeypatch.setattr(settings, "admin_password", "")
    db = SessionLocal()
    db.query(AuthSession).delete()
    db.query(User).delete()
    db.commit()
    db.close()
    yield


@pytest.fixture()
def two_users() -> Iterator[None]:
    """One admin and one ordinary member."""
    db = SessionLocal()
    for (email, password), role in ((ADMIN, "admin"), (MEMBER, "user")):
        db.add(
            User(
                email=email,
                username=email.split("@")[0],
                password_hash=auth.hash_password(password),
                role=role,
                is_active=True,
            )
        )
    db.commit()
    try:
        yield
    finally:
        db.query(AuthSession).delete()
        db.query(User).delete()
        db.commit()
        db.close()


def _login(client: TestClient, creds: tuple[str, str]) -> None:
    email, password = creds
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", "/api/v1/settings", {"debug": True}),
        ("put", "/api/v1/settings/keys", {"provider": "openai", "api_key": "sk-attacker"}),
        ("delete", "/api/v1/settings/keys/openai", None),
        ("delete", "/api/v1/invoices/purge", None),
        ("get", "/api/v1/debug/ai-calls", None),
    ],
)
def test_non_admin_cannot_reach_privileged_endpoints(two_users, method, path, body):
    with TestClient(app) as client:
        _login(client, MEMBER)
        resp = client.request(method.upper(), path, json=body)
        assert resp.status_code == 403, f"{method.upper()} {path} -> {resp.status_code}"
        assert resp.json()["detail"] == "Admin access required"


def test_non_admin_cannot_escalate_via_debug_then_purge(two_users):
    """The full chain, end to end: flip debug, then wipe the data."""
    with TestClient(app) as client:
        _login(client, MEMBER)
        assert client.put("/api/v1/settings", json={"debug": True}).status_code == 403
        assert client.delete("/api/v1/invoices/purge").status_code == 403


def test_admin_can_still_read_and_write_settings(two_users):
    with TestClient(app) as client:
        _login(client, ADMIN)
        assert client.get("/api/v1/settings").status_code == 200
        # Round-trip a harmless value rather than toggling debug for the suite.
        resp = client.put("/api/v1/settings", json={"openai_max_retries": 3})
        assert resp.status_code == 200
        assert resp.json()["openai_max_retries"] == 3


def test_settings_remain_readable_by_a_non_admin(two_users):
    """Reads stay open — the page exposes no secrets, only key-set booleans."""
    with TestClient(app) as client:
        _login(client, MEMBER)
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200
        assert "api_key" not in resp.text.replace("api_key_set", "")


def test_session_cookie_carries_the_hardened_flags(two_users, monkeypatch):
    # Production posture: Secure on. (Auto-resolution turns it off in debug so
    # plain-HTTP local dev still works — covered by the next test.)
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}
        )
        set_cookie = resp.headers["set-cookie"]
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert "SameSite=lax" in set_cookie
        # Lifetime comes from session_ttl_days, not from last_login_at (which is
        # None on a first login and silently produced a session-only cookie).
        assert f"Max-Age={settings.session_ttl_days * 86400}" in set_cookie


@pytest.mark.parametrize(
    ("configured", "debug", "expect_secure"),
    [
        (None, False, True),   # production default: Secure on
        (None, True, False),   # debug/local HTTP: browser would drop a Secure cookie
        (True, True, True),    # explicit override wins
        (False, False, False),
    ],
)
def test_secure_flag_resolution(monkeypatch, configured, debug, expect_secure):
    monkeypatch.setattr(settings, "session_cookie_secure", configured)
    monkeypatch.setattr(settings, "debug", debug)
    assert auth.session_cookie_secure() is expect_secure
