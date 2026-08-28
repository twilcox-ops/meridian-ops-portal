"""Server-side session helpers.

Session storage is Starlette's SessionMiddleware (wired in app.py using
SESSION_SECRET_KEY), which signs a cookie holding request.session, a plain
dict — the browser can read it but not forge or tamper with it undetected.
login_user() writes only id/email/role into that dict, never a password or
token, and every request has get_current_user() read the id back and
re-load the User row fresh from the database, so a role change or
deactivation takes effect on the very next request rather than waiting for
the session to expire.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..db.base import get_db
from ..db.models import User

_SESSION_KEY = "user"


def login_user(request: Request, user: User) -> None:
    """Call once, right after a successful Entra ID auth-code exchange
    (see auth/msal_client.py) has been resolved to a known User row."""
    request.session[_SESSION_KEY] = {
        "id": user.id,
        "email": user.email,
        "role": user.role.value,
    }


def logout_user(request: Request) -> None:
    request.session.pop(_SESSION_KEY, None)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """FastAPI dependency. Returns None if no one is signed in.

    Returning None rather than raising means this alone is not an
    access-control check — routes that must reject a signed-out caller do
    so explicitly, either via require_role() (auth/roles.py) for a
    role-gated route, or `if user is None: raise HTTPException(...)` for a
    route that only needs "signed in," not a specific role.
    """
    session_user = request.session.get(_SESSION_KEY)
    if session_user is None:
        return None
    return db.get(User, session_user["id"])
