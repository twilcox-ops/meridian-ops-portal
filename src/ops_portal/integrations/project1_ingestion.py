"""Read-only adapter onto Project 1's ingestion output.

Reads PROJECT1_INGESTION_SOURCE (see config.py) as a filesystem path to
Project 1's own SQLite database and queries its `pipeline_watermark` and
`earthquakes` tables (see
../../project-1-scheduled-pipeline/src/pipeline/db.py) for the dashboard's
ingestion-health card: records processed, when the job last ran, and how
many recorded runs failed.

Never writes back into Project 1's database: the connection is opened in
SQLite's own read-only URI mode (`mode=ro`), so a write attempt would raise
at the database level rather than just happening not to occur because no
write code path exists here — the same "enforced, not just avoided"
approach as db/models.py's audit_log trigger.

This dev environment has no real Project 1 database to point at
(PROJECT1_INGESTION_SOURCE is blank in .env). Every failure mode here —
unset var, missing file, unreadable file, unexpected schema — returns
placeholder data instead of raising: a dashboard card reading "no data
yet" is the right degraded state for a read-only adapter over an optional
upstream system, a 500 is not.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import load_config


@dataclass(frozen=True)
class IngestionHealth:
    available: bool  # False: source unset, missing, unreadable, or unqueryable
    records_processed: int
    last_run_at_ms: Optional[int]
    last_run_status: Optional[str]
    failures: int


_UNAVAILABLE = IngestionHealth(
    available=False,
    records_processed=0,
    last_run_at_ms=None,
    last_run_status=None,
    failures=0,
)


def get_ingestion_health() -> IngestionHealth:
    source = load_config().project1_ingestion_source
    if not source or not Path(source).is_file():
        return _UNAVAILABLE

    try:
        uri = f"file:{Path(source).as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            return _query_health(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        # Upstream schema changed, file is corrupt/locked, etc. — degrade
        # to placeholder data rather than taking the dashboard down.
        return _UNAVAILABLE


def _query_health(conn: sqlite3.Connection) -> IngestionHealth:
    records_processed = conn.execute("SELECT COUNT(*) FROM earthquakes").fetchone()[0]

    last_run_at_ms: Optional[int] = None
    last_run_status: Optional[str] = None
    failures = 0

    # One row per job in project-1's schema; today that's just
    # "usgs_earthquakes", but summing/max-ing across whatever rows exist
    # keeps this correct if a second scheduled job is ever added there.
    rows = conn.execute(
        "SELECT last_run_completed_at_ms, last_run_started_at_ms, last_run_status "
        "FROM pipeline_watermark"
    )
    for completed_ms, started_ms, status in rows:
        run_at_ms = completed_ms if completed_ms is not None else started_ms
        if run_at_ms is not None and (last_run_at_ms is None or run_at_ms > last_run_at_ms):
            last_run_at_ms = run_at_ms
            last_run_status = status
        if status == "failed":
            failures += 1

    return IngestionHealth(
        available=True,
        records_processed=records_processed,
        last_run_at_ms=last_run_at_ms,
        last_run_status=last_run_status,
        failures=failures,
    )
