"""Near-duplicate rows (same asset_tag) -> one surviving row, picked by the
three-step rule documented in dedupe.py's module docstring: fewest missing
fields, then a later install_date, then first-seen order. Every dropped
row's decision is recorded to the audit log.

Every test here uses the shared `db_session` fixture from conftest.py
(an isolated in-memory SQLite database) since resolve_duplicate_group()
writes to audit_log for each row it drops.
"""
from __future__ import annotations

import pytest

from ops_portal.db.models import AuditLog
from ops_portal.normalization.dedupe import resolve_duplicate_group


def test_more_complete_row_wins_on_fewer_missing_fields(db_session):
    sparse = {"asset_tag": "MES-1", "building": "Kestrel Plaza", "unit_id": None, "notes": None}
    complete = {"asset_tag": "MES-1", "building": "Kestrel Plaza", "unit_id": "A1-1", "notes": "OK"}

    survivor = resolve_duplicate_group([sparse, complete], db_session)

    assert survivor is complete


def test_tie_break_by_more_recent_install_date_when_completeness_ties(db_session):
    older = {"asset_tag": "MES-2", "building": "Alder Commons", "install_date": "2020-01-01"}
    newer = {"asset_tag": "MES-2", "building": "Alder Commons", "install_date": "2023-06-15"}

    # Order shouldn't matter to the outcome — only which row is actually better.
    assert resolve_duplicate_group([older, newer], db_session) is newer
    assert resolve_duplicate_group([newer, older], db_session) is newer


def test_full_tie_falls_back_to_first_seen_order(db_session):
    first_seen = {"asset_tag": "MES-3", "building": "Harborview Tower", "status": "Active"}
    second_seen = {"asset_tag": "MES-3", "building": "Harborview Tower", "status": "Active"}

    survivor = resolve_duplicate_group([first_seen, second_seen], db_session)

    assert survivor is first_seen


def test_dropped_rows_each_write_an_audit_log_entry(db_session):
    dropped = {"asset_tag": "MES-4", "building": "Kestrel Plaza", "unit_id": None}
    kept = {"asset_tag": "MES-4", "building": "Kestrel Plaza", "unit_id": "B2-2"}

    survivor = resolve_duplicate_group([dropped, kept], db_session)

    assert survivor is kept
    entries = db_session.query(AuditLog).all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "deduplication.dropped"
    assert entry.entity_type == "asset_row"
    assert entry.before == dropped
    assert entry.after == "MES-4"
    assert entry.actor_id is None


def test_a_group_of_one_writes_no_audit_log_entry(db_session):
    only_row = {"asset_tag": "MES-5", "building": "Sable Point Medical"}

    survivor = resolve_duplicate_group([only_row], db_session)

    assert survivor is only_row
    assert db_session.query(AuditLog).count() == 0


def test_three_way_group_logs_exactly_two_drops(db_session):
    a = {"asset_tag": "MES-6", "building": "Alder Commons", "unit_id": None}
    b = {"asset_tag": "MES-6", "building": "Alder Commons", "unit_id": None}
    c = {"asset_tag": "MES-6", "building": "Alder Commons", "unit_id": "C3-3"}

    survivor = resolve_duplicate_group([a, b, c], db_session)

    assert survivor is c
    entries = db_session.query(AuditLog).all()
    assert len(entries) == 2
    assert {entry.before["unit_id"] for entry in entries} == {None}
    assert all(entry.after == "MES-6" for entry in entries)


def test_mismatched_asset_tags_raise_value_error(db_session):
    a = {"asset_tag": "MES-7", "building": "Alder Commons"}
    b = {"asset_tag": "MES-8", "building": "Alder Commons"}

    with pytest.raises(ValueError, match="same asset_tag"):
        resolve_duplicate_group([a, b], db_session)


def test_empty_candidate_list_raises_value_error(db_session):
    with pytest.raises(ValueError, match="at least one candidate"):
        resolve_duplicate_group([], db_session)
