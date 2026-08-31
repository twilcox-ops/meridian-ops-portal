"""GET / (routers/dashboard.py): a signed-out request is rejected; a
signed-in user gets 200 with Project 1's ingestion health rendered onto
the page.

Signing in reuses the real /login -> /auth/callback round trip from
test_auth_routes.py (msal_client's token exchange mocked, everything else
real). The ingestion data itself comes from a real, temporary SQLite file
shaped like project-1's schema (pipeline_watermark + earthquakes — see
../../project-1-scheduled-pipeline/src/pipeline/db.py), so this also
exercises integrations/project1_ingestion.py's query logic end-to-end,
not just the route.
"""
from __future__ import annotations

import sqlite3

from ops_portal.auth import msal_client

FAKE_AUTHORIZE_URL = "https://login.microsoftonline.com/fake-tenant/oauth2/v2.0/authorize"


def _fake_token_result(oid="oid-dash", email="dash@example.com", name="Dash User", roles=None):
    return {
        "id_token_claims": {
            "oid": oid,
            "email": email,
            "name": name,
            "roles": roles or [],
        }
    }


def _sign_in(client, monkeypatch, roles=None) -> None:
    monkeypatch.setattr(msal_client, "build_auth_url", lambda state=None: f"{FAKE_AUTHORIZE_URL}?state={state}")
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.headers["location"].rsplit("state=", 1)[-1]

    monkeypatch.setattr(msal_client, "acquire_token_by_auth_code", lambda code: _fake_token_result(roles=roles))
    callback_response = client.get(f"/auth/callback?code=fake-code&state={state}", follow_redirects=False)
    assert callback_response.status_code == 302


def _make_project1_db(tmp_path):
    db_path = tmp_path / "project1.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE earthquakes (id TEXT PRIMARY KEY)")
    conn.execute(
        """
        CREATE TABLE pipeline_watermark (
            job_name TEXT PRIMARY KEY,
            last_updated_watermark_ms INTEGER,
            last_run_started_at_ms INTEGER,
            last_run_completed_at_ms INTEGER,
            last_run_status TEXT,
            last_run_error TEXT
        )
        """
    )
    conn.executemany("INSERT INTO earthquakes (id) VALUES (?)", [("us1",), ("us2",), ("us3",)])
    conn.execute(
        "INSERT INTO pipeline_watermark "
        "(job_name, last_updated_watermark_ms, last_run_started_at_ms, last_run_completed_at_ms, "
        "last_run_status, last_run_error) VALUES (?, ?, ?, ?, ?, ?)",
        ("usgs_earthquakes", 1_700_000_000_000, 1_700_000_000_000, 1_700_000_005_000, "success", None),
    )
    conn.commit()
    conn.close()
    return db_path


def test_signed_out_request_is_rejected(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 401


def test_signed_in_user_sees_placeholder_when_source_not_configured(client, monkeypatch):
    monkeypatch.delenv("PROJECT1_INGESTION_SOURCE", raising=False)
    _sign_in(client, monkeypatch, roles=["viewer"])

    response = client.get("/")

    assert response.status_code == 200
    assert 'id="ingestion-unavailable"' in response.text


def test_signed_in_user_sees_ingestion_health(client, monkeypatch, tmp_path):
    db_path = _make_project1_db(tmp_path)
    monkeypatch.setenv("PROJECT1_INGESTION_SOURCE", str(db_path))
    _sign_in(client, monkeypatch, roles=["approver"])

    response = client.get("/")

    assert response.status_code == 200
    assert 'id="records-processed">3<' in response.text
    assert "success" in response.text
    assert "dash@example.com" in response.text  # the signed-in user indicator
