"""Orchestrates rules.py + entity_resolution.py + dedupe.py.

TODO: read sample-data/messy-asset-registry.csv -> apply rules -> resolve
entities -> dedupe -> write clean.csv, rejected.csv (with a reason per
row — never silently dropped), and a summary. Must be idempotent: run it
twice, same output.
"""
