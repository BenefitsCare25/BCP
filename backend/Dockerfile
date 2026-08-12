# Inspro — production container.
#
# Build context is the REPO ROOT (not backend/), because single-host
# deployments bake the SPA into the same image as the API:
#     docker build -f backend/Dockerfile -t <image> .
# The SPA and API must share an origin — the HR refresh cookie is host-only and
# SameSite=Strict, so a separately-hosted frontend could never refresh a
# session. See backend/app/core/spa.py.

# ── Frontend build ───────────────────────────────────────────────────────────
FROM node:20-slim AS frontend

WORKDIR /web

RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./

# Vite inlines env at BUILD time, so anything env-specific is a build arg and
# staging/prod get different images. Entra IDs differ per environment; without
# them ENTRA_ENABLED is false and the broker surface falls back to mock auth.
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_TENANT_MODE=header
ARG VITE_ENTRA_TENANT_ID=""
ARG VITE_ENTRA_CLIENT_ID=""
ARG VITE_TENANT_BASE_DOMAIN=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_TENANT_MODE=$VITE_TENANT_MODE \
    VITE_ENTRA_TENANT_ID=$VITE_ENTRA_TENANT_ID \
    VITE_ENTRA_CLIENT_ID=$VITE_ENTRA_CLIENT_ID \
    VITE_TENANT_BASE_DOMAIN=$VITE_TENANT_BASE_DOMAIN

RUN pnpm build

# ── Python dependency build ──────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install uv for fast resolution + locked installs.
RUN pip install --no-cache-dir uv

COPY backend/pyproject.toml backend/uv.lock ./

# Generate a requirements.txt from the lockfile, then install system-wide so
# the runtime stage can copy /usr/local/lib/python3.12/site-packages cleanly.
# Process-substitution (`<(...)`) doesn't work in dash/sh — pipe through stdin
# or use a temp file. Temp-file pattern is more debuggable on failure.
RUN uv export --frozen --no-dev --format requirements-txt > /tmp/requirements.txt \
    && uv pip install --system --no-cache --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Copy installed packages from the builder stage.
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# App source.
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/scripts ./scripts

# Built SPA — `app.core.spa` mounts it if present, no-op otherwise.
COPY --from=frontend /web/dist ./static

# Non-root user.
RUN useradd --create-home --shell /bin/bash inspro && chown -R inspro:inspro /app
USER inspro

EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3); sys.exit(0 if r.status==200 else 1)"

# `--forwarded-allow-ips=*` so SlowAPI / request-ID middleware can read
# X-Forwarded-For from the App Service load balancer.
# `--limit-request-line` / `--limit-request-field_size` left at defaults;
# multipart upload size is capped by FastAPI / saved_upload, not gunicorn.
#
# Worker count is env-driven (WEB_CONCURRENCY), defaulting to 2 to match a
# 2-vCPU App Service plan. It was hardcoded to 4, which oversubscribes anything
# smaller AND silently multiplies two per-process limits: the AI concurrency
# semaphore and the SQLAlchemy pool. Raise it in lockstep with the plan size.
#
# `exec` so gunicorn is PID 1 and actually receives SIGTERM — otherwise the
# shell swallows it, the container is SIGKILLed, and in-flight background claim
# reviews are stranded (recoverable via the startup reaper, but needlessly).
# `--graceful-timeout 60` gives those background reviews a chance to land.
# `--timeout 120` (default 30) is the arbiter's patience for a worker HEARTBEAT,
# not for a request. An endpoint that blocks the event loop stops beating, so a
# long roster apply read as a hung worker and was SIGKILLed mid-request at 30s —
# no response, no error message in the browser. The endpoints themselves now run
# their blocking work in a thread (`api/v1/adc.py`); this is the backstop for
# any that still don't, and 120s stays well inside App Service's own ~230s
# front-end idle timeout, which is the real ceiling on a request here.
CMD ["sh", "-c", "exec gunicorn -w ${WEB_CONCURRENCY:-2} -k uvicorn.workers.UvicornWorker \
     --bind 0.0.0.0:8000 \
     --forwarded-allow-ips '*' \
     --timeout 120 \
     --graceful-timeout 60 \
     --access-logfile - \
     --error-logfile - \
     app.main:app"]
