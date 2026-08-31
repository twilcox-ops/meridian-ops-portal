"""GET /asset-registry (routers/asset_registry.py): signed-out is
rejected; with no clean.csv/rejected.csv present it shows an empty state
(200, not a crash); with real fixture CSVs it shows the correct rows;
?q= filters by asset_tag or building (case-insensitive substring); and
rejected rows and their reasons are visible on the page.

`asset_registry_data.DEFAULT_OUTPUT_DIR` is monkeypatched per test to an
isolated tmp_path directory, so these tests never depend on (or pollute)
the real normalization_output/ directory normalization/pipeline.py writes
to by default.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ops_portal.auth import msal_client
from ops_portal.integrations import asset_registry_data

FAKE_AUTHORIZE_URL = "https://login.microsoftonline.com/fake-tenant/oauth2/v2.0/authorize"

_CLEAN_HEADER = ["asset_tag", "building", "unit_id", "install_date", "status", "last_service_cost", "technician", "notes"]
_CLEAN_ROWS = [
    ["MES-1001", "Harborview Tower", "A1-1", "2022-09-09", "Active", "1234.56", "J. Smith", "OK"],
    ["MES-1002", "Kestrel Plaza", "B2-2", "2021-05-01", "Active", "500.00", "A. Lee", "OK"],
    ["MES-1003", "Harborview Tower", "C3-3", "2020-01-01", "Retired", "-1234.56", "R. Okonkwo", ""],
]
_REJECTED_HEADER = _CLEAN_HEADER + ["reason"]
_REJECTED_ROWS = [
    ["MES-2001", "Alder Commons", "D4-4", "", "Active", "", "M. Lindqvist", "", "unrecognized date format: 'Not A Date'"],
]


def _write_fixture_csvs(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "clean.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_CLEAN_HEADER)
        writer.writerows(_CLEAN_ROWS)
    with (output_dir / "rejected.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_REJECTED_HEADER)
        writer.writerows(_REJECTED_ROWS)


def _fake_token_result(oid="oid-registry", email="registry@example.com", name="Registry User", roles=None):
    return {"id_token_claims": {"oid": oid, "email": email, "name": name, "roles": roles or []}}


def _sign_in(client, monkeypatch, roles=None) -> None:
    monkeypatch.setattr(msal_client, "build_auth_url", lambda state=None: f"{FAKE_AUTHORIZE_URL}?state={state}")
    login_response = client.get("/login", follow_redirects=False)
    state = login_response.headers["location"].rsplit("state=", 1)[-1]
    monkeypatch.setattr(msal_client, "acquire_token_by_auth_code", lambda code: _fake_token_result(roles=roles))
    callback_response = client.get(f"/auth/callback?code=fake-code&state={state}", follow_redirects=False)
    assert callback_response.status_code == 302


def test_signed_out_request_is_rejected(client):
    response = client.get("/asset-registry", follow_redirects=False)

    assert response.status_code == 401


def test_signed_in_with_no_files_shows_empty_state_not_a_crash(client, monkeypatch, tmp_path):
    monkeypatch.setattr(asset_registry_data, "DEFAULT_OUTPUT_DIR", tmp_path / "does-not-exist")
    _sign_in(client, monkeypatch, roles=["viewer"])

    response = client.get("/asset-registry")

    assert response.status_code == 200
    assert 'id="registry-unavailable"' in response.text
    assert 'id="rejected-unavailable"' in response.text


def test_signed_in_with_real_fixture_csvs_shows_the_rows(client, monkeypatch, tmp_path):
    output_dir = tmp_path / "normalization_output"
    _write_fixture_csvs(output_dir)
    monkeypatch.setattr(asset_registry_data, "DEFAULT_OUTPUT_DIR", output_dir)
    _sign_in(client, monkeypatch, roles=["approver"])

    response = client.get("/asset-registry")

    assert response.status_code == 200
    assert "MES-1001" in response.text
    assert "MES-1002" in response.text
    assert "MES-1003" in response.text
    assert "Harborview Tower" in response.text
    assert "-1234.56" in response.text


def test_search_filters_by_asset_tag(client, monkeypatch, tmp_path):
    output_dir = tmp_path / "normalization_output"
    _write_fixture_csvs(output_dir)
    monkeypatch.setattr(asset_registry_data, "DEFAULT_OUTPUT_DIR", output_dir)
    _sign_in(client, monkeypatch, roles=["viewer"])

    response = client.get("/asset-registry", params={"q": "mes-1002"})

    assert response.status_code == 200
    assert "MES-1002" in response.text
    assert "MES-1001" not in response.text
    assert "MES-1003" not in response.text


def test_search_filters_by_building_name_case_insensitively(client, monkeypatch, tmp_path):
    output_dir = tmp_path / "normalization_output"
    _write_fixture_csvs(output_dir)
    monkeypatch.setattr(asset_registry_data, "DEFAULT_OUTPUT_DIR", output_dir)
    _sign_in(client, monkeypatch, roles=["viewer"])

    response = client.get("/asset-registry", params={"q": "harborview"})

    assert response.status_code == 200
    assert "MES-1001" in response.text
    assert "MES-1003" in response.text
    assert "MES-1002" not in response.text  # Kestrel Plaza, no match


def test_rejected_rows_and_their_reasons_are_visible(client, monkeypatch, tmp_path):
    output_dir = tmp_path / "normalization_output"
    _write_fixture_csvs(output_dir)
    monkeypatch.setattr(asset_registry_data, "DEFAULT_OUTPUT_DIR", output_dir)
    _sign_in(client, monkeypatch, roles=["viewer"])

    response = client.get("/asset-registry")

    assert response.status_code == 200
    assert "MES-2001" in response.text
    assert "unrecognized date format" in response.text
    assert "Not A Date" in response.text
