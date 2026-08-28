"""Configuration is read entirely from environment variables — locally from a
gitignored .env (see .env.example), in the cloud from variables the platform
injects via Key Vault-backed secretRefs and a managed identity. Application
code is identical either way: it only ever calls os.getenv, never a vault
SDK. Mirrors project-1's config.py pattern
(../project-1-scheduled-pipeline/src/pipeline/config.py): a frozen dataclass
built by a single load_config() call.

load_dotenv() is unconditional, but only has an effect when a .env file is
actually present on disk — which is what makes this "local dev only" without
needing an if/else. Locally a .env exists and gets loaded (without
overriding any variable already set in the real environment, since
load_dotenv()'s default is override=False). In the deployed container, .env
is never copied into the image (see deploy/Dockerfile and .dockerignore),
so the call is a no-op there and the platform-injected environment
variables pass straight through untouched.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Postgres in the cloud via DATABASE_URL; sqlite:///./local.db is the
# local-dev fallback when it's unset (see .env.example and db/base.py).
_DEFAULT_DATABASE_URL = "sqlite:///./local.db"


@dataclass(frozen=True)
class Config:
    database_url: str
    session_secret_key: Optional[str]
    entra_tenant_id: Optional[str]
    entra_client_id: Optional[str]
    entra_client_secret: Optional[str]
    entra_redirect_uri: Optional[str]
    project1_ingestion_source: Optional[str]
    project2_review_queue_source: Optional[str]
    project4_triage_source: Optional[str]


# Unlike project-1 (which uses a `_require()` helper for DATABASE_URL, its
# one field with no safe default), nothing here is hard-required at load
# time: DATABASE_URL always has the sqlite fallback above, and the
# session/Entra/PROJECT*_SOURCE values are read by code that doesn't exist
# yet (auth/, integrations/). That code is the right place to validate its
# own inputs when it lands, so importing config today never raises just
# because a .env hasn't been created.
def load_config() -> Config:
    return Config(
        database_url=os.getenv("DATABASE_URL", _DEFAULT_DATABASE_URL),
        session_secret_key=os.getenv("SESSION_SECRET_KEY"),
        entra_tenant_id=os.getenv("ENTRA_TENANT_ID"),
        entra_client_id=os.getenv("ENTRA_CLIENT_ID"),
        entra_client_secret=os.getenv("ENTRA_CLIENT_SECRET"),
        entra_redirect_uri=os.getenv("ENTRA_REDIRECT_URI"),
        project1_ingestion_source=os.getenv("PROJECT1_INGESTION_SOURCE"),
        project2_review_queue_source=os.getenv("PROJECT2_REVIEW_QUEUE_SOURCE"),
        project4_triage_source=os.getenv("PROJECT4_TRIAGE_SOURCE"),
    )
