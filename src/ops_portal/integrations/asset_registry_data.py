"""Read-only adapter onto normalization/pipeline.py's own output.

Reads clean.csv and rejected.csv back out of the pipeline's output
directory (same default as normalization/pipeline.py's DEFAULT_OUTPUT_DIR)
for the asset registry screen. This is a different kind of "integration"
than project1_ingestion.py and friends: Part 1 (the asset-registry
normalization pipeline) is part of this project, not a different one — but
the same reasoning still applies, that routers/asset_registry.py shouldn't
know anything about file paths, CSV parsing, or the search rule, just call
a function and get rows back.

Never writes to either file — normalization/pipeline.py is the only thing
that produces them. If the pipeline hasn't been run yet (neither file
exists), read_asset_registry_data() returns an empty, `available=False`
result rather than raising — the same "degrade to placeholder data, don't
500" stance integrations/project1_ingestion.py takes toward its own
optional, possibly-absent upstream.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..normalization.pipeline import DEFAULT_OUTPUT_DIR


@dataclass(frozen=True)
class AssetRow:
    asset_tag: str
    building: Optional[str]
    unit_id: Optional[str]
    install_date: Optional[str]
    status: Optional[str]
    last_service_cost: Optional[str]
    technician: Optional[str]
    notes: Optional[str]


@dataclass(frozen=True)
class RejectedAssetRow:
    asset_tag: Optional[str]
    building: Optional[str]
    unit_id: Optional[str]
    install_date: Optional[str]
    status: Optional[str]
    last_service_cost: Optional[str]
    technician: Optional[str]
    notes: Optional[str]
    reason: str


@dataclass(frozen=True)
class AssetRegistryData:
    available: bool  # False: clean.csv doesn't exist yet — the pipeline hasn't run
    clean_rows: list[AssetRow]
    rejected_rows: list[RejectedAssetRow]


def _read_csv_rows(path: Path) -> tuple[bool, list[dict[str, str]]]:
    if not path.is_file():
        return False, []
    with path.open("r", encoding="utf-8", newline="") as f:
        return True, list(csv.DictReader(f))


def _asset_row_from_csv_row(row: dict[str, str]) -> AssetRow:
    return AssetRow(
        asset_tag=row.get("asset_tag", ""),
        building=row.get("building") or None,
        unit_id=row.get("unit_id") or None,
        install_date=row.get("install_date") or None,
        status=row.get("status") or None,
        last_service_cost=row.get("last_service_cost") or None,
        technician=row.get("technician") or None,
        notes=row.get("notes") or None,
    )


def _rejected_row_from_csv_row(row: dict[str, str]) -> RejectedAssetRow:
    return RejectedAssetRow(
        asset_tag=row.get("asset_tag") or None,
        building=row.get("building") or None,
        unit_id=row.get("unit_id") or None,
        install_date=row.get("install_date") or None,
        status=row.get("status") or None,
        last_service_cost=row.get("last_service_cost") or None,
        technician=row.get("technician") or None,
        notes=row.get("notes") or None,
        reason=row.get("reason", ""),
    )


def read_asset_registry_data(output_dir: Optional[Path] = None) -> AssetRegistryData:
    """Reads both CSVs from `output_dir` (default: DEFAULT_OUTPUT_DIR,
    looked up at call time so tests can monkeypatch that module attribute
    rather than needing to pass output_dir through on every call).
    Returns unfiltered rows — see filter_clean_rows() for the search box's
    asset_tag/building substring match, applied separately so it stays
    independently testable.
    """
    resolved_output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR

    clean_available, clean_raw_rows = _read_csv_rows(resolved_output_dir / "clean.csv")
    _, rejected_raw_rows = _read_csv_rows(resolved_output_dir / "rejected.csv")

    return AssetRegistryData(
        available=clean_available,
        clean_rows=[_asset_row_from_csv_row(row) for row in clean_raw_rows],
        rejected_rows=[_rejected_row_from_csv_row(row) for row in rejected_raw_rows],
    )


def filter_clean_rows(rows: list[AssetRow], query: Optional[str]) -> list[AssetRow]:
    """Case-insensitive substring match against `asset_tag` or `building`.
    A blank, whitespace-only, or absent query returns every row
    unchanged — an empty search box means "show everything," not "show
    nothing."
    """
    if query is None or not query.strip():
        return rows

    needle = query.strip().casefold()
    return [
        row
        for row in rows
        if needle in row.asset_tag.casefold() or (row.building is not None and needle in row.building.casefold())
    ]
