"""Orchestrates rules.py + entity_resolution.py + dedupe.py.

run_pipeline() reads sample-data/messy-asset-registry.csv, applies rules.py's
field-level normalization to every row, resolves each row's building name
through entity_resolution.py, groups the results by asset_tag, resolves any
group of more than one row through dedupe.py's survivorship rule, and writes
clean.csv + rejected.csv (with a reason per row — a row is never silently
dropped) plus a small summary of counts.

Row-level rejection: exactly two things reject a row here, both explained
in the `reason` column with the specific error text, not a generic message:

1. A blank/missing asset_tag. Nothing downstream works without one — it's
   the grouping key dedupe.py operates on and the natural primary key for
   the eventual assets table (db/models.py's Asset.asset_tag is unique,
   not-null) — so there's no reasonable "clean" row to produce.
2. normalize_date() or normalize_currency() raising ValueError on
   install_date / last_service_cost. Every other field just gets cleaned
   with rules.py's text-oriented rules (normalize_dashes,
   strip_trailing_whitespace, normalize_null_token), which never raise, so
   these two are the only failure modes rules.py exposes.

A row is checked for both failure modes *before* its building name is
resolved: entity_resolution.resolve_building_name() has a side effect
(mutating the known-buildings registry and, on a merge, writing to
audit_log), and a row that's about to be rejected shouldn't be able to
influence either — only rows that make it into clean.csv should ever touch
that registry.

Idempotency: running this twice on the same input must produce
byte-identical clean.csv and rejected.csv, not just matching row counts.
Two things that could break that, both avoided deliberately:
- Row order. Rows are grouped by asset_tag into a plain `dict`, never a
  `set` — a set's iteration order isn't part of Python's language
  guarantees (and can differ run to run under hash randomization), while a
  dict has preserved insertion order as a language guarantee since 3.7.
  Every row list here (raw rows read from the CSV, each asset_tag's group
  of candidates, the rejected list) is ordered by first appearance in the
  source file and nothing here ever reorders by hash-based iteration.
- No wall-clock output. Nothing here writes the current time, a run id, or
  any other value that isn't a pure function of the source file's content
  into clean.csv or rejected.csv. (audit_log rows written along the way by
  entity_resolution/dedupe do carry a server-side timestamp — that's
  correct and expected for an append-only audit trail, and orthogonal to
  the file-output idempotency guarantee this module is responsible for.)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Union

from sqlalchemy.orm import Session

from .dedupe import resolve_duplicate_group
from .entity_resolution import resolve_building_name
from .rules import normalize_currency, normalize_dashes, normalize_date, normalize_null_token, strip_trailing_whitespace

# messy-asset-registry.csv lives one level up from this repo, alongside the
# other projects (see the portfolio's own layout) — not inside project-5's
# own src tree. parents[3] from this file is project-5-capstone-ops-portal;
# its parent is the portfolio root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SOURCE_PATH = _PROJECT_ROOT.parent / "sample-data" / "messy-asset-registry.csv"

# Public (not underscore-prefixed): integrations/asset_registry_data.py
# reads clean.csv/rejected.csv back out of this same directory for the
# asset registry screen, so it needs this default too, rather than
# hard-coding a second copy of it.
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "normalization_output"

_SOURCE_FIELDNAMES = [
    "asset_tag",
    "building",
    "unit_id",
    "install_date",
    "status",
    "last_service_cost",
    "technician",
    "notes",
]
_REJECTED_FIELDNAMES = _SOURCE_FIELDNAMES + ["reason"]


@dataclass(frozen=True)
class PipelineSummary:
    total_rows_read: int
    clean_rows_written: int
    rejected_rows: int
    duplicate_groups_resolved: int  # asset_tags that had more than one row


def run_pipeline(
    db: Session,
    source_path: Optional[Union[str, Path]] = None,
    output_dir: Optional[Union[str, Path]] = None,
) -> PipelineSummary:
    """Runs the full Part 1 process against `source_path` (default:
    sample-data/messy-asset-registry.csv) and writes clean.csv +
    rejected.csv into `output_dir` (default: normalization_output/ at the
    project root — not committed; a developer running this locally should
    gitignore it).

    `db` is threaded through to entity_resolution.resolve_building_name()
    and dedupe.resolve_duplicate_group(), which use it to write audit_log
    entries for automatic merges and dropped-duplicate decisions — see
    their own docstrings for exactly what gets logged and why.
    """
    source_path = Path(source_path) if source_path is not None else _DEFAULT_SOURCE_PATH
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_rows = _read_source_rows(source_path)

    known_buildings: set[str] = set()
    groups: dict[str, list[dict[str, Any]]] = {}
    rejected_rows: list[dict[str, Any]] = []

    for raw_row in raw_rows:
        cleaned, reason = _clean_row(raw_row)
        if cleaned is None:
            rejected_rows.append({**raw_row, "reason": reason})
            continue

        if cleaned["building"] is not None:
            cleaned["building"] = resolve_building_name(cleaned["building"], known_buildings, db)

        groups.setdefault(cleaned["asset_tag"], []).append(cleaned)

    clean_rows: list[dict[str, Any]] = []
    duplicate_groups_resolved = 0
    for group in groups.values():
        if len(group) > 1:
            duplicate_groups_resolved += 1
            survivor = resolve_duplicate_group(group, db)
        else:
            survivor = group[0]
        clean_rows.append(survivor)

    _write_csv(output_dir / "clean.csv", clean_rows, _SOURCE_FIELDNAMES)
    _write_csv(output_dir / "rejected.csv", rejected_rows, _REJECTED_FIELDNAMES)

    return PipelineSummary(
        total_rows_read=len(raw_rows),
        clean_rows_written=len(clean_rows),
        rejected_rows=len(rejected_rows),
        duplicate_groups_resolved=duplicate_groups_resolved,
    )


def _read_source_rows(source_path: Path) -> list[dict[str, str]]:
    # utf-8-sig strips a leading BOM at decode time (the "or utf-8-sig
    # encoding" option for handling it) — messy-asset-registry.csv starts
    # with one, so this is what keeps it from ending up glued onto the
    # first row's asset_tag value. rules.strip_bom() stays independently
    # available (and tested) for a caller that decodes a source with plain
    # `utf-8` instead and needs to strip the BOM from the resulting text
    # itself; it isn't separately invoked here because utf-8-sig already
    # prevents the BOM from ever reaching field values in the first place.
    with source_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _clean_text(raw: Optional[str]) -> Optional[str]:
    """Composes the three rules.py rules that apply to every free-text
    field: dash normalization, then the null-token check, then (for
    whatever isn't a null token) trailing-whitespace trimming. Order
    between the first two doesn't matter — neither's outcome depends on
    the other — but null-checking has to happen before trailing-whitespace
    trimming is applied, since a value that turned out to be a null token
    has nothing left to trim.
    """
    if raw is None:
        return None
    value = normalize_dashes(raw)
    value = normalize_null_token(value)
    if value is None:
        return None
    return strip_trailing_whitespace(value)


def _clean_row(raw_row: dict[str, str]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Returns (cleaned_row, None) on success, or (None, reason) if this
    row is rejected. See the module docstring for the two rejection
    conditions.

    `status` is intentionally passed through _clean_text with no case
    normalization: rules.py doesn't define a general-purpose case rule (its
    six rules are date/currency/BOM/dashes/trailing-whitespace/null-token),
    and this module's job is to compose the rules that exist, not add new
    ones — so "ACTIVE" / "Active" / "active" remain distinct values here.
    """
    asset_tag = _clean_text(raw_row.get("asset_tag"))
    if not asset_tag:
        return None, "missing asset_tag"

    try:
        install_date = normalize_date(raw_row.get("install_date"))
    except ValueError as exc:
        return None, str(exc)

    try:
        last_service_cost = normalize_currency(raw_row.get("last_service_cost"))
    except ValueError as exc:
        return None, str(exc)

    cleaned: dict[str, Any] = {
        "asset_tag": asset_tag,
        "building": _clean_text(raw_row.get("building")),
        "unit_id": _clean_text(raw_row.get("unit_id")),
        "install_date": install_date,
        "status": _clean_text(raw_row.get("status")),
        "last_service_cost": last_service_cost,
        "technician": _clean_text(raw_row.get("technician")),
        "notes": _clean_text(raw_row.get("notes")),
    }
    return cleaned, None


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01")))
    return str(value)


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _csv_value(row.get(name)) for name in fieldnames})
