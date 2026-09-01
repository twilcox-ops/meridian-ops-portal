# Meridian Ops Portal (Project 5 — Capstone)

A single ops portal that pulls together three things a facilities team would otherwise juggle separately: a pipeline that cleans up a messy asset-registry CSV, a queue for reviewing and correcting flagged records, and a screen for triaging tickets. It sits behind Entra ID login, enforces who can approve what, and keeps an audit trail of every change — so a viewer can look but not touch, and nothing gets changed without a second person signing off.

## What's built and verified

The normalization pipeline (dates, currency, dashes, null tokens, dedup, entity resolution) is done and covered by tests — 97 tests pass, including idempotency (running it twice gives byte-identical output). It has **not** yet been run against the real sample-data CSV; everything so far has been validated against synthetic test fixtures, not the actual file.

The portal itself is up: Entra ID SSO, role-based access (viewer vs. approver enforced server-side, not just hidden in the UI), all four screens (dashboard, asset registry, review queue, ticket triage), a maker-checker approval flow (you can't approve your own request), and an append-only audit log recording who did what.

Docker is built and has been run-verified locally — the multi-stage image builds and the app comes up in a container.

The Bicep IaC (Container App, Postgres, Key Vault, managed identity) compiles clean. It has not been deployed anywhere.

## What's not done yet

There's no CI/CD. `.github/workflows/deploy.yml` exists but it's a stub — checkout only, no build/test/deploy steps wired up.

The pipeline hasn't been run against the real sample-data CSV. Test coverage is against fixtures, not production-shaped data, so there could still be a format or edge case the tests don't catch.

Nothing is deployed to Azure. The Bicep templates exist and compile, but no Container App, database, or Key Vault has actually been stood up.

## Setup

Requires Python 3.11+.

```bash
# install the package and dev dependencies (pytest, httpx)
pip install -e ".[dev]"

# run the test suite
pytest -v
```

To run the app locally, you need a `.env` file (copy `.env.example` and fill it in) — at minimum a `SESSION_SECRET_KEY` and, for sign-in to actually work, an Entra ID app registration (`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`). Without Entra credentials the app still starts and unauthenticated routes correctly return 401, but `/login` will error since there's nowhere to redirect to.

```bash
uvicorn ops_portal.app:app --reload
```

Defaults to SQLite (`sqlite:///./local.db`) if `DATABASE_URL` isn't set. For local Postgres instead, see `deploy/docker-compose.local.yml`.

## Architecture diagram

TODO

## Five-minute demo script

TODO

## Measurements

TODO

## What I'd do differently

TODO
