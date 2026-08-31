"""GET /review-queue and its three POST actions (request/approve/reject).

Signing in reuses the real /login -> /auth/callback round trip (msal_client
mocked, everything else real — see test_auth_routes.py), so approve/reject
role enforcement and self-approval blocking are exercised through the
actual HTTP layer, not by calling services/approvals.py directly.
"""
from __future__ import annotations

from ops_portal.auth import msal_client
from ops_portal.db.models import Approval, ApprovalStatus, ReviewItem, ReviewStatus, User

FAKE_AUTHORIZE_URL = "https://login.microsoftonline.com/fake-tenant/oauth2/v2.0/authorize"


def _fake_token_result(oid, email, roles):
    return {"id_token_claims": {"oid": oid, "email": email, "name": email, "roles": roles}}


def _sign_in(client, monkeypatch, *, oid: str, email: str, roles: list[str]) -> None:
    monkeypatch.setattr(msal_client, "build_auth_url", lambda state=None: f"{FAKE_AUTHORIZE_URL}?state={state}")
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.headers["location"].rsplit("state=", 1)[-1]
    monkeypatch.setattr(msal_client, "acquire_token_by_auth_code", lambda code: _fake_token_result(oid, email, roles))
    callback_response = client.get(f"/auth/callback?code=fake-code&state={state}", follow_redirects=False)
    assert callback_response.status_code == 302


def _make_review_item(db_session) -> ReviewItem:
    item = ReviewItem(
        source_document="MES-2026-4100.pdf",
        field_name="invoice_total",
        extracted_value="1766.82",
        confidence=0.42,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def test_signed_out_get_is_rejected(client):
    response = client.get("/review-queue", follow_redirects=False)

    assert response.status_code == 401


def test_viewer_can_view_and_request_a_correction(client, monkeypatch, db_session):
    review_item = _make_review_item(db_session)
    _sign_in(client, monkeypatch, oid="oid-viewer", email="viewer@example.com", roles=["viewer"])

    get_response = client.get("/review-queue")
    assert get_response.status_code == 200
    assert "MES-2026-4100.pdf" in get_response.text

    post_response = client.post(
        f"/review-queue/{review_item.id}/request",
        data={"proposed_value": "1776.82"},
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/review-queue"

    viewer = db_session.query(User).filter_by(email="viewer@example.com").one()
    approval = db_session.query(Approval).filter_by(review_item_id=review_item.id).one()
    assert approval.status == ApprovalStatus.PENDING
    assert approval.requested_by_id == viewer.id
    assert approval.proposed_value == "1776.82"


def test_a_second_pending_request_gets_409(client, monkeypatch, db_session):
    review_item = _make_review_item(db_session)
    _sign_in(client, monkeypatch, oid="oid-viewer", email="viewer@example.com", roles=["viewer"])

    first = client.post(f"/review-queue/{review_item.id}/request", data={"proposed_value": "1776.82"})
    assert first.status_code == 200  # followed the 303 redirect back to the (200) GET

    second = client.post(f"/review-queue/{review_item.id}/request", data={"proposed_value": "1786.82"})

    assert second.status_code == 409


def test_viewer_hitting_approve_and_reject_gets_403(client, monkeypatch, db_session):
    review_item = _make_review_item(db_session)
    _sign_in(client, monkeypatch, oid="oid-viewer", email="viewer@example.com", roles=["viewer"])
    client.post(f"/review-queue/{review_item.id}/request", data={"proposed_value": "1776.82"})
    approval = db_session.query(Approval).filter_by(review_item_id=review_item.id).one()

    approve_response = client.post(f"/review-queue/{approval.id}/approve", follow_redirects=False)
    reject_response = client.post(f"/review-queue/{approval.id}/reject", follow_redirects=False)

    assert approve_response.status_code == 403
    assert reject_response.status_code == 403


def test_approver_can_approve_and_the_review_item_updates(client, monkeypatch, db_session):
    review_item = _make_review_item(db_session)

    _sign_in(client, monkeypatch, oid="oid-viewer", email="viewer@example.com", roles=["viewer"])
    client.post(f"/review-queue/{review_item.id}/request", data={"proposed_value": "1776.82"})
    approval = db_session.query(Approval).filter_by(review_item_id=review_item.id).one()

    _sign_in(client, monkeypatch, oid="oid-approver", email="approver@example.com", roles=["approver"])
    response = client.post(f"/review-queue/{approval.id}/approve", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/review-queue"

    db_session.refresh(approval)
    db_session.refresh(review_item)
    approver = db_session.query(User).filter_by(email="approver@example.com").one()
    assert approval.status == ApprovalStatus.APPROVED
    assert approval.approved_by_id == approver.id
    assert review_item.corrected_value == "1776.82"
    assert review_item.status == ReviewStatus.CORRECTED

    get_response = client.get("/review-queue")
    assert "corrected" in get_response.text


def test_self_approval_via_the_endpoint_gets_403(client, monkeypatch, db_session):
    review_item = _make_review_item(db_session)
    # Same identity requests and then tries to approve their own request.
    _sign_in(client, monkeypatch, oid="oid-approver", email="approver@example.com", roles=["approver"])
    client.post(f"/review-queue/{review_item.id}/request", data={"proposed_value": "1776.82"})
    approval = db_session.query(Approval).filter_by(review_item_id=review_item.id).one()

    response = client.post(f"/review-queue/{approval.id}/approve", follow_redirects=False)

    assert response.status_code == 403
    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.PENDING
