"""Append-only audit log writer, called by every mutating action.

log_change() is the only function this module exposes, and the only way
any code in this project should write to audit_log: it wraps a single
AuditLog insert and hands back the inserted row. There is no corresponding
update/delete function anywhere in this module — the append-only guarantee
is enforced at the database level too (see db/models.py's AuditLog
docstring for the trigger/REVOKE enforcement), but not exposing a way to
touch an existing row at the application layer is the first line of
defense, not the only one.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..db.models import AuditLog


def log_change(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    before: Optional[Any] = None,
    after: Optional[Any] = None,
    actor_id: Optional[int] = None,
) -> AuditLog:
    """Records one state change: who (actor_id — None for a system/
    automated action, not a human), what (action, entity_type, entity_id),
    and before/after.

    Flushes, not commits: this lets a caller see the row immediately
    (including its assigned id) within its own session, while leaving
    transaction/commit boundaries to the caller — a shared writer that
    might be called many times in one pipeline run shouldn't be the thing
    deciding when the surrounding unit of work commits.
    """
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
    )
    db.add(entry)
    db.flush()
    return entry
