"""Signed-out user reaches nothing; a viewer calling the approver's
endpoint directly gets a 403 (server-side, not a hidden button).

No real Entra ID login is exercised here (and no database, in turn —
viewer_user/approver_user from conftest.py are transient rows). Instead the
session is faked the standard FastAPI way: get_current_user is the single
seam every protected route depends on (either directly, or through
require_role(), which itself depends on it), so overriding that one
dependency is equivalent to "signed in as this user" or "signed out"
without needing a real signed session cookie.

The two routes below stand in for the not-yet-built protected routers
(dashboard, review_queue, ...): both will follow this exact same pattern,
so this is a test of the reusable auth dependencies themselves.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from ops_portal.auth.roles import require_role
from ops_portal.auth.session import get_current_user
from ops_portal.db.models import UserRole


def _build_test_app() -> FastAPI:
    test_app = FastAPI()

    @test_app.get("/protected")
    def protected_route(user=Depends(get_current_user)):
        # This is the pattern a route needs when it only requires "signed
        # in," not a specific role — get_current_user() itself never raises.
        if user is None:
            raise HTTPException(status_code=401, detail="not signed in")
        return {"email": user.email}

    @test_app.get("/approver-only")
    def approver_only_route(user=Depends(require_role(UserRole.APPROVER))):
        return {"email": user.email}

    return test_app


def test_signed_out_user_gets_401_on_a_signed_in_only_route():
    test_app = _build_test_app()
    test_app.dependency_overrides[get_current_user] = lambda: None
    client = TestClient(test_app)

    response = client.get("/protected")

    assert response.status_code == 401


def test_signed_out_user_gets_403_on_approver_only_route():
    test_app = _build_test_app()
    test_app.dependency_overrides[get_current_user] = lambda: None
    client = TestClient(test_app)

    response = client.get("/approver-only")

    assert response.status_code == 403


def test_viewer_calling_approver_only_route_gets_403(viewer_user):
    test_app = _build_test_app()
    test_app.dependency_overrides[get_current_user] = lambda: viewer_user
    client = TestClient(test_app)

    response = client.get("/approver-only")

    assert response.status_code == 403


def test_approver_calling_approver_only_route_succeeds(approver_user):
    test_app = _build_test_app()
    test_app.dependency_overrides[get_current_user] = lambda: approver_user
    client = TestClient(test_app)

    response = client.get("/approver-only")

    assert response.status_code == 200
    assert response.json() == {"email": approver_user.email}
