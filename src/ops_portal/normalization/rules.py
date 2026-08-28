"""Part 1 transformation rules — one explicit, unit-tested function per rule.

TODO: date-format normalization (five formats incl. two-digit years),
currency parsing (incl. the `(1,234.56)` parenthesized-negative case —
get this right, it's the one people miss), BOM stripping, non-breaking
hyphen / em dash normalization, trailing-whitespace trimming, and the
various "no value" spellings (``, `  `, `n/a`, `N/A`, `NULL`) -> null.

No LLM here. Deterministic problem, deterministic solution.
"""
