"""FastAPI application factory.

TODO:
- mount static/ (templates/ needs no mounting — routers/dashboard.py's
  Jinja2Templates instance reads straight from the directory)
- register the remaining routers: review_queue, ticket_triage,
  asset_registry
"""

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from .config import load_config
from .routers.auth import router as auth_router
from .routers.dashboard import router as dashboard_router

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
