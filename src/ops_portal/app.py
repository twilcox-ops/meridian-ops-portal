"""FastAPI application factory.

TODO:
- mount static/ (templates/ needs no mounting — each router's own
  Jinja2Templates instance reads straight from the directory)
- all four portal screens (dashboard, review queue, ticket triage, asset
  registry) plus auth are registered below; nothing left unregistered.
"""

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from .config import load_config
from .routers.asset_registry import router as asset_registry_router
from .routers.auth import router as auth_router
from .routers.dashboard import router as dashboard_router
from .routers.review_queue import router as review_queue_router
from .routers.ticket_triage import router as ticket_triage_router

app = FastAPI(title="Meridian Ops Portal")

_config = load_config()
if not _config.session_secret_key:
    raise RuntimeError(
        "missing required environment variable: SESSION_SECRET_KEY "
        '(see .env.example — generate one with `python -c "import secrets; '
        'print(secrets.token_hex(32))"`)'
    )
app.add_middleware(SessionMiddleware, secret_key=_config.session_secret_key)

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(asset_registry_router)
app.include_router(review_queue_router)
app.include_router(ticket_triage_router)
