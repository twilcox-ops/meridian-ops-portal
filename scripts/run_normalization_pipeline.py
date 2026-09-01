"""One-off entrypoint: runs the Part 1 normalization pipeline
(normalization/pipeline.py's run_pipeline()) against the real
sample-data/messy-asset-registry.csv and writes clean.csv + rejected.csv to
normalization_output/ at the project root (gitignored — not committed,
same as deploy/main.json).

Usage:
    python scripts/run_normalization_pipeline.py

run_pipeline() threads a db Session through to entity_resolution.py and
dedupe.py, which use it only to write audit_log rows for automatic merges
and dropped-duplicate decisions (see pipeline.py's own docstring). This
script's job is to prove the pipeline runs correctly against the real
file, not to populate dev's real sqlite:///./local.db, so it opens its own
throwaway in-memory SQLite session rather than touching that database.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ops_portal.db.base import Base
from ops_portal.normalization.pipeline import run_pipeline


def main() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    db = session_factory()
    try:
        summary = run_pipeline(db)
        db.commit()
    finally:
        db.close()

    print(f"total_rows_read:           {summary.total_rows_read}")
    print(f"clean_rows_written:        {summary.clean_rows_written}")
    print(f"rejected_rows:             {summary.rejected_rows}")
    print(f"duplicate_groups_resolved: {summary.duplicate_groups_resolved}")


if __name__ == "__main__":
    main()
