"""Read-only adapter onto Project 2's low-confidence extraction output.

Reads PROJECT2_REVIEW_QUEUE_SOURCE (see config.py) as a filesystem path to
a JSON Lines file Project 2 produces — one line per low-confidence
extraction it flagged for human review, shaped like:

    {"source_document": "MES-2026-4100.pdf", "field_name": "invoice_total",
     "extracted_value": "1766.82", "confidence": 0.42}

Unlike integrations/project1_ingestion.py (which queries Project 1's own
database live, on every dashboard load), the review queue's actual working
data lives in *this* project's own review_items table (db/models.py): the
maker-checker workflow in services/approvals.py operates on ReviewItem
rows, which need their own id, status, and (once corrected)
corrected_value — none of which belong in, or should be written back into,
Project 2's own output. sync_review_items_from_source() is the one-way
bridge between the two: it reads the JSONL file and inserts a new
ReviewItem for every (source_document, field_name) pair not already
present, so calling it again later is safe — no duplicate rows — and it
never reads back from or writes to Project 2's own data at all, only this
project's own database.

Project 2 doesn't exist yet in this portfolio (no
project-2-document-extraction/ directory alongside this one), so this dev
environment has no real source file to point at. Every failure mode here —
unset var, missing file, a line that isn't valid JSON, or one missing a
required field — is skipped/degraded rather than raising: routers/
review_queue.py's screen should never 500 because an optional upstream
file is absent or malformed, and one bad line shouldn't sink an otherwise
good import.

routers/review_queue.py does not call sync_review_items_from_source()
itself on every page load — GET /review-queue's job is reading this
project's own review_items table, full stop. Syncing from Project 2 is a
separate, explicit step (a future CLI/scheduled job would call this
function directly), kept out of the request path so a page view is never
what triggers a file read against an external system.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..config import load_config
from ..db.models import ReviewItem


@dataclass(frozen=True)
class SyncResult:
    available: bool  # False: PROJECT2_REVIEW_QUEUE_SOURCE unset or the file is missing
    records_read: int
    records_imported: int
    records_skipped: int  # malformed lines, or a (source_document, field_name) already present


def _default_source_path() -> Optional[Path]:
    source = load_config().project2_review_queue_source
    return Path(source) if source else None


def _parse_record(line: str) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    source_document = data.get("source_document")
    field_name = data.get("field_name")
    if not source_document or not field_name:
        return None

    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        return None

    extracted_value = data.get("extracted_value")
    return {
        "source_document": source_document,
        "field_name": field_name,
        "extracted_value": None if extracted_value is None else str(extracted_value),
        "confidence": confidence,
    }


def sync_review_items_from_source(db: Session, source_path: Optional[Path] = None) -> SyncResult:
    """Imports every record from the source JSONL file that isn't already
    present (matched on the (source_document, field_name) pair) as a new,
    PENDING ReviewItem. Flushes, not commits — same convention as every
    other service in this project — so the caller decides the transaction
    boundary.
    """
    resolved_path = Path(source_path) if source_path is not None else _default_source_path()
    if resolved_path is None or not resolved_path.is_file():
        return SyncResult(available=False, records_read=0, records_imported=0, records_skipped=0)

    existing_keys = {
        (row.source_document, row.field_name)
        for row in db.query(ReviewItem.source_document, ReviewItem.field_name).all()
    }

    records_read = 0
    records_imported = 0
    records_skipped = 0

    with resolved_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            records_read += 1

            record = _parse_record(line)
            if record is None:
                records_skipped += 1
                continue

            key = (record["source_document"], record["field_name"])
            if key in existing_keys:
                records_skipped += 1
                continue

            db.add(ReviewItem(**record))
            existing_keys.add(key)
            records_imported += 1

    db.flush()
    return SyncResult(
        available=True,
        records_read=records_read,
        records_imported=records_imported,
        records_skipped=records_skipped,
    )
