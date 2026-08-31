"""Dashboard screen: ingestion health from Project 1.

GET / — any signed-in user, viewer or approver; no role restriction, just
"signed in" (the same `get_current_user() is None -> 401` pattern
test_authorization.py exercises for a route that doesn't need a specific
role).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from ..auth.session import get_current_user
from ..db.models import User
from ..integrations.project1_ingestion import get_ingestion_health

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _format_run_at(last_run_at_ms: Optional[int]) -> Optional[str]:
    if last_run_at_ms is None:
        return None
    return datetime.fromtimestamp(last_run_at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@router.get("/")
def dashboard(request: Request, user: Optional[User] = Depends(get_current_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="not signed in")

    health = get_ingestion_health()
    context = {
        "user": user,
        "ingestion": {
            "available": health.available,
            "records_processed": health.records_processed,
            "last_run_at": _format_run_at(health.last_run_at_ms),
            "last_run_status": health.last_run_status,
            "failures": health.failures,
        },
    }
    return templates.TemplateResponse(request, "dashboard.html", context)
