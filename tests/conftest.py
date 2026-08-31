"""Shared pytest fixtures.

viewer_user / approver_user: transient User rows (never persisted — no
database is touched) for testing role enforcement. auth/roles.py's
require_role() only reads `.role` off whatever get_current_user() resolves
to, so tests fake the session by overriding that dependency with one of
these rather than exercising a real Entra ID login.

db_session / client: for tests that need to exercise a real router
end-to-end (e.g. routers/auth.py's find-or-create-on-login logic) — an
isolated in-memory SQLite database, and a TestClient wired to it via a
get_db dependency override so tests never touch the real
sqlite:///./local.db.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ops_portal.app import app
from ops_portal.db.base import Base, get_db
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


@pytest.fixture()
def db_session():
    # `sqlite://` = in-memory; StaticPool keeps every connection pointing
    # at the same underlying database (the default pool would hand out a
    # fresh, empty in-memory database per connection) and
    # check_same_thread=False allows the connection to cross the thread
    # boundary TestClient's ASGI transport runs requests on.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
