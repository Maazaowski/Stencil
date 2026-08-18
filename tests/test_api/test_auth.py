"""Login system — offline tests (in-memory sqlite from conftest).

IMPORTANT: the test DB is shared module-level state and the auth middleware
runs in open mode only while the users table is EMPTY. Every test here cleans
up the users/sessions it creates so the rest of the API suite stays unauthed.
"""

import pytest
from fastapi.testclient import TestClient

from stencil import auth
from stencil.config import settings
from stencil.db.models import AuthSession, User
from stencil.db.session import SessionLocal
from stencil.main import app


@pytest.fixture(autouse=True)
def _isolated_auth_env(monkeypatch):
    """Tests must not depend on the developer's .env: TestClient's lifespan runs
    bootstrap_admin with the REAL settings, so live ST_ADMIN_* creds would seed
    a user into the shared test DB. Blank them and start every test user-free."""
    monkeypatch.setattr(settings, "admin_email", "")
    monkeypatch.setattr(settings, "admin_password", "")
    db = SessionLocal()
    db.query(AuthSession).delete()
    db.query(User).delete()
    db.commit()
    db.close()
    yield


@pytest.fixture()
def user_db():
    """Create one known user; wipe users+sessions afterward (open mode restored)."""
    db = SessionLocal()
    db.add(
        User(
            email="admin@temforce.com",
            username="Admin",
            password_hash=auth.hash_password("pilot-pass-1"),
            role="admin",
            is_active=True,
        )
    )
    db.commit()
    try:
        yield db
    finally:
        db.query(AuthSession).delete()
        db.query(User).delete()
        db.commit()
        db.close()


def test_open_mode_while_no_users_exist():
    with TestClient(app) as client:
        assert client.get("/api/v1/settings").status_code == 200
        assert client.get("/health").status_code == 200


def test_login_sets_cookie_and_me_works(user_db):
    with TestClient(app) as client:
        # Unauthenticated is rejected once a user exists.
        assert client.get("/api/v1/settings").status_code == 401
        assert client.get("/api/v1/auth/me").status_code == 401

        bad = client.post("/api/v1/auth/login", json={"email": "admin@temforce.com", "password": "wrong"})
        assert bad.status_code == 401
        assert bad.json()["detail"] == "Invalid email or password"
        # Unknown email returns the SAME generic error (no enumeration).
        unknown = client.post("/api/v1/auth/login", json={"email": "nobody@x.com", "password": "wrong"})
        assert unknown.status_code == 401
        assert unknown.json()["detail"] == bad.json()["detail"]

        ok = client.post(
            "/api/v1/auth/login",
            json={"email": "Admin@Temforce.com", "password": "pilot-pass-1"},  # case-insensitive email
        )
        assert ok.status_code == 200
        payload = ok.json()
        assert payload["email"] == "admin@temforce.com"
        assert payload["username"] == "Admin"
        assert payload["role"] == "admin"
        assert payload["is_admin"] is True
        assert auth.SESSION_COOKIE in client.cookies

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "admin@temforce.com"
        # Gated endpoints now work with the session cookie.
        assert client.get("/api/v1/settings").status_code == 200


def test_logout_invalidates_session(user_db):
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"email": "admin@temforce.com", "password": "pilot-pass-1"})
        assert client.get("/api/v1/auth/me").status_code == 200
        token = client.cookies.get(auth.SESSION_COOKIE)

        client.post("/api/v1/auth/logout")
        # Even replaying the old token fails: the server-side session is gone.
        replay = client.get("/api/v1/auth/me", cookies={auth.SESSION_COOKIE: token})
        assert replay.status_code == 401


def test_health_and_root_stay_open(user_db):
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200


def test_bootstrap_admin_only_when_table_empty(monkeypatch):
    monkeypatch.setattr(settings, "admin_email", "boot@temforce.com")
    monkeypatch.setattr(settings, "admin_password", "boot-pass")
    db = SessionLocal()
    try:
        auth.bootstrap_admin(db)
        assert auth.user_count(db) == 1
        # Second boot is a no-op even with different env creds.
        monkeypatch.setattr(settings, "admin_email", "other@temforce.com")
        auth.bootstrap_admin(db)
        assert auth.user_count(db) == 1
        user = db.query(User).one()
        assert user.email == "boot@temforce.com"
        assert user.role == "admin"
        assert user.is_active is True
        assert user.password_hash != "boot-pass"  # hashed, never plaintext
        assert auth.verify_password("boot-pass", user.password_hash)
    finally:
        db.query(AuthSession).delete()
        db.query(User).delete()
        db.commit()
        db.close()


def test_inactive_user_cannot_login(user_db):
    user = user_db.query(User).one()
    user.is_active = False
    user_db.commit()
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@temforce.com", "password": "pilot-pass-1"},
        )
        assert res.status_code == 401


def _login_admin(client: TestClient) -> None:
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@temforce.com", "password": "pilot-pass-1"},
    )
    assert res.status_code == 200


def test_admin_can_create_update_and_deactivate_users(user_db):
    with TestClient(app) as client:
        _login_admin(client)
        created = client.post(
            "/api/v1/users",
            json={
                "email": "operator@temforce.com",
                "username": "Operator",
                "password": "operator-pass-1",
                "role": "user",
            },
        )
        assert created.status_code == 201
        user_id = created.json()["id"]
        assert created.json()["is_active"] is True

        updated = client.patch(
            f"/api/v1/users/{user_id}",
            json={"username": "Ops", "role": "admin"},
        )
        assert updated.status_code == 200
        assert updated.json()["username"] == "Ops"
        assert updated.json()["role"] == "admin"

        deleted = client.delete(f"/api/v1/users/{user_id}")
        assert deleted.status_code == 200
        assert deleted.json()["is_active"] is False
        assert deleted.json()["deleted_at"] is not None


def test_non_admin_cannot_manage_users(user_db):
    user_db.add(
        User(
            email="operator@temforce.com",
            username="Operator",
            password_hash=auth.hash_password("operator-pass-1"),
            role="user",
            is_active=True,
        )
    )
    user_db.commit()
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/auth/login",
            json={"email": "operator@temforce.com", "password": "operator-pass-1"},
        )
        assert res.status_code == 200
        assert client.get("/api/v1/users").status_code == 403


def test_cannot_deactivate_last_admin(user_db):
    admin = user_db.query(User).one()
    with TestClient(app) as client:
        _login_admin(client)
        res = client.delete(f"/api/v1/users/{admin.id}")
        assert res.status_code == 409


def test_session_expiry_rejected(user_db):
    from datetime import datetime, timedelta

    user = user_db.query(User).one()
    token, _ = auth.create_session(user_db, user.id)
    row = user_db.query(AuthSession).one()
    row.expires_at = datetime.now() - timedelta(minutes=1)
    user_db.commit()
    assert auth.validate_session(user_db, token) is None


def test_change_password_self_service(user_db):
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"email": "admin@temforce.com", "password": "pilot-pass-1"})

        # Wrong current password is rejected.
        bad = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "wrong", "new_password": "new-pass-123"},
        )
        assert bad.status_code == 401

        # Too-short new password is rejected.
        short = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "pilot-pass-1", "new_password": "short"},
        )
        assert short.status_code == 422

        # A second session (another device) exists before the change.
        user = user_db.query(User).one()
        other_token, _ = auth.create_session(user_db, user.id)

        ok = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "pilot-pass-1", "new_password": "new-pass-123"},
        )
        assert ok.status_code == 200

        # Current session still valid; the other device's session is revoked.
        assert client.get("/api/v1/auth/me").status_code == 200
        user_db.expire_all()
        assert auth.validate_session(user_db, other_token) is None

        # Old password no longer works; new one does.
        client.post("/api/v1/auth/logout")
        assert client.post(
            "/api/v1/auth/login",
            json={"email": "admin@temforce.com", "password": "pilot-pass-1"},
        ).status_code == 401
        assert client.post(
            "/api/v1/auth/login",
            json={"email": "admin@temforce.com", "password": "new-pass-123"},
        ).status_code == 200
