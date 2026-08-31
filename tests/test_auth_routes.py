"""Route-logic tests for /login, /auth/callback, /logout.

Only the Entra ID token exchange itself is mocked — msal_client.build_
auth_url and .acquire_token_by_auth_code are monkeypatched on the module
object (see routers/auth.py's docstring for why that import style makes
this possible), since those are the two calls that would otherwise talk to
a real tenant. Everything downstream — CSRF state verification, find-or-
create by entra_object_id, role parsing, and writing the session — runs for
real against the isolated in-memory database from conftest.py's `client`
fixture.
"""
from __future__ import annotations

from ops_portal.auth import msal_client
from ops_portal.db.models import User, UserRole

FAKE_AUTHORIZE_URL = "https://login.microsoftonline.com/fake-tenant/oauth2/v2.0/authorize"


def _fake_token_result(oid="oid-123", email="new.user@example.com", name="New User", roles=None):
    return {
        "id_token_claims": {
            "oid": oid,
            "email": email,
            "name": name,
            "roles": roles or [],
        }
    }


def _do_login(client, monkeypatch) -> str:
    """Hits /login and returns the state value it generated, so a test can
    complete the round trip with a matching /auth/callback request."""
    monkeypatch.setattr(msal_client, "build_auth_url", lambda state=None: f"{FAKE_AUTHORIZE_URL}?state={state}")
    response = client.get("/login", follow_redirects=False)
    return response.headers["location"].rsplit("state=", 1)[-1]


def test_login_redirects_to_entra_with_a_random_state(client, monkeypatch):
    seen_states = []
    monkeypatch.setattr(
        msal_client,
        "build_auth_url",
        lambda state=None: seen_states.append(state) or f"{FAKE_AUTHORIZE_URL}?state={state}",
    )

    response = client.get("/login", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith(FAKE_AUTHORIZE_URL)
    assert seen_states and seen_states[0]  # a non-empty state was generated
    # The state is stashed server-side, not just handed back in the URL.
    assert client.cookies.get("session") is not None


def test_callback_creates_and_logs_in_a_new_user(client, monkeypatch, db_session):
    state = _do_login(client, monkeypatch)
    monkeypatch.setattr(
        msal_client, "acquire_token_by_auth_code", lambda code: _fake_token_result(roles=["approver"])
    )

    response = client.get(f"/auth/callback?code=fake-code&state={state}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/"

    user = db_session.query(User).filter_by(entra_object_id="oid-123").one()
    assert user.email == "new.user@example.com"
    assert user.role == UserRole.APPROVER


def test_callback_updates_role_and_email_for_a_returning_user(client, monkeypatch, db_session):
    existing = User(
        entra_object_id="oid-456",
        email="old.address@example.com",
        display_name="Returning User",
        role=UserRole.VIEWER,
    )
    db_session.add(existing)
    db_session.commit()
    existing_id = existing.id

    state = _do_login(client, monkeypatch)
    monkeypatch.setattr(
        msal_client,
        "acquire_token_by_auth_code",
        lambda code: _fake_token_result(oid="oid-456", email="new.address@example.com", roles=["approver"]),
    )

    response = client.get(f"/auth/callback?code=fake-code&state={state}", follow_redirects=False)

    assert response.status_code == 302
    assert db_session.query(User).count() == 1  # no duplicate row created
    updated = db_session.get(User, existing_id)
    assert updated.email == "new.address@example.com"
    assert updated.role == UserRole.APPROVER


def test_callback_rejects_a_state_mismatch(client, monkeypatch, db_session):
    _do_login(client, monkeypatch)
    monkeypatch.setattr(msal_client, "acquire_token_by_auth_code", lambda code: _fake_token_result())

    response = client.get("/auth/callback?code=fake-code&state=not-the-real-state", follow_redirects=False)

    assert response.status_code == 400
    assert db_session.query(User).count() == 0  # the token exchange never happened


def test_callback_rejects_a_missing_state(client, monkeypatch, db_session):
    # No /login call at all, so nothing was ever stashed in the session.
    monkeypatch.setattr(msal_client, "acquire_token_by_auth_code", lambda code: _fake_token_result())

    response = client.get("/auth/callback?code=fake-code&state=anything", follow_redirects=False)

    assert response.status_code == 400
    assert db_session.query(User).count() == 0


def test_logout_clears_the_session(client, monkeypatch):
    state = _do_login(client, monkeypatch)
    monkeypatch.setattr(msal_client, "acquire_token_by_auth_code", lambda code: _fake_token_result())
    client.get(f"/auth/callback?code=fake-code&state={state}", follow_redirects=False)

    response = client.get("/logout", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    # Starlette's SessionMiddleware only emits this expired-cookie
    # Set-Cookie when the session went from non-empty to empty — i.e. the
    # signed-in user really was removed, not just that a redirect happened.
    set_cookie = response.headers.get("set-cookie", "")
    assert "session=null" in set_cookie
    assert "1970" in set_cookie
