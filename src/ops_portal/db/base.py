"""SQLAlchemy engine/session setup.

Postgres in production via DATABASE_URL; sqlite:///./local.db for local dev
when it isn't set — same split as project-1's db.get_engine()
(../../project-1-scheduled-pipeline/src/pipeline/db.py), except the engine
here is created once at import time and shared, rather than threaded through
as a function argument: FastAPI's request-scoped get_db() dependency below
needs a module-level sessionmaker to open a session from on every request.
"""
from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from ..config import load_config

_config = load_config()

# SQLite connections are single-threaded by default, but FastAPI can hand a
# request's dependency teardown to a different thread than the one that
# opened it; check_same_thread=False lifts that restriction. Postgres has no
# such restriction and gets no extra connect_args.
_connect_args = {"check_same_thread": False} if _config.database_url.startswith("sqlite") else {}

engine = create_engine(_config.database_url, connect_args=_connect_args, future=True)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

# Every ORM model in db/models.py inherits from this.
Base = declarative_base()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields one Session per request, always closed after.

    Usage: `db: Session = Depends(get_db)` in a route function.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
