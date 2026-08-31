"""Deduplication with a stated survivorship rule.

~8% of messy-asset-registry.csv's rows are near-duplicates: the same
asset_tag appears more than once, with the rows agreeing on almost every
field and differing in one — e.g. one copy spells the building
"Sable Point Medical" and the other "Sable Point Med Ctr", or one has a
trailing space in `notes` that survived as a distinct string. This module
picks the single row that survives out of each such group and records why.

Survivorship rule, applied in order until one candidate is left:

1. Fewest missing fields wins. A row's completeness is the count of
   non-None field values; the candidate with the fewest `None`s advances.
   Rationale: a more complete row carries strictly more information
   forward with no added risk — nothing is lost that the more complete
   row didn't already have, whereas keeping the sparser row would silently
   drop data the fuller row recorded. This function only ever checks
   `is None`, not empty strings or other null spellings: it assumes rows
   have already passed through normalization/rules.py's
   normalize_null_token (and, for `install_date` to be comparable at all
   in rule 2 below, normalize_date), keeping this function focused on one
   job — picking a winner — rather than re-doing null/date normalization
   itself.
2. If completeness ties, the later `install_date` wins (ISO 8601 strings
   compare correctly as plain strings, so this is a lexicographic
   comparison, not a parsed-date one). Rationale: this is a real
   assumption, not a certainty — the sample data doesn't carry a
   "when was this row captured" timestamp, so `install_date` is the only
   date-shaped signal available, and a later value on an otherwise-tied
   duplicate is read as "this copy was re-entered or corrected more
   recently." A row missing `install_date` entirely always loses this
   tie-break against one that has a value.
3. If every field ties (including `install_date`, or both are absent),
   the first-seen row wins — whichever candidate appears earliest in the
   input list, not whatever order a set or dict would happen to iterate
   in. This matches entity_resolution.py's "first spelling seen survives"
   convention and guarantees a deterministic result: given the same input
   list, this function always picks the same survivor.

This does not merge fields across candidates into a synthesized row —
exactly one input row survives, unchanged, matching "returns the single
row that survives" rather than reconstructing a new one that was never
actually seen in the source data.

Every row that loses (candidates minus the one survivor) is recorded to
the audit log via services/audit.py's log_change():
action="deduplication.dropped", entity_type="asset_row", before=the
dropped row's full data, after=the surviving row's asset_tag, actor_id=
None (a system decision, not a human one). A group with only one
candidate has nothing to drop, so it writes nothing — resolving a group of
one isn't a deduplication decision, there's no alternative it was chosen
over.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..services.audit import log_change


def _missing_count(row: dict[str, Any]) -> int:
    return sum(1 for value in row.values() if value is None)


def _is_better(candidate: dict[str, Any], current_best: dict[str, Any]) -> bool:
    """True if `candidate` should replace `current_best` as the survivor,
    per the three-step rule documented at the top of this module. Uses
    strict `<`/`>` throughout (never `<=`/`>=`), so a full tie always
    returns False and `current_best` — which, by construction, is always
    the earliest-seen row among those tied so far — keeps the title. That
    is what makes rule 3 (first-seen wins) fall out of this comparison
    automatically, without a separate index check.
    """
    candidate_missing = _missing_count(candidate)
    best_missing = _missing_count(current_best)
    if candidate_missing != best_missing:
        return candidate_missing < best_missing

    candidate_date = candidate.get("install_date") or ""
    best_date = current_best.get("install_date") or ""
    if candidate_date != best_date:
        return candidate_date > best_date

    return False


def resolve_duplicate_group(candidates: list[dict[str, Any]], db: Session) -> dict[str, Any]:
    """Picks the single surviving row from `candidates` — every row in the
    list must share the same `asset_tag` (this is checked; a mixed group
    raises ValueError, since resolving duplicates across different assets
    would silently discard a legitimately distinct asset's data).

    Logs one audit_log entry per dropped row (see the module docstring for
    the exact fields) and returns the survivor.
    """
    if not candidates:
        raise ValueError("resolve_duplicate_group requires at least one candidate row")

    asset_tag = candidates[0].get("asset_tag")
    if any(row.get("asset_tag") != asset_tag for row in candidates):
        raise ValueError("all candidate rows must share the same asset_tag")

    survivor_index = 0
    for index in range(1, len(candidates)):
        if _is_better(candidates[index], candidates[survivor_index]):
            survivor_index = index
    survivor = candidates[survivor_index]

    for index, row in enumerate(candidates):
        if index == survivor_index:
            continue
        log_change(
            db,
            action="deduplication.dropped",
            entity_type="asset_row",
            before=row,
            after=survivor["asset_tag"],
            actor_id=None,
        )

    return survivor
