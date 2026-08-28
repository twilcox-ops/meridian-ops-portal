"""Shared pytest fixtures.

viewer_user / approver_user: transient User rows (never persisted — no
database is touched) for testing role enforcement. auth/roles.py's
require_role() only reads `.role` off whatever get_current_user() resolves
to, so tests fake the session by overriding that dependency with one of
these rather than exercising a real Entra ID login.

TODO: an in-memory/temp-file SQLite session fixture and a FastAPI
TestClient fixture wired to it, once routers exist to test end-to-end.
"""
from __future__ import annotations

import pytest

from ops_portal.db.models import User, UserRole


@pytest.fixture()
def viewer_user() -> User:
    return User(
        id=1,
        entra_object_id="oid-viewer",
        email="viewer@example.com",
        display_name="Viewer",
        role=UserRole.VIEWER,
    )


@pytest.fixture()
def approver_user() -> User:
    return User(
        id=2,
        entra_object_id="oid-approver",
        email="approver@example.com",
        display_name="Approver",
        role=UserRole.APPROVER,
    )
