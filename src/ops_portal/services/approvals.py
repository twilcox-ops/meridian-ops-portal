"""Maker-checker approval workflow.

Scope (decided): this gates Project 2 review-queue corrections
specifically — requested by one user, approved by a different user,
executed after approval, fully logged via services/audit.py.

Three operations, each raising a specific, importable exception (never a
bare Exception/ValueError) on failure, so a future router can catch each
one and map it to the right HTTP status without string-matching an error
message:

- request_correction(): creates a PENDING Approval for a ReviewItem.
- approve(): the server-side "requester != approver" check (matching
  db/models.py's Approval.__table_args__ CheckConstraint, which is the
  database's own backstop for the same rule — this is what runs first and
  fails cleanly instead of surfacing a raw IntegrityError), then applies
  the proposed correction to the ReviewItem and logs the change.
- reject(): the same self-approval check, but never touches the
  ReviewItem — a rejected proposal isn't applied.

Every function here flushes the session, never commits — consistent with
services/audit.py's log_change() and normalization/{entity_resolution,
dedupe}.py, transaction/commit boundaries belong to the caller (the
not-yet-built router), not to a service function that might be one step
of a larger request.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import Approval, ApprovalStatus, ReviewItem, ReviewStatus
from .audit import log_change


class ApprovalError(Exception):
    """Base class for every error this module raises."""


class ReviewItemNotFoundError(ApprovalError):
    """No ReviewItem exists with the given id."""


class DuplicatePendingApprovalError(ApprovalError):
    """The target ReviewItem already has a PENDING Approval outstanding."""


class ApprovalNotFoundError(ApprovalError):
    """No Approval exists with the given id."""


class ApprovalAlreadyDecidedError(ApprovalError):
    """The Approval is no longer PENDING — approve()/reject() only apply
    to a decision that hasn't been made yet. Not explicitly called for in
    the original scope, but left unguarded this would let a second
    approve() re-apply (or a reject() overturn) an already-decided
    approval, silently corrupting both the audit trail and the
    ReviewItem's state."""


class SelfApprovalError(ApprovalError):
    """approved_by_id == requested_by_id — the application-level half of
    the "cannot be self-approved" requirement, enforced here before the
    database's CheckConstraint ever gets a chance to reject the write."""


def request_correction(db: Session, review_item_id: int, requested_by_id: int, proposed_value: str) -> Approval:
    """Creates a new PENDING Approval proposing `proposed_value` as the
    correction for ReviewItem `review_item_id`, requested by
    `requested_by_id`.

    Raises ReviewItemNotFoundError if the review item doesn't exist, or
    DuplicatePendingApprovalError if it already has a PENDING approval —
    one outstanding request per item at a time, so a second reviewer can't
    file a competing proposal while the first is still awaiting a
    decision.
    """
    review_item = db.get(ReviewItem, review_item_id)
    if review_item is None:
        raise ReviewItemNotFoundError(f"no review_item with id={review_item_id}")

    existing_pending = (
        db.query(Approval)
        .filter(Approval.review_item_id == review_item_id, Approval.status == ApprovalStatus.PENDING)
        .first()
    )
    if existing_pending is not None:
        raise DuplicatePendingApprovalError(
            f"review_item {review_item_id} already has a pending approval (approval id={existing_pending.id})"
        )

    approval = Approval(
        review_item_id=review_item_id,
        proposed_value=proposed_value,
        requested_by_id=requested_by_id,
        status=ApprovalStatus.PENDING,
    )
    db.add(approval)
    db.flush()
    return approval


def _load_pending_approval_for_decision(db: Session, approval_id: int, approved_by_id: int) -> Approval:
    """Shared precondition checks for approve() and reject(): the approval
    must exist, must still be PENDING, and the decider must not be the
    original requester. Returns the loaded Approval once all three hold.
    """
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise ApprovalNotFoundError(f"no approval with id={approval_id}")

    if approval.status != ApprovalStatus.PENDING:
        raise ApprovalAlreadyDecidedError(
            f"approval {approval_id} has already been decided (status={approval.status.value})"
        )

    if approved_by_id == approval.requested_by_id:
        raise SelfApprovalError(
            f"user {approved_by_id} requested approval {approval_id} and cannot also decide it"
        )

    return approval


def approve(db: Session, approval_id: int, approved_by_id: int) -> Approval:
    """Approves `approval_id`: applies its proposed_value to the
    ReviewItem's corrected_value, sets the ReviewItem's status to
    CORRECTED, marks the Approval APPROVED, and logs the ReviewItem's
    before/after state to audit_log (actor_id=approved_by_id — a human
    decision, unlike the None/system actor entity_resolution.py and
    dedupe.py log with).

    Raises ApprovalNotFoundError, ApprovalAlreadyDecidedError, or
    SelfApprovalError — see _load_pending_approval_for_decision().
    """
    approval = _load_pending_approval_for_decision(db, approval_id, approved_by_id)

    review_item = db.get(ReviewItem, approval.review_item_id)
    if review_item is None:
        # Shouldn't happen given the foreign key, but don't silently
        # proceed as if the correction applied when it didn't.
        raise ReviewItemNotFoundError(
            f"approval {approval_id} references review_item {approval.review_item_id}, which no longer exists"
        )

    before = {"corrected_value": review_item.corrected_value, "status": review_item.status.value}

    approval.status = ApprovalStatus.APPROVED
    approval.approved_by_id = approved_by_id
    approval.decided_at = datetime.now(timezone.utc)

    review_item.corrected_value = approval.proposed_value
    review_item.status = ReviewStatus.CORRECTED

    after = {"corrected_value": review_item.corrected_value, "status": review_item.status.value}

    log_change(
        db,
        action="approval.approved",
        entity_type="review_item",
        entity_id=str(review_item.id),
        before=before,
        after=after,
        actor_id=approved_by_id,
    )

    return approval


def reject(db: Session, approval_id: int, approved_by_id: int, notes: Optional[str] = None) -> Approval:
    """Rejects `approval_id`: marks the Approval REJECTED with `notes`,
    and does not touch the ReviewItem at all — a rejected proposal was
    never applied, so there's no ReviewItem state to roll back.

    The audit entry logs the Approval's own before/after (pending ->
    rejected, plus the notes) rather than a ReviewItem value change, since
    that's the only state that actually changed here; entity_type stays
    "review_item" to match approve()'s convention (both are decisions
    about the same review item), with entity_id pointing at that item.

    Raises the same three exceptions as approve() for the same reasons.
    """
    approval = _load_pending_approval_for_decision(db, approval_id, approved_by_id)

    before = {"approval_status": ApprovalStatus.PENDING.value, "proposed_value": approval.proposed_value}

    approval.status = ApprovalStatus.REJECTED
    approval.approved_by_id = approved_by_id
    approval.decided_at = datetime.now(timezone.utc)
    approval.notes = notes

    after = {"approval_status": ApprovalStatus.REJECTED.value, "notes": approval.notes}

    log_change(
        db,
        action="approval.rejected",
        entity_type="review_item",
        entity_id=str(approval.review_item_id),
        before=before,
        after=after,
        actor_id=approved_by_id,
    )

    return approval
