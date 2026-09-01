"""audit_log is append-only: no UPDATE, no DELETE, enforced at the database
level (see db/models.py's AuditLog docstring for the trigger mechanism).

These tests exercise the real trigger, not a mocked one: the shared
`db_session` fixture (conftest.py) creates a genuine SQLite engine
(`create_engine("sqlite://", ...)`, the same backend
sqlite:///./local.db uses locally and in every other test in this suite)
and calls `Base.metadata.create_all(engine)`, which is exactly what fires
the `after_create` DDL event that installs `audit_log_no_update` /
`audit_log_no_delete` (db/models.py's `_AUDIT_LOG_TRIGGER_SQLITE*`). So a
write attempt here hits the actual trigger SQL, the same statements that
run against a real sqlite:///./local.db file — nothing about the
enforcement path is faked.

Only the SQLite trigger is exercised here, matching the backend this test
suite (and local dev) actually runs against — see db/models.py's AuditLog
docstring for why the Postgres trigger (and the separate, not-yet-built
REVOKE-based role grant) can't be exercised the same way in this suite.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ops_portal.db.models import AuditLog
from ops_portal.services.audit import log_change


def _insert_one_entry(db_session) -> AuditLog:
    """log_change() only flushes (see services/audit.py) — committed here,
    not just flushed, because every test below deliberately rolls back a
    *later*, failed statement, and a rollback undoes the whole open
    transaction, flush included. Without this commit, the insert itself
    would vanish along with the rejected UPDATE/DELETE, and a test
    asserting "the row is unchanged" would really be asserting "there's no
    row at all" — passing for the wrong reason.
    """
    entry = log_change(
        db_session,
        action="review_item.corrected",
        entity_type="review_item",
        entity_id="1",
        before={"status": "pending"},
        after={"status": "corrected"},
        actor_id=None,
    )
    db_session.commit()
    return entry


def test_direct_sql_update_against_audit_log_is_rejected(db_session):
    """A raw `UPDATE audit_log ...` — standing in for "a future admin
    script" or "a stray UPDATE typed by hand" (db/models.py's own framing
    of what the trigger defends against) — must fail, and the row's
    content must be unchanged afterward.
    """
    entry = _insert_one_entry(db_session)
    entry_id = entry.id

    with pytest.raises(IntegrityError, match="append-only"):
        db_session.execute(text("UPDATE audit_log SET action = 'tampered' WHERE id = :id"), {"id": entry_id})

    # The failed statement leaves the session's transaction unusable until
    # rolled back — the same thing a real caller would have to do after
    # catching this.
    db_session.rollback()

    unchanged = db_session.get(AuditLog, entry_id)
    assert unchanged.action == "review_item.corrected"


def test_direct_sql_delete_against_audit_log_is_rejected(db_session):
    """Same as above, for DELETE: the row must still exist afterward."""
    entry = _insert_one_entry(db_session)
    entry_id = entry.id

    with pytest.raises(IntegrityError, match="append-only"):
        db_session.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": entry_id})

    db_session.rollback()

    assert db_session.get(AuditLog, entry_id) is not None
    assert db_session.query(AuditLog).count() == 1


def test_orm_level_update_is_also_rejected_by_the_same_trigger(db_session):
    """Proves the guarantee isn't just "services/audit.py exposes no
    update function" (an application-layer convention that a bug or a new
    code path could bypass): mutating an already-flushed AuditLog row
    through the ORM and flushing again still issues an UPDATE against the
    real table, and the same database trigger rejects it — not a
    convention, an actual constraint the ORM can't route around.
    """
    entry = _insert_one_entry(db_session)
    db_session.expire_all()  # force the next access to re-SELECT, not reuse the flushed identity map state

    entry = db_session.get(AuditLog, entry.id)
    entry.action = "tampered"

    with pytest.raises(IntegrityError, match="append-only"):
        db_session.flush()

    db_session.rollback()


def test_delete_via_orm_session_is_also_rejected_by_the_same_trigger(db_session):
    """Same proof as the ORM UPDATE test above, for `Session.delete()`."""
    entry = _insert_one_entry(db_session)
    entry_id = entry.id

    db_session.delete(entry)
    with pytest.raises(IntegrityError, match="append-only"):
        db_session.flush()

    db_session.rollback()

    assert db_session.get(AuditLog, entry_id) is not None
