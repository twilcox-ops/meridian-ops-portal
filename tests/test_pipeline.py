"""Integration-style tests for normalization/pipeline.py, against a small,
hand-built CSV fixture (not the full 648-row sample data): a bad-date row
lands in rejected.csv with a specific reason, a parenthesized-negative
currency value lands correctly in clean.csv, and a real duplicate pair
collapses to the one row dedupe.py's survivorship rule picks.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ops_portal.normalization.pipeline import run_pipeline

_HEADER = ["asset_tag", "building", "unit_id", "install_date", "status", "last_service_cost", "technician", "notes"]

_ROWS = [
    ["MES-1001", "Harborview Tower", "A1-1", "2022-09-09", "Active", "1234.56", "J. Smith", "OK"],
    ["MES-1002", "Kestrel Plaza", "B2-2", "Not A Date", "Active", "500.00", "A. Lee", "OK"],
    ["MES-1003", "Alder Commons", "C3-3", "2021-05-01", "Active", "(1,234.56)", "R. Okonkwo", "OK"],
    # MES-1004 appears twice: the second copy is missing `notes`, so the
    # first (more complete) copy is the one dedupe.py should keep.
    ["MES-1004", "Sable Point Medical", "D4-4", "2020-01-01", "Active", "100.00", "A. Lee", "first copy"],
    ["MES-1004", "Sable Point Medical", "D4-4", "2020-01-01", "Active", "100.00", "A. Lee", ""],
]


def _write_fixture_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_HEADER)
        writer.writerows(_ROWS)


def _run(tmp_path: Path, db_session):
    source = tmp_path / "fixture.csv"
    _write_fixture_csv(source)
    output_dir = tmp_path / "out"
    summary = run_pipeline(db_session, source_path=source, output_dir=output_dir)

    with (output_dir / "clean.csv").open(encoding="utf-8", newline="") as f:
        clean = list(csv.DictReader(f))
    with (output_dir / "rejected.csv").open(encoding="utf-8", newline="") as f:
        rejected = list(csv.DictReader(f))

    return summary, clean, rejected


def test_bad_date_row_is_rejected_with_a_specific_reason(tmp_path, db_session):
    summary, clean, rejected = _run(tmp_path, db_session)

    assert summary.rejected_rows == 1
    bad_date_row = next(r for r in rejected if r["asset_tag"] == "MES-1002")
    assert "unrecognized date format" in bad_date_row["reason"]
    assert "Not A Date" in bad_date_row["reason"]
    assert all(r["asset_tag"] != "MES-1002" for r in clean)  # never silently in both


def test_parenthesized_negative_currency_lands_correctly_in_clean_csv(tmp_path, db_session):
    _, clean, _ = _run(tmp_path, db_session)

    row = next(r for r in clean if r["asset_tag"] == "MES-1003")
    assert row["last_service_cost"] == "-1234.56"


def test_duplicate_pair_collapses_to_the_more_complete_row(tmp_path, db_session):
    summary, clean, _ = _run(tmp_path, db_session)

    matches = [r for r in clean if r["asset_tag"] == "MES-1004"]
    assert len(matches) == 1
    assert matches[0]["notes"] == "first copy"  # the more complete row survived
    assert summary.duplicate_groups_resolved == 1


def test_summary_counts_are_internally_consistent(tmp_path, db_session):
    summary, clean, rejected = _run(tmp_path, db_session)

    assert summary.total_rows_read == len(_ROWS)
    assert summary.clean_rows_written == len(clean) == 3  # 1001, 1003, and one 1004
    assert summary.rejected_rows == len(rejected) == 1
