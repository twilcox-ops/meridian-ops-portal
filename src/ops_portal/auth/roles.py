"""Role-claim parsing and role-enforcement dependency.

parse_roles() turns the `roles` claim from an Entra ID ID token (an app
role assignment array, e.g. ["approver"]) into the UserRole enum already
defined in db/models.py, at login time — the not-yet-built auth router
calls this once per sign-in and hands the result to auth/session.py's
login_user().

require_role() is the server-side gate every role-restricted route depends
on. Enforcing it here, not in a template, is what makes "a viewer calling
the approver's endpoint gets a 403" true regardless of whether the UI shows
or hides the button for that action.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException

from ..db.models import User, UserRole
from .session import get_current_user


def parse_roles(id_token_claims: dict) -> UserRole:
    """The tenant's Entra ID app roles are named to match UserRole's values
    exactly ("viewer", "approver"), configured in the app registration's
    manifest. A user can in principle hold more than one app role; the
    first claimed value that matches a known UserRole wins. Anyone with no
    recognized app role gets the least-privileged default rather than
    failing login outright.
    """
    for claim in id_token_claims.get("roles", []):
        try:
            return UserRole(claim)
        except ValueError:
            continue
    return UserRole.VIEWER


def require_role(role: UserRole):
    """FastAPI dependency factory: `Depends(require_role(UserRole.APPROVER))`.

    Raises 403 — never a redirect, never something a hidden UI element
    could stand in for — if no one is signed in, or the signed-in user's
    role doesn't match the one required.
    """

    def _require_role(user: Optional[User] = Depends(get_current_user)) -> User:
        if user is None or user.role != role:
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    return _require_role
