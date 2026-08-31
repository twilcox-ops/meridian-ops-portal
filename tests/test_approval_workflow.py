"""requester != approver enforced server-side; self-approval is rejected;
the approved action executes and is logged.

Uses the shared `db_session` fixture from conftest.py directly (not the
transient viewer_user/approver_user fixtures, which are never persisted —
Approval.requested_by_id/approved_by_id are real foreign keys, so these
tests need committed User rows with real ids).
"""
from __future__ import annotations

import pytest

from ops_portal.db.models import AuditLog, ApprovalStatus, ReviewItem, ReviewStatus, User, UserRole
from ops_portal.services.approvals import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    DuplicatePendingApprovalError,
    ReviewItemNotFoundError,
    SelfApprovalError,
    approve,
    reject,
    request_correction,
)


@pytest.fixture()
def requester(db_session) -> User:
    user = User(entra_object_id="oid-requester", email="requester@example.com", role=UserRole.VIEWER)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def approver(db_session) -> User:
    user = User(entra_object_id="oid-approver", email="approver@example.com", role=UserRole.APPROVER)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def review_item(db_session) -> ReviewItem:
    item = ReviewItem(
        source_document="MES-2026-4100.pdf",
        field_name="invoice_total",
        extracted_value="1766.82",
        confidence=0.42,
    )
    db_session.add(item)
    db_session.flush()
    return item


def test_request_then_approve_updates_review_item_and_logs_audit(db_session, requester, approver, review_item):
    approval = request_correction(db_session, review_item.id, requester.id, "1776.82")
    assert approval.status == ApprovalStatus.PENDING

    result = approve(db_session, approval.id, approver.id)

    assert result.status == ApprovalStatus.APPROVED
    assert result.approved_by_id == approver.id
    assert result.decided_at is not None

    db_session.refresh(review_item)
    assert review_item.corrected_value == "1776.82"
    assert review_item.status == ReviewStatus.CORRECTED

    entries = db_session.query(AuditLog).filter(AuditLog.action == "approval.approved").all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.entity_type == "review_item"
    assert entry.entity_id == str(review_item.id)
    assert entry.actor_id == approver.id
    assert entry.before == {"corrected_value": None, "status": "pending"}
    assert entry.after == {"corrected_value": "1776.82", "status": "corrected"}


def test_self_approval_is_rejected_and_changes_nothing(db_session, requester, review_item):
    approval = request_correction(db_session, review_item.id, requester.id, "1776.82")

    with pytest.raises(SelfApprovalError):
        approve(db_session, approval.id, requester.id)

    db_session.refresh(approval)
    db_session.refresh(review_item)
    assert approval.status == ApprovalStatus.PENDING
    assert approval.approved_by_id is None
    assert review_item.corrected_value is None
    assert review_item.status == ReviewStatus.PENDING
    assert db_session.query(AuditLog).count() == 0


def test_self_rejection_is_also_rejected(db_session, requester, review_item):
    approval = request_correction(db_session, review_item.id, requester.id, "1776.82")

    with pytest.raises(SelfApprovalError):
        reject(db_session, approval.id, requester.id)

    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.PENDING
    assert db_session.query(AuditLog).count() == 0


def test_a_second_pending_request_on_the_same_item_is_rejected(db_session, requester, review_item):
    request_correction(db_session, review_item.id, requester.id, "1776.82")

    with pytest.raises(DuplicatePendingApprovalError):
        request_correction(db_session, review_item.id, requester.id, "1786.82")


def test_reject_marks_the_approval_rejected_and_leaves_the_review_item_untouched(
    db_session, requester, approver, review_item
):
    approval = request_correction(db_session, review_item.id, requester.id, "1776.82")

    result = reject(db_session, approval.id, approver.id, notes="not confident in this correction")

    assert result.status == ApprovalStatus.REJECTED
    assert result.approved_by_id == approver.id
    assert result.notes == "not confident in this correction"

    db_session.refresh(review_item)
    assert review_item.corrected_value is None
    assert review_item.status == ReviewStatus.PENDING

    entries = db_session.query(AuditLog).filter(AuditLog.action == "approval.rejected").all()
    assert len(entries) == 1
    assert entries[0].actor_id == approver.id


def test_requesting_a_correction_on_a_missing_review_item_raises(db_session, requester):
    with pytest.raises(ReviewItemNotFoundError):
        request_correction(db_session, 999999, requester.id, "x")


def test_approving_a_nonexistent_approval_raises(db_session, approver):
    with pytest.raises(ApprovalNotFoundError):
        approve(db_session, 999999, approver.id)


def test_rejecting_a_nonexistent_approval_raises(db_session, approver):
    with pytest.raises(ApprovalNotFoundError):
        reject(db_session, 999999, approver.id)


def test_approving_an_already_decided_approval_raises(db_session, requester, approver, review_item):
    approval = request_correction(db_session, review_item.id, requester.id, "1776.82")
    approve(db_session, approval.id, approver.id)

    with pytest.raises(ApprovalAlreadyDecidedError):
        approve(db_session, approval.id, approver.id)
