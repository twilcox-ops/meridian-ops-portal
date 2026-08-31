"""Building-name entity resolution.

The source data has the same building under multiple spellings — casing
("HARBORVIEW TOWER" vs "Harborview Tower"), stray whitespace
("Harborview  Tower", a double space), and abbreviations ("Harborview
Twr"). resolve_building_name() collapses all of these to one canonical
name per building, and records every automatic merge to the audit log.

Matching rule (exact, not fuzzy): two raw strings are the same building
if and only if they produce the same *normalized key* —
case-folded, whitespace-collapsed, then each word expanded against a
fixed abbreviation table (see _ABBREVIATIONS) — compared for exact
equality. There is no similarity threshold, edit distance, or scoring
involved anywhere in this rule.

That's a deliberate choice, for the same reason normalization/rules.py
reaches for explicit functions instead of a model: a fixed set of known
defects (case, whitespace, a short abbreviation list) has a fixed set of
correct fixes, and exact-key matching after applying them is deterministic,
fast, and trivially unit-testable — the same input always resolves the
same way, and there's no probability threshold to tune or justify.
Similarity-based fuzzy matching (edit distance, token overlap, embeddings)
trades that determinism for the ability to also catch defects outside the
known list — at the cost of a tuning knob that can just as easily merge two
genuinely different buildings whose names happen to be similar (a false
merge, which is far more damaging to an asset registry than a
near-duplicate that stays split — see dedupe.py for the separate concern
of collapsing duplicate rows within one already-resolved building). Given
that this data's actual defects are a short, enumerable list, the
deterministic rule covers what's really there without accepting that risk.

_ABBREVIATIONS is deliberately scoped to unambiguous building *suffix*
words only — twr/plz/cmns/ctr/bldg/apts are, in this kind of data,
essentially always shorthand for the building-type word they expand to.
Street/address abbreviations (st, ave, blvd) are excluded even though
they're common in real-estate data generally, because they're not
building-name abbreviations at all and don't appear in this column.
"pt" and "med" are excluded for a sharper reason: they're too generic to
expand safely as a blanket rule. "St" commonly means "Saint" in a proper
name, not "Street" — expanding it would actively corrupt names it wasn't
meant to touch — and "Pt"/"Med" are common enough as parts of unrelated
words or abbreviations that treating them as always meaning
"Point"/"Medical" risks the same kind of wrong, silent merge that ruling
out fuzzy matching was meant to avoid in the first place. Every entry that
remains is a suffix that, once separated by whitespace, has no other
plausible expansion in this domain.

Known limitation of this narrower table, visible in the sample data
itself: "Sable Point Med Ctr", "Sable Point Medical", and "Sable Pt
Medical" are almost certainly the same building, but none of them share a
normalized key under this rule — "Ctr" expands to "Center" (an extra token
the other two lack), and "Pt"/"Med" are no longer expanded at all, so
"Point" and "Pt", and "Medical" and "Med", are treated as different words.
All three stay separate canonical entities. That's the direct cost of
excluding "pt"/"med" from the table above, accepted deliberately: an
under-merge here is a visible, correctable gap (a human can add a
synonym-list entry through the review workflow once they see three
similarly-named entries), where the alternative — auto-expanding
generic-enough word fragments — risks an over-merge that's much harder to
even notice happened.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..services.audit import log_change

# Unambiguous building-suffix abbreviations only — see the module
# docstring for why st/ave/blvd (street abbreviations, not building-name
# abbreviations) and pt/med (too generic to expand safely) are excluded.
# Keys are compared case-insensitively (see _normalized_key, which
# casefolds every word before this lookup).
_ABBREVIATIONS: dict[str, str] = {
    "twr": "tower",
    "plz": "plaza",
    "cmns": "commons",
    "ctr": "center",
    "bldg": "building",
    "apts": "apartments",
}


def _normalized_key(raw: str) -> str:
    """Case-folds, collapses whitespace, and expands known abbreviations,
    word by word. `str.split()` with no arguments both strips the ends and
    collapses any run of whitespace (including the sample data's double
    space) into single word boundaries, which is what makes the simple
    `" ".join(...)` below produce single-space-separated output.
    """
    words = raw.split()
    expanded = [_ABBREVIATIONS.get(word.casefold(), word.casefold()) for word in words]
    return " ".join(expanded)


def resolve_building_name(raw: str, known_buildings: set[str], db: Session) -> str:
    """Resolves `raw` to a canonical building name against `known_buildings`.

    If `raw`'s normalized key (see _normalized_key and the matching rule
    documented at the top of this module) matches an existing entry's
    normalized key, that existing entry is returned unchanged — the first
    spelling seen for a building is the one that survives, later variants
    just resolve to it rather than displacing it — and this automatic
    merge is recorded to the audit log via services/audit.py's
    log_change(): action="entity_resolution.merged", entity_type=
    "building_name", before=`raw`, after=the canonical name, actor_id=None
    (a system action, not a human one). This is what makes an automatic
    merge traceable rather than silent: anyone can later ask "why did these
    648 rows collapse to only a handful of buildings" and get an answer
    from the append-only trail instead of having to trust the code.

    If no existing entry matches, `raw` itself (verbatim — not
    case-folded, not whitespace-collapsed) is added to `known_buildings`
    and returned as the new canonical name for that building. Nothing is
    written to the audit log in this case — registering the first-seen
    spelling of a building isn't a merge decision, it's just noting a new
    entity exists, so there's nothing yet to explain.

    Mutates `known_buildings` in place: the set is the running registry a
    caller (normalization/pipeline.py, not built yet) is expected to build
    up by calling this function once per row in source order, so the
    registry reflects every canonical name seen so far on every subsequent
    call. That's a deliberate departure from rules.py's pure,
    side-effect-free functions — resolving an entity against a growing
    registry, and logging the decision, is inherently stateful in a way a
    row-independent value transformation isn't.

    Recomputes every existing entry's normalized key on every call, so this
    is O(known buildings) per row. Fine at this data's scale (a few dozen
    distinct buildings at most); a version fronting a much larger registry
    would instead maintain a persistent {normalized_key: canonical_name}
    index alongside the set, updated incrementally instead of recomputed.
    """
    key = _normalized_key(raw)
    for existing in known_buildings:
        if _normalized_key(existing) == key:
            log_change(
                db,
                action="entity_resolution.merged",
                entity_type="building_name",
                before=raw,
                after=existing,
                actor_id=None,
            )
            return existing
    known_buildings.add(raw)
    return raw
