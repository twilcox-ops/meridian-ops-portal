"""MSAL confidential client wrapper — Entra ID auth-code flow.

Two entry points, used by the not-yet-built auth router (routers/auth.py):
build_auth_url() for the /auth/login redirect to Entra ID's login page, and
acquire_token_by_auth_code() for the /auth/callback handler that exchanges
the `code` query parameter Entra ID appends to ENTRA_REDIRECT_URI for an ID
token. auth/roles.py then reads that token's `roles` claim, and
auth/session.py's login_user() writes the result into the session.
"""
from __future__ import annotations

from typing import Any, Optional

import msal

from ..config import load_config

_config = load_config()

# Only the ID token is needed — role assignment (auth/roles.py) comes from
# its `roles` claim, not a Graph call, so no Graph scope is requested here.
_SCOPES: list[str] = []

_msal_app: Optional[msal.ConfidentialClientApplication] = None


def _authority() -> str:
    return f"https://login.microsoftonline.com/{_config.entra_tenant_id}"


def get_msal_app() -> msal.ConfidentialClientApplication:
    """Built and cached on first use, not at import time — so importing
    this module never requires ENTRA_* to be set (e.g. in tests, which
    fake the session instead of exercising this at all)."""
    global _msal_app
    if _msal_app is None:
        _msal_app = msal.ConfidentialClientApplication(
            client_id=_config.entra_client_id,
            client_credential=_config.entra_client_secret,
            authority=_authority(),
        )
    return _msal_app


def build_auth_url(state: Optional[str] = None) -> str:
    """The URL to redirect the browser to for the Entra ID login prompt.

    `state` should be a per-request random value the caller stashes in the
    session and re-checks in the callback (CSRF protection on the redirect)
    — that round trip belongs to routers/auth.py, not here.
    """
    return get_msal_app().get_authorization_request_url(
        scopes=_SCOPES,
        state=state,
        redirect_uri=_config.entra_redirect_uri,
    )


def acquire_token_by_auth_code(code: str) -> dict[str, Any]:
    """Completes the auth-code flow: exchanges the callback's `code` for an
    ID token. Raises RuntimeError on failure rather than returning MSAL's
    error dict, so callers don't have to remember to check for "error" in
    the result themselves.
    """
    result = get_msal_app().acquire_token_by_authorization_code(
        code=code,
        scopes=_SCOPES,
        redirect_uri=_config.entra_redirect_uri,
    )
    if "error" in result:
        raise RuntimeError(
            f"Entra ID token acquisition failed: {result.get('error')}: "
            f"{result.get('error_description')}"
        )
    return result
