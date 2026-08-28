"""Append-only audit log writer, called by every mutating action.

TODO: record actor, timestamp, what, before, after for every state change.
Insert-only — no update/delete path exposed here at all.
"""
