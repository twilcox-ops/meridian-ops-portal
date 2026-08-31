"""FastAPI application factory.

TODO:
- mount templates/ and static/
- register the remaining routers: dashboard, review_queue, ticket_triage,
  asset_registry
- this is intentionally close to the minimal app needed to prove `uvicorn`
  can start it: SessionMiddleware is wired in below because
  auth/session.py's get_current_user() depends on request.session
  existing, and the auth router is registered because it's the only thing
  that can populate that session — but there is still no "/" route (the
  auth callback's post-login redirect target) or other business logic.
"""

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from .config import load_config
from .routers.auth import router as auth_router

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
