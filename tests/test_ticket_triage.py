"""GET /ticket-triage and POST /ticket-triage/{ticket_id}/override.

Signing in reuses the real /login -> /auth/callback round trip (msal_client
mocked, everything else real — see test_auth_routes.py).
"""
from __future__ import annotations

from ops_portal.auth import msal_client
from ops_portal.db.models import AuditLog, TicketClassification, User

FAKE_AUTHORIZE_URL = "https://login.microsoftonline.com/fake-tenant/oauth2/v2.0/authorize"


def _fake_token_result(oid, email, roles):
    return {"id_token_claims": {"oid": oid, "email": email, "name": email, "roles": roles}}


def _sign_in(client, monkeypatch, *, oid: str = "oid-viewer", email: str = "viewer@example.com", roles=None) -> None:
    monkeypatch.setattr(msal_client, "build_auth_url", lambda state=None: f"{FAKE_AUTHORIZE_URL}?state={state}")
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.headers["location"].rsplit("state=", 1)[-1]
    monkeypatch.setattr(
        msal_client, "acquire_token_by_auth_code", lambda code: _fake_token_result(oid, email, roles or ["viewer"])
    )
    callback_response = client.get(f"/auth/callback?code=fake-code&state={state}", follow_redirects=False)
    assert callback_response.status_code == 302


def _make_ticket(db_session, ticket_id="TKT-5148") -> TicketClassification:
    ticket = TicketClassification(
        ticket_id=ticket_id,
        ticket_text="Please confirm the re-inspection due date.",
        predicted_category="compliance",
        predicted_urgency="medium",
        confidence=0.83,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def test_signed_out_get_is_rejected(client):
    response = client.get("/ticket-triage", follow_redirects=False)

    assert response.status_code == 401


def test_signed_in_user_sees_the_list(client, monkeypatch, db_session):
    _make_ticket(db_session)
    _sign_in(client, monkeypatch)

    response = client.get("/ticket-triage")

    assert response.status_code == 200
    assert "TKT-5148" in response.text
    assert "compliance" in response.text
    assert "medium" in response.text


def test_override_updates_the_row_and_logs_audit_with_correct_actor(client, monkeypatch, db_session):
    _make_ticket(db_session)
    _sign_in(client, monkeypatch, oid="oid-viewer", email="viewer@example.com", roles=["viewer"])

    response = client.post(
        "/ticket-triage/TKT-5148/override",
        data={"override_category": "outage", "override_urgency": "critical"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ticket-triage"

    viewer = db_session.query(User).filter_by(email="viewer@example.com").one()
    ticket = db_session.query(TicketClassification).filter_by(ticket_id="TKT-5148").one()
    assert ticket.override_category == "outage"
    assert ticket.override_urgency == "critical"
    assert ticket.overridden_by_id == viewer.id
    assert ticket.overridden_at is not None

    entries = db_session.query(AuditLog).filter(AuditLog.action == "ticket_classification.overridden").all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.entity_type == "ticket_classification"
    assert entry.entity_id == "TKT-5148"
    assert entry.actor_id == viewer.id
    assert entry.before == {"override_category": None, "override_urgency": None}
    assert entry.after == {"override_category": "outage", "override_urgency": "critical"}


def test_overriding_a_nonexistent_ticket_returns_404(client, monkeypatch, db_session):
    _sign_in(client, monkeypatch)

    response = client.post(
        "/ticket-triage/TKT-DOES-NOT-EXIST/override",
        data={"override_category": "outage", "override_urgency": "critical"},
        follow_redirects=False,
    )

    assert response.status_code == 404
