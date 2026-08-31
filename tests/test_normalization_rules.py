"""One test per transformation rule in normalization/rules.py, plus edge
cases for each. The parenthesized-negative currency case gets its own
dedicated test — `(1,234.56)` -> -1234.56, not a parse error — per the
project's explicit "this is the one people miss" warning.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from ops_portal.normalization.rules import (
    normalize_currency,
    normalize_dashes,
    normalize_date,
    normalize_null_token,
    strip_bom,
    strip_trailing_whitespace,
)

# --- 1. normalize_date -------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2022-09-09", "2022-09-09"),  # already ISO
        ("04/02/2022", "2022-04-02"),  # MM/DD/YYYY
        ("07/24/23", "2023-07-24"),  # MM/DD/YY, two-digit year
        ("24-Dec-2023", "2023-12-24"),  # DD-Mon-YYYY
        ("February 14, 2023", "2023-02-14"),  # Month DD, YYYY
        ("July 03, 2022", "2022-07-03"),  # Month DD, YYYY, zero-padded day
    ],
)
def test_normalize_date_handles_all_five_source_formats(raw, expected):
    assert normalize_date(raw) == expected


def test_normalize_date_two_digit_year_windows_into_the_2000s():
    # %y windowing: 00-68 -> 2000-2068. Every two-digit year in the sample
    # data (19-25) falls here, so "23" means 2023, not 1923.
    assert normalize_date("06/16/25") == "2025-06-16"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_normalize_date_blank_is_none(blank):
    assert normalize_date(blank) is None


def test_normalize_date_unrecognized_format_raises_value_error():
    with pytest.raises(ValueError, match="unrecognized date format"):
        normalize_date("Sept 2022")


# --- 2. normalize_currency ---------------------------------------------------


def test_normalize_currency_plain_number():
    assert normalize_currency("1234.56") == Decimal("1234.56")


def test_normalize_currency_dollar_prefixed_with_thousands_separator():
    assert normalize_currency("$1,234.56") == Decimal("1234.56")


def test_normalize_currency_thousands_separator_without_dollar_sign():
    assert normalize_currency("1,234.56") == Decimal("1234.56")


def test_normalize_currency_parenthesized_value_is_a_negative_number():
    """The one to get right: "(1,234.56)" is accounting notation for a
    negative number, not malformed input that should fail to parse."""
    assert normalize_currency("(1,234.56)") == Decimal("-1234.56")


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_normalize_currency_blank_is_none(blank):
    assert normalize_currency(blank) is None


def test_normalize_currency_unparseable_text_raises_value_error():
    with pytest.raises(ValueError, match="unparseable currency value"):
        normalize_currency("not a number")


# --- 3. strip_bom -------------------------------------------------------------


def test_strip_bom_removes_a_leading_bom():
    assert strip_bom("﻿asset_tag") == "asset_tag"


def test_strip_bom_leaves_a_string_without_a_bom_unchanged():
    assert strip_bom("asset_tag") == "asset_tag"


# --- 4. normalize_dashes ------------------------------------------------------


def test_normalize_dashes_converts_non_breaking_hyphen():
    assert normalize_dashes("off‑hours") == "off-hours"


def test_normalize_dashes_converts_em_dash():
    assert normalize_dashes("Deferred — parts backorder") == "Deferred - parts backorder"


def test_normalize_dashes_leaves_ascii_hyphens_unchanged():
    assert normalize_dashes("MES-2026-4100") == "MES-2026-4100"


# --- 5. strip_trailing_whitespace --------------------------------------------


def test_strip_trailing_whitespace_makes_active_and_active_space_equal():
    assert strip_trailing_whitespace("Active") == strip_trailing_whitespace("Active ")
    assert strip_trailing_whitespace("Active ") == "Active"


def test_strip_trailing_whitespace_leaves_leading_whitespace_untouched():
    # Scoped exactly to trailing whitespace, per the rule's name — leading
    # whitespace is a different defect this rule doesn't claim to fix.
    assert strip_trailing_whitespace(" Active ") == " Active"


# --- 6. normalize_null_token --------------------------------------------------


@pytest.mark.parametrize("token", ["", "  ", "n/a", "N/A", "NULL", "Null", "null"])
def test_normalize_null_token_maps_known_spellings_to_none(token):
    assert normalize_null_token(token) is None


def test_normalize_null_token_none_input_stays_none():
    assert normalize_null_token(None) is None


def test_normalize_null_token_passes_through_a_real_value_unchanged():
    assert normalize_null_token("OK") == "OK"
