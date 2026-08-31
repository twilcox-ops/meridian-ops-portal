"""/login, /auth/callback, /logout.

A signed-out user reaches nothing beyond these three routes and whatever a
`require_role`-gated route rejects them from (see auth/roles.py) — there is
no username/password path, only the Entra ID auth-code flow.

msal_client is imported as a module, not `from ..auth.msal_client import
...`, so tests can monkeypatch `msal_client.build_auth_url` /
`.acquire_token_by_auth_code` on the module object and have this file's
calls pick the replacement up — real Entra ID is never contacted in tests.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import msal_client
from ..auth.roles import parse_roles
from ..auth.session import login_user, logout_user
from ..db.base import get_db
from ..db.models import User

router = APIRouter()

# Session key for the CSRF state value, distinct from session.py's "user"
# key. Never present at the same time as "user" in normal use — it's
# written by /login and always consumed (popped) by /auth/callback,
# success or failure.
_STATE_SESSION_KEY = "oauth_state"


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    """Redirects to Entra ID's login page. A random, single-use `state`
    value is stashed in the session and re-checked in /auth/callback —
    without it, an attacker could get a victim's browser to complete an
    auth-code flow the attacker initiated (login CSRF)."""
    state = secrets.token_urlsafe(32)
    request.session[_STATE_SESSION_KEY] = state
    return RedirectResponse(msal_client.build_auth_url(state=state))


@router.get("/auth/callback")
def auth_callback(request: Request, code: str, state: str, db: Session = Depends(get_db)) -> RedirectResponse:
    expected_state = request.session.pop(_STATE_SESSION_KEY, None)
    if not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="invalid or expired login attempt")

    token_result = msal_client.acquire_token_by_auth_code(code)
    claims = token_result.get("id_token_claims", {})

    # `oid` is the Entra ID object id — stable for the life of the account,
    # which is what makes it safe to use as the lookup key below (unlike
    # email, which can be renamed in the tenant).
    entra_object_id = claims.get("oid")
    if not entra_object_id:
        raise HTTPException(status_code=400, detail="ID token missing 'oid' claim")

    role = parse_roles(claims)
    email = claims.get("email") or claims.get("preferred_username") or ""
    display_name = claims.get("name")

    user = db.query(User).filter_by(entra_object_id=entra_object_id).first()
    if user is None:
        user = User(entra_object_id=entra_object_id, email=email, display_name=display_name, role=role)
        db.add(user)
    else:
        # Keep the row in sync with the tenant on every login, so a role
        # change or rename in Entra ID takes effect the next time this
        # person signs in rather than waiting on a separate sync job.
        user.email = email
        user.display_name = display_name
        user.role = role
    db.commit()
    db.refresh(user)

    login_user(request, user)
    return RedirectResponse("/", status_code=302)


@router.get("/logout")
def logout(request: Request) -> RedirectResponse:
    logout_user(request)
    return RedirectResponse("/login", status_code=302)
