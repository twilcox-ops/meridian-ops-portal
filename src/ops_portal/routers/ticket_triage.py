"""Ticket triage screen: Project 4 classifications (mirrored into this
project's own ticket_classifications table — see
integrations/project4_triage.py for why), the model's confidence, and a
human override.

GET /ticket-triage — any signed-in user, viewer or approver; same
"signed in or 401" pattern as the other screens.

POST .../override — any signed-in user may submit a human override.
Unlike routers/review_queue.py's correction flow, this is NOT gated
behind maker-checker approval — per the spec ("Ticket triage: ... with the
model's confidence, and a human override"), an override here is a direct,
single-step action, not a proposal a second person has to approve. It's
still a real state change, so it's still logged to audit_log via
services/audit.py's log_change() — "not gated by approval" and "not
audited" are two different things, and only the first is true here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth.session import get_current_user
from ..db.base import get_db
from ..db.models import TicketClassification, User
from ..services.audit import log_change

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/ticket-triage")
def ticket_triage(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401, detail="not signed in")

    tickets = db.query(TicketClassification).order_by(TicketClassification.id).all()
    return templates.TemplateResponse(request, "ticket_triage.html", {"user": user, "tickets": tickets})


@router.post("/ticket-triage/{ticket_id}/override")
def override_ticket_classification(
    ticket_id: str,
    override_category: Optional[str] = Form(None),
    override_urgency: Optional[str] = Form(None),
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401, detail="not signed in")

    ticket = db.query(TicketClassification).filter_by(ticket_id=ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"no ticket classification with ticket_id={ticket_id!r}")

    before = {"override_category": ticket.override_category, "override_urgency": ticket.override_urgency}

    ticket.override_category = override_category
    ticket.override_urgency = override_urgency
    ticket.overridden_by_id = user.id
    ticket.overridden_at = datetime.now(timezone.utc)

    after = {"override_category": ticket.override_category, "override_urgency": ticket.override_urgency}

    log_change(
        db,
        action="ticket_classification.overridden",
        entity_type="ticket_classification",
        entity_id=ticket.ticket_id,
        before=before,
        after=after,
        actor_id=user.id,
    )

    db.commit()
    return RedirectResponse("/ticket-triage", status_code=303)
