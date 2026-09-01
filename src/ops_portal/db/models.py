"""ORM models: users, assets, rejected_rows, review_items,
ticket_classifications, approvals, audit_log, ingestion_runs.

Table-by-table intent:

- users: Entra ID-federated accounts (see auth/msal_client.py) — no
  username/password. `role` carries the "at least two roles" requirement
  (viewer / approver); auth/roles.py's require_role() dependency is what
  turns that into a server-side 403.
- ingestion_runs: a generic run ledger — one row per execution of a batch
  job owned by ops_portal itself (the Part 1 asset-registry normalization
  pipeline, and each integrations/ adapter's periodic pull from Projects
  1/2/4), distinguished by `job_name`. This is separate from Project 1's own
  ingestion history, which integrations/project1_ingestion.py reads live
  from PROJECT1_INGESTION_SOURCE and never copies in.
- assets / rejected_rows: Part 1's output — the deduped canonical registry
  and its rejection report (every dropped row, with a reason, never silent).
- review_items: Project 2's low-confidence extractions, one row per field
  that needs a human look; `approvals` gates the correction on this table
  specifically (see services/approvals.py's documented scope).
- ticket_classifications: Project 4's model output plus a human override.
- audit_log: append-only record of every state change. See the enforcement
  note above the AuditLog class — this is enforced at the database level,
  not just by omitting update/delete code paths in services/audit.py.
"""
from __future__ import annotations

import enum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.event import listen
from sqlalchemy.orm import relationship
from sqlalchemy.schema import DDL

from .base import Base


class UserRole(str, enum.Enum):
    VIEWER = "viewer"
    APPROVER = "approver"


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CORRECTED = "corrected"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# native_enum=False renders these as a plain VARCHAR + CHECK constraint on
# every backend (SQLite included), rather than a Postgres-only native ENUM
# type — one less thing that needs an Alembic migration of its own if a
# value is ever added.
def _enum_column(enum_cls: type[enum.Enum], **kwargs):
    return Column(Enum(enum_cls, native_enum=False, validate_strings=True), **kwargs)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    # The `oid` claim from the Entra ID token — stable for the life of the
    # account, unlike email (which can be renamed in the tenant).
    entra_object_id = Column(String, nullable=False, unique=True, index=True)
    email = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=True)
    role = _enum_column(UserRole, nullable=False, default=UserRole.VIEWER)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    audit_entries = relationship("AuditLog", back_populates="actor")
    reviews_done = relationship("ReviewItem", back_populates="reviewed_by")
    approvals_requested = relationship(
        "Approval", back_populates="requested_by", foreign_keys="Approval.requested_by_id"
    )
    approvals_decided = relationship(
        "Approval", back_populates="approved_by", foreign_keys="Approval.approved_by_id"
    )
    ticket_overrides = relationship("TicketClassification", back_populates="overridden_by")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id = Column(Integer, primary_key=True)
    job_name = Column(String, nullable=False, index=True)
    status = _enum_column(RunStatus, nullable=False, default=RunStatus.RUNNING)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    records_processed = Column(Integer, nullable=False, default=0)
    records_failed = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)

    assets = relationship("Asset", back_populates="ingestion_run")
    rejected_rows = relationship("RejectedRow", back_populates="ingestion_run")


class Asset(Base):
    """The canonical, deduped output of the Part 1 normalization pipeline.

    Columns mirror sample-data/messy-asset-registry.csv after cleaning:
    asset_tag is the natural key that survives entity resolution + the
    documented survivorship rule, so exactly one row exists per asset.
    """

    __tablename__ = "assets"

    id = Column(Integer, primary_key=True)
    ingestion_run_id = Column(ForeignKey("ingestion_runs.id"), nullable=True)
    asset_tag = Column(String, nullable=False, unique=True, index=True)
    building = Column(String, nullable=False, index=True)  # post entity-resolution canonical name
    unit_id = Column(String, nullable=True)
    install_date = Column(Date, nullable=True)
    status = Column(String, nullable=True)
    last_service_cost = Column(Numeric(12, 2), nullable=True)  # parsed incl. "(1,234.56)" negatives
    technician = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    ingestion_run = relationship("IngestionRun", back_populates="assets")


class RejectedRow(Base):
    """A row Part 1 could not or would not admit into `assets` — always kept,
    never silently dropped, with a human-readable reason."""

    __tablename__ = "rejected_rows"

    id = Column(Integer, primary_key=True)
    ingestion_run_id = Column(ForeignKey("ingestion_runs.id"), nullable=True)
    source_row_number = Column(Integer, nullable=True)  # 1-based line in the source CSV
    asset_tag = Column(String, nullable=True)  # may be unparseable, hence nullable
    raw_row = Column(JSON, nullable=False)  # the untouched source fields, for reprocessing
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    ingestion_run = relationship("IngestionRun", back_populates="rejected_rows")


class ReviewItem(Base):
    """One low-confidence field from a Project 2 document extraction.

    A correction on this row goes through Approval, not a direct write —
    see services/approvals.py.
    """

    __tablename__ = "review_items"

    id = Column(Integer, primary_key=True)
    source_document = Column(String, nullable=False, index=True)  # e.g. "MES-2026-4100.pdf"
    field_name = Column(String, nullable=False)  # e.g. "invoice_total"
    extracted_value = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False)
    status = _enum_column(ReviewStatus, nullable=False, default=ReviewStatus.PENDING)
    corrected_value = Column(Text, nullable=True)
    reviewed_by_id = Column(ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    reviewed_by = relationship("User", back_populates="reviews_done")
    approvals = relationship("Approval", back_populates="review_item")


class TicketClassification(Base):
    """A Project 4 model prediction for one support ticket, plus an optional
    human override — both category and urgency, per the eval data shape in
    sample-data/support-tickets-eval.jsonl."""

    __tablename__ = "ticket_classifications"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(String, nullable=False, unique=True, index=True)  # e.g. "TKT-5148"
    ticket_text = Column(Text, nullable=True)
    predicted_category = Column(String, nullable=True)
    predicted_urgency = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    override_category = Column(String, nullable=True)
    override_urgency = Column(String, nullable=True)
    overridden_by_id = Column(ForeignKey("users.id"), nullable=True)
    overridden_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    overridden_by = relationship("User", back_populates="ticket_overrides")


class Approval(Base):
    """Maker-checker workflow. Current scope (per services/approvals.py):
    gates a ReviewItem correction only — requested by one user, approved by
    a different user, executed after approval.

    The CheckConstraint is the database-level half of the "cannot be
    self-approved" requirement: it blocks approved_by_id == requested_by_id
    even if the app-level check in services/approvals.py has a bug, is
    bypassed, or is called from a future second code path. The app-level
    check must still exist and run first, so a self-approval attempt gets a
    clean rejection instead of a raw IntegrityError.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "approved_by_id IS NULL OR approved_by_id != requested_by_id",
            name="ck_approvals_no_self_approval",
        ),
    )

    id = Column(Integer, primary_key=True)
    review_item_id = Column(ForeignKey("review_items.id"), nullable=False)
    proposed_value = Column(Text, nullable=False)
    status = _enum_column(ApprovalStatus, nullable=False, default=ApprovalStatus.PENDING)
    requested_by_id = Column(ForeignKey("users.id"), nullable=False)
    requested_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    approved_by_id = Column(ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    review_item = relationship("ReviewItem", back_populates="approvals")
    requested_by = relationship("User", back_populates="approvals_requested", foreign_keys=[requested_by_id])
    approved_by = relationship("User", back_populates="approvals_decided", foreign_keys=[approved_by_id])


class AuditLog(Base):
    """Every state change: who, what, when, before, after. Immutable —
    append-only, no updates or deletes.

    Enforced at the database level, today, by one real mechanism: right
    here, at table-creation time, a BEFORE UPDATE/DELETE trigger is
    attached below via SQLAlchemy DDL events (`after_create`), one
    dialect-specific statement for SQLite and one for Postgres. It fires
    for any writer on any connection — the app's own ORM session, a
    future admin script, a stray `UPDATE audit_log ...` typed by hand —
    and raises before the write can complete. This is what makes the
    guarantee real in both the sqlite:///./local.db dev database and a
    Postgres instance that hasn't had its grants touched yet. See
    tests/test_audit_log.py, which exercises this trigger directly (raw
    SQL and ORM-level UPDATE/DELETE, both against a real SQLite engine —
    not a mocked one) rather than only asserting the convention below.

    services/audit.py additionally never exposes an update/delete function
    at the application layer at all — belt and suspenders, not belt alone.

    NOT YET IMPLEMENTED: a second, independent database-level layer,
    `REVOKE UPDATE, DELETE ON audit_log FROM <app_role>;` (granting that
    role only SELECT and INSERT) in the deployed Postgres database, once
    the app's own least-privilege DB role is provisioned in deploy/. This
    would matter because a trigger can in principle be dropped by anyone
    with DDL rights on the table, whereas a role that was never granted
    UPDATE/DELETE can't exercise a privilege it doesn't have — but no such
    role exists yet (deploy/main.bicep provisions the Postgres server and
    database, not an application-level role or its grants), so this layer
    is aspirational, not active. Today the trigger above is the only
    database-level enforcement in place.
    """

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    actor_id = Column(ForeignKey("users.id"), nullable=True)  # null for automated/system actions
    action = Column(String, nullable=False)  # e.g. "review_item.corrected", "approval.approved"
    entity_type = Column(String, nullable=False)  # e.g. "review_item"
    entity_id = Column(String, nullable=True)  # stored as text to stay entity-agnostic
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    actor = relationship("User", back_populates="audit_entries")


_AUDIT_LOG_TRIGGER_SQLITE = DDL(
    """
    CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE is not permitted');
    END;
    """
)

_AUDIT_LOG_TRIGGER_SQLITE_DELETE = DDL(
    """
    CREATE TRIGGER audit_log_no_delete
    BEFORE DELETE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: DELETE is not permitted');
    END;
    """
)

_AUDIT_LOG_TRIGGER_POSTGRESQL = DDL(
    """
    CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_log is append-only: % is not permitted', TG_OP;
    END;
    $$ LANGUAGE plpgsql;

    CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
    """
)

listen(
    AuditLog.__table__,
    "after_create",
    _AUDIT_LOG_TRIGGER_SQLITE.execute_if(dialect="sqlite"),
)
listen(
    AuditLog.__table__,
    "after_create",
    _AUDIT_LOG_TRIGGER_SQLITE_DELETE.execute_if(dialect="sqlite"),
)
listen(
    AuditLog.__table__,
    "after_create",
    _AUDIT_LOG_TRIGGER_POSTGRESQL.execute_if(dialect="postgresql"),
)
