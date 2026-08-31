"""Running normalization/pipeline.py twice against the same input must
produce byte-identical clean.csv and rejected.csv, not just matching row
counts — see pipeline.py's module docstring for the two things that could
break that (set-iteration-order row shuffling, and any wall-clock value
leaking into the output) and why this implementation avoids them.

This fixture is intentionally independent of test_pipeline.py's — kept
self-contained rather than imported, so this file exercises the pipeline
without depending on another test module's internals.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ops_portal.normalization.pipeline import run_pipeline

_HEADER = ["asset_tag", "building", "unit_id", "install_date", "status", "last_service_cost", "technician", "notes"]

_ROWS = [
    ["MES-2001", "Harborview Tower", "A1-1", "2022-09-09", "Active", "1234.56", "J. Smith", "OK"],
    ["MES-2002", "HARBORVIEW TOWER", "A1-2", "04/02/2022", "Active", "$500.00", "A. Lee", "n/a"],
    ["MES-2003", "Kestrel Plz", "B2-2", "Not A Date", "Active", "500.00", "A. Lee", "OK"],
    ["MES-2004", "Alder Commons", "C3-3", "2021-05-01", "Active", "(1,234.56)", "R. Okonkwo", "OK"],
    ["MES-2005", "Sable Point Medical", "D4-4", "2020-01-01", "Active", "100.00", "A. Lee", "first copy"],
    ["MES-2005", "Sable Point Medical", "D4-4", "2020-01-01", "Active", "100.00", "A. Lee", ""],
    ["MES-2006", "Kestrel Plaza", "E5-5", "07/24/23", "Retired", "2,000.00", "M. Lindqvist", ""],
]


def _write_fixture_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_HEADER)
        writer.writerows(_ROWS)


def test_running_the_pipeline_twice_produces_byte_identical_output(tmp_path, db_session):
    source = tmp_path / "fixture.csv"
    _write_fixture_csv(source)

    first_output = tmp_path / "run1"
    second_output = tmp_path / "run2"

    # Same db_session for both calls on purpose: audit_log is append-only
    # and is expected to grow on every run (that's not what's under test
    # here) — what must not change between runs is the file output.
    first_summary = run_pipeline(db_session, source_path=source, output_dir=first_output)
    second_summary = run_pipeline(db_session, source_path=source, output_dir=second_output)

    assert first_summary == second_summary

    for filename in ("clean.csv", "rejected.csv"):
        first_bytes = (first_output / filename).read_bytes()
        second_bytes = (second_output / filename).read_bytes()
        assert first_bytes == second_bytes, f"{filename} differed between the two runs"
