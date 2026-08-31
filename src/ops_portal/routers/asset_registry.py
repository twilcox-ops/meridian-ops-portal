"""Asset registry screen: the cleaned Part 1 data, searchable, with
rejected rows and their reasons visible alongside it.

GET /asset-registry — any signed-in user, viewer or approver; no role
restriction, the same "signed in or 401" pattern as routers/dashboard.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from ..auth.session import get_current_user
from ..db.models import User
from ..integrations.asset_registry_data import filter_clean_rows, read_asset_registry_data

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/asset-registry")
def asset_registry(
    request: Request,
    q: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user),
):
    if user is None:
        raise HTTPException(status_code=401, detail="not signed in")

    data = read_asset_registry_data()
    context = {
        "user": user,
        "query": q or "",
        "available": data.available,
        "clean_rows": filter_clean_rows(data.clean_rows, q),
        "rejected_rows": data.rejected_rows,
    }
    return templates.TemplateResponse(request, "asset_registry.html", context)
