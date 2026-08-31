# Build from the project root (deploy/README.md's demo script; also what
# the top-level README's setup instructions use):
#   docker build -t meridian-ops-portal -f deploy/Dockerfile .
# The build context is `.`, not `deploy/` — see ../.dockerignore (not
# deploy/.dockerignore, which has no effect for this exact command) for
# what that means .env never ends up in.

# --- builder -----------------------------------------------------------
# Installs the package — with the [postgres] extra, since the production
# DATABASE_URL is Postgres; sqlite is the local-dev-only fallback and
# needs no driver — into an isolated venv, so none of this stage's build
# tooling ends up in the final image.
FROM python:3.11-slim AS builder
# 3.11-slim matches pyproject.toml's `requires-python = ">=3.11"`: the
# same major.minor Python this project targets, without pinning a patch
# version the project itself never specifies.

WORKDIR /build

# psycopg2-binary ships prebuilt wheels for the common platforms, but
# build-essential covers pip falling back to a source build on anything
# that doesn't have one available. Only ever installed here — discarded
# along with the rest of this stage once the final image is built.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Only what pip actually needs to resolve and install the package: the
# build metadata and the source tree it packages (see pyproject.toml's
# package-data — templates/ and static/ are bundled into the installed
# package here, not copied separately in the final stage below).
COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[postgres]"

# --- final ---------------------------------------------------------------
FROM python:3.11-slim AS final

# Non-root: a compromised app process gets only this user's own rights,
# never root's. --no-create-home / --shell nologin because nothing here
# is interactive or needs a writable home directory.
RUN groupadd --system app \
    && useradd --system --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# The built venv — dependencies plus the installed ops_portal package
# (code, templates, static assets) — is the only thing that crosses from
# the builder stage. No compiler, no build headers, no pip cache, no
# source tree outside what's already inside site-packages.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

EXPOSE 8000

CMD ["uvicorn", "ops_portal.app:app", "--host", "0.0.0.0", "--port", "8000"]
