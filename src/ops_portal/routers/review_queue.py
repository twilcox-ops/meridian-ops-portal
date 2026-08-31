"""Review queue screen: Project 2's low-confidence extractions (as
mirrored into this project's own review_items table — see
integrations/project2_review.py for why), with approve/correct routed
through the maker-checker workflow in services/approvals.py.

GET /review-queue — any signed-in user, viewer or approver; same
"signed in or 401" pattern as dashboard/asset registry.

POST .../request — any signed-in user may propose a correction.

POST .../approve, POST .../reject — approver-only, enforced by
require_role(UserRole.APPROVER) — a 403 here is a real, server-side
rejection, not a UI affordance the template happens to hide (see
templates/review_queue.html's comment on exactly this point). A
self-approval attempt is also mapped to 403: services/approvals.py raises
SelfApprovalError for it, and refusing to let someone approve their own
request is an authorization failure (who is allowed to decide this), not
a validation one, so it gets the same status code as failing the role
check.

Every mutating route commits after its service call succeeds —
services/approvals.py only flushes (by design: it doesn't get to decide a
request's transaction boundary), so committing is this router's job.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth.roles import require_role
from ..auth.session import get_current_user
from ..db.base import get_db
from ..db.models import Approval, ApprovalStatus, ReviewItem, User, UserRole
from ..services.approvals import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    DuplicatePendingApprovalError,
    ReviewItemNotFoundError,
    SelfApprovalError,
    approve,
    reject,
    request_correction,
)

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/review-queue")
def review_queue(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401, detail="not signed in")

    items = db.query(ReviewItem).order_by(ReviewItem.id).all()
    pending_by_review_item_id = {
        approval.review_item_id: approval
        for approval in db.query(Approval).filter(Approval.status == ApprovalStatus.PENDING).all()
    }
    rows = [{"item": item, "pending_approval": pending_by_review_item_id.get(item.id)} for item in items]

    return templates.TemplateResponse(request, "review_queue.html", {"user": user, "rows": rows})


@router.post("/review-queue/{review_item_id}/request")
def request_correction_route(
    review_item_id: int,
    proposed_value: str = Form(...),
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=401, detail="not signed in")

    try:
        request_correction(db, review_item_id, user.id, proposed_value)
    except ReviewItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicatePendingApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    db.commit()
    return RedirectResponse("/review-queue", status_code=303)


@router.post("/review-queue/{approval_id}/approve")
def approve_route(
    approval_id: int,
    user: User = Depends(require_role(UserRole.APPROVER)),
    db: Session = Depends(get_db),
):
    try:
        approve(db, approval_id, user.id)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SelfApprovalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    db.commit()
    return RedirectResponse("/review-queue", status_code=303)


@router.post("/review-queue/{approval_id}/reject")
def reject_route(
    approval_id: int,
    notes: Optional[str] = Form(None),
    user: User = Depends(require_role(UserRole.APPROVER)),
    db: Session = Depends(get_db),
):
    try:
        reject(db, approval_id, user.id, notes=notes)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SelfApprovalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    db.commit()
    return RedirectResponse("/review-queue", status_code=303)
