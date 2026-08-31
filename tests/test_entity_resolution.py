"""Same building under multiple spellings -> one resolved canonical name,
with every automatic merge recorded to the audit log.

Why deterministic normalized-key matching instead of fuzzy/similarity
matching (consistent with normalization/rules.py's "no LLM, deterministic
solution" stance): the source data's building-name defects are a fixed,
enumerable list — casing, stray whitespace, a handful of building-suffix
abbreviations — so a deterministic rule covers all of them without a
similarity threshold to tune. A fuzzy matcher (edit distance, token
overlap, embeddings) could also catch defects outside that list, but at
the cost of a tuning knob that can just as easily merge two genuinely
different buildings whose names happen to be similar — a false merge is
worse for an asset registry than a near-duplicate name that stays split,
since the latter is at least visible and correctable, while the former
silently loses the distinction between two real assets. See
entity_resolution.py's module docstring for the full rationale, including
why the abbreviation table itself is scoped narrowly (building-suffix
words only, not the more generic street/address abbreviations a fuzzier
or broader table might have included) and the resulting, deliberately
accepted gap in the sample data ("Sable Point Med Ctr" / "Sable Point
Medical" / "Sable Pt Medical" now stay three distinct entities).

Every test here uses the shared `db_session` fixture from conftest.py (an
isolated in-memory SQLite database) since resolve_building_name() now
writes to audit_log as part of resolving a merge.
"""
from __future__ import annotations

from ops_portal.db.models import AuditLog
from ops_portal.normalization.entity_resolution import resolve_building_name

HARBORVIEW_SPELLINGS = [
    "Harborview Tower",
    "Harborview Twr",
    "HARBORVIEW TOWER",
    "Harborview  Tower",  # double space
]


def test_all_harborview_spelling_variants_resolve_to_the_same_canonical_name(db_session):
    known_buildings: set[str] = set()

    resolved = [resolve_building_name(spelling, known_buildings, db_session) for spelling in HARBORVIEW_SPELLINGS]

    assert len(set(resolved)) == 1
    # The first spelling seen becomes canonical; later variants resolve to it.
    assert resolved[0] == "Harborview Tower"
    assert all(name == "Harborview Tower" for name in resolved)
    assert known_buildings == {"Harborview Tower"}


def test_two_genuinely_different_buildings_remain_distinct(db_session):
    known_buildings: set[str] = set()

    harborview = resolve_building_name("Harborview Tower", known_buildings, db_session)
    kestrel = resolve_building_name("Kestrel Plaza", known_buildings, db_session)

    assert harborview != kestrel
    assert known_buildings == {"Harborview Tower", "Kestrel Plaza"}


def test_case_and_whitespace_variants_alone_still_match_without_an_abbreviation(db_session):
    known_buildings: set[str] = set()

    resolve_building_name("Kestrel Plaza", known_buildings, db_session)
    resolved = resolve_building_name("  kestrel   plaza ", known_buildings, db_session)

    assert resolved == "Kestrel Plaza"
    assert known_buildings == {"Kestrel Plaza"}


def test_abbreviation_expansion_matches_a_full_word_spelling(db_session):
    known_buildings: set[str] = set()

    resolve_building_name("Alder Commons", known_buildings, db_session)
    resolved = resolve_building_name("Alder Cmns", known_buildings, db_session)

    assert resolved == "Alder Commons"


def test_a_later_variant_never_displaces_the_first_seen_canonical_spelling(db_session):
    known_buildings: set[str] = set()

    first = resolve_building_name("HARBORVIEW TOWER", known_buildings, db_session)
    second = resolve_building_name("Harborview Tower", known_buildings, db_session)

    assert first == second == "HARBORVIEW TOWER"
    assert known_buildings == {"HARBORVIEW TOWER"}


# --- audit logging -----------------------------------------------------------


def test_a_merge_writes_exactly_one_audit_log_row(db_session):
    known_buildings: set[str] = set()
    resolve_building_name("Harborview Tower", known_buildings, db_session)  # first sighting, no merge

    resolve_building_name("Harborview Twr", known_buildings, db_session)  # merges into the above

    entries = db_session.query(AuditLog).all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "entity_resolution.merged"
    assert entry.entity_type == "building_name"
    assert entry.before == "Harborview Twr"
    assert entry.after == "Harborview Tower"
    assert entry.actor_id is None  # a system action, not a human one


def test_registering_a_brand_new_building_writes_no_audit_log_row(db_session):
    known_buildings: set[str] = set()

    resolve_building_name("Harborview Tower", known_buildings, db_session)

    assert db_session.query(AuditLog).count() == 0
