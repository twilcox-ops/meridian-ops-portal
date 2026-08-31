"""Read-only adapter onto Project 4's ticket classification output.

Reads PROJECT4_TRIAGE_SOURCE (see config.py) as a filesystem path to a
JSON Lines file — one line per classified ticket, shaped like:

    {"ticket_id": "TKT-5148", "predicted_category": "compliance",
     "predicted_urgency": "medium", "confidence": 0.83}

("ticket_text" is also read if present, for extra context on the triage
screen, but isn't required — see _parse_record.)

Same reasoning as integrations/project2_review.py: the ticket triage
screen's actual working data lives in *this* project's own
ticket_classifications table (db/models.py), not read live from Project 4
on every page view — a human override (routers/ticket_triage.py) needs a
row of its own to update (override_category, override_urgency,
overridden_by_id, overridden_at), none of which belong in Project 4's own
output. sync_ticket_classifications_from_source() is the one-way bridge:
reads the JSONL file and inserts a new TicketClassification for every
ticket_id not already present (ticket_id is unique — see db/models.py),
so calling it again later is safe. It never reads back from or writes to
Project 4's own data at all.

Project 4 (meridian-llm-classifier-evals) is an eval harness, not a
service that publishes a standing predictions file for another system to
consume — its holdout/iteration JSONL files are scored once and reported
on, not emitted as an ongoing artifact — so, same as Projects 1 and 2,
this dev environment has no real source file to point at either. Same
conventions as integrations/project1_ingestion.py and project2_review.py:
every failure mode (unset var, missing file, a malformed line) is
skipped/degraded rather than raising.

routers/ticket_triage.py does not call this on every page load, for the
same reason routers/review_queue.py doesn't call
sync_review_items_from_source() on every page load — see that module's
docstring.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..config import load_config
from ..db.models import TicketClassification


@dataclass(frozen=True)
class SyncResult:
    available: bool  # False: PROJECT4_TRIAGE_SOURCE unset or the file is missing
    records_read: int
    records_imported: int
    records_skipped: int  # malformed lines, or a ticket_id already present


def _default_source_path() -> Optional[Path]:
    source = load_config().project4_triage_source
    return Path(source) if source else None


def _parse_record(line: str) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    ticket_id = data.get("ticket_id")
    if not ticket_id:
        return None

    # Unlike review_items.confidence (non-nullable), a ticket classification's
    # confidence is nullable — a missing value is allowed through as None,
    # but a present-and-unparseable one still means a malformed line.
    confidence = data.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return None

    return {
        "ticket_id": ticket_id,
        "ticket_text": data.get("ticket_text"),
        "predicted_category": data.get("predicted_category"),
        "predicted_urgency": data.get("predicted_urgency"),
        "confidence": confidence,
    }


def sync_ticket_classifications_from_source(db: Session, source_path: Optional[Path] = None) -> SyncResult:
    """Imports every record from the source JSONL file whose ticket_id
    isn't already present as a new TicketClassification. Flushes, not
    commits — same convention as every other service/integration in this
    project — so the caller decides the transaction boundary.
    """
    resolved_path = Path(source_path) if source_path is not None else _default_source_path()
    if resolved_path is None or not resolved_path.is_file():
        return SyncResult(available=False, records_read=0, records_imported=0, records_skipped=0)

    existing_ticket_ids = {row.ticket_id for row in db.query(TicketClassification.ticket_id).all()}

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
            if record is None or record["ticket_id"] in existing_ticket_ids:
                records_skipped += 1
                continue

            db.add(TicketClassification(**record))
            existing_ticket_ids.add(record["ticket_id"])
            records_imported += 1

    db.flush()
    return SyncResult(
        available=True,
        records_read=records_read,
        records_imported=records_imported,
        records_skipped=records_skipped,
    )
