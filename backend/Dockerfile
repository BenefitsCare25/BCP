# Inspro backend — production container.
# Multi-stage to keep the final image small.

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install uv for fast resolution + locked installs.
RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./

# Generate a requirements.txt from the lockfile, then install system-wide so
# the runtime stage can copy /usr/local/lib/python3.12/site-packages cleanly.
# Process-substitution (`<(...)`) doesn't work in dash/sh — pipe through stdin
# or use a temp file. Temp-file pattern is more debuggable on failure.
RUN uv export --frozen --no-dev --format requirements-txt > /tmp/requirements.txt \
    && uv pip install --system --no-cache --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Copy installed packages from the builder stage.
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# App source.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

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
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--forwarded-allow-ips", "*", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app.main:app"]
