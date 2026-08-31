"""Part 1 transformation rules — one explicit, unit-tested function per rule.

Six rules, each a pure function (input -> output, no side effects, no I/O):
normalize_date, normalize_currency, strip_bom, normalize_dashes,
strip_trailing_whitespace, and normalize_null_token. normalization/
pipeline.py (not built yet) is what will compose these over
sample-data/messy-asset-registry.csv's columns; entity resolution and
deduplication are separate concerns living in entity_resolution.py and
dedupe.py.

No LLM here. This is a deterministic problem — five known date shapes, one
currency notation, a fixed set of null spellings — so a deterministic,
unit-tested function is cheaper, faster, and can't drift the way a model
call could. Reaching for a model on a problem this well-specified would be
the wrong instinct, not a shortcut.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

# --- 1. Date normalization -------------------------------------------------

# Ordered by shape, not frequency — each pattern is structurally exclusive
# of the others (digits-only with dashes vs. slashes vs. letters), so the
# first (and only) match determines which strptime format applies. This
# sidesteps the ambiguity a blind sequence of strptime attempts would hit:
# "%m/%d/%Y" applied to "07/24/23" would happily parse "23" as the (wrong)
# 2-digit year, so the year's digit count has to pick the format, not the
# other way around.
_DATE_FORMATS_BY_SHAPE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),  # 2022-09-09
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"), "%m/%d/%Y"),  # 04/02/2022
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{2}$"), "%m/%d/%y"),  # 07/24/23
    (re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$"), "%d-%b-%Y"),  # 24-Dec-2023
    (re.compile(r"^[A-Za-z]+\s+\d{1,2},\s*\d{4}$"), "%B %d, %Y"),  # February 14, 2023
]


def normalize_date(raw: Optional[str]) -> Optional[str]:
    """Parses any of the five date shapes found in messy-asset-registry.csv's
    `install_date` column into a single canonical ISO 8601 date
    ("YYYY-MM-DD").

    Two-digit years (`"07/24/23"`) use Python's standard strptime `%y`
    windowing: 00-68 -> 2000-2068, 69-99 -> 1969-1999. Every two-digit year
    actually present in the sample data (19, 20, 21, 23, 25) falls in
    00-68, so this always resolves to the intended 2019-2025 range here.

    A blank or whitespace-only value normalizes to None (no date recorded),
    matching normalize_null_token's convention. Anything non-blank that
    doesn't match one of the five known shapes raises ValueError with the
    offending value, so a caller composing this into a pipeline can catch
    it and route the row to the rejection report with that message as the
    reason, rather than silently guessing.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    for pattern, fmt in _DATE_FORMATS_BY_SHAPE:
        if pattern.match(value):
            return datetime.strptime(value, fmt).date().isoformat()

    raise ValueError(f"unrecognized date format: {raw!r}")


# --- 2. Currency parsing -----------------------------------------------------


def normalize_currency(raw: Optional[str]) -> Optional[Decimal]:
    """Parses a currency string into a Decimal amount (Decimal, not float,
    so downstream arithmetic and storage — db/models.py's
    `Numeric(12, 2)` — never pick up binary floating-point rounding error).

    Handles:
      - plain numbers: "1234.56" -> Decimal("1234.56")
      - thousands separators and/or a dollar sign: "1,234.56", "$1,234.56"
        -> Decimal("1234.56")
      - parenthesized negatives: "(1,234.56)" -> Decimal("-1234.56"). This
        is accounting notation for a negative number, not malformed text —
        the one case that's easy to get wrong, either by failing to parse
        parens at all, or by stripping them and losing the sign, which
        silently turns a negative into a positive rather than raising.
      - blank/whitespace-only -> None (no value recorded)

    Anything else — text that isn't a number once parens/`$`/commas are
    accounted for — raises ValueError with the offending value.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1].strip()

    value = value.lstrip("$").strip().replace(",", "")

    if not value:
        raise ValueError(f"unparseable currency value: {raw!r}")

    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"unparseable currency value: {raw!r}") from exc

    return -amount if negative else amount


# --- 3. BOM stripping --------------------------------------------------------

_UTF8_BOM = "﻿"


def strip_bom(value: str) -> str:
    """Removes a leading UTF-8 byte-order-mark (U+FEFF) from a string, if
    present.

    This is the character a plain `utf-8` decode of
    sample-data/messy-asset-registry.csv's first bytes produces (the file
    starts with the 3-byte BOM `EF BB BF`); decoding with `utf-8-sig`
    instead strips it during decoding, which would make this a no-op on
    already-clean input — safe either way, since a string without a
    leading BOM is returned unchanged.
    """
    return value[len(_UTF8_BOM):] if value.startswith(_UTF8_BOM) else value


# --- 4. Non-breaking hyphen / em dash normalization -------------------------

_DASH_LIKE_CHARACTERS = {
    "‑": "-",  # non-breaking hyphen, e.g. "off‑hours"
    "—": "-",  # em dash, e.g. "Deferred — parts backorder"
}


def normalize_dashes(value: str) -> str:
    """Replaces non-breaking hyphens (U+2011) and em dashes (U+2014) with a
    standard ASCII hyphen (U+002D), so text that differs only by which
    dash-like character a source system happened to use compares equal.
    """
    for dash_like_char, ascii_hyphen in _DASH_LIKE_CHARACTERS.items():
        value = value.replace(dash_like_char, ascii_hyphen)
    return value


# --- 5. Trailing whitespace trimming ----------------------------------------


def strip_trailing_whitespace(value: str) -> str:
    """Strips trailing whitespace so "Active" and "Active " — otherwise
    indistinguishable in a rendered table, but unequal as strings — compare
    and group as the same value.
    """
    return value.rstrip()


# --- 6. Null-value normalization ---------------------------------------------

# Compared case-insensitively against the value after stripping whitespace,
# so "", "  ", "n/a", "N/A", "NULL", and "Null" (etc.) all match.
_NULL_TOKENS = {"", "n/a", "null"}


def normalize_null_token(value: Optional[str]) -> Optional[str]:
    """Maps every "no value" spelling found in the source data — an empty
    string, a whitespace-only string, "n/a", "N/A", "NULL" (any casing) —
    to a real `None`, so downstream code has exactly one way to test for
    "no value" instead of six different strings to remember and compare
    against.

    A value that isn't one of those spellings is returned unchanged
    (untrimmed) — this rule only classifies "is this a null token," it
    doesn't also do the job of strip_trailing_whitespace.
    """
    if value is None:
        return None
    if value.strip().lower() in _NULL_TOKENS:
        return None
    return value
