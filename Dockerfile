# ==============================================================================
# PROJECT AURA — PRODUCTION DOCKERFILE
# Multi-stage, non-root, minimal attack surface container definition.
# ==============================================================================

# --- Stage 1: Build Dependencies ---
FROM python:3.11-slim as builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels -r requirements.txt

# --- Stage 2: Production Runtime ---
FROM python:3.11-slim as runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AURA_ENV=production \
    AURA_SERVER_HOST=0.0.0.0 \
    AURA_SERVER_PORT=8000 \
    PYTHONPATH=/app

# Create unprivileged system user
RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -s /bin/bash -m appuser

# Install wheels from builder
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache /wheels/* && rm -rf /wheels

# Copy application source code
COPY --chown=appuser:appgroup app/ /app/app/
COPY --chown=appuser:appgroup core/ /app/core/
COPY --chown=appuser:appgroup evaluation/ /app/evaluation/
COPY --chown=appuser:appgroup interfaces/ /app/interfaces/
COPY --chown=appuser:appgroup knowledge/ /app/knowledge/
COPY --chown=appuser:appgroup memory/ /app/memory/
COPY --chown=appuser:appgroup providers/ /app/providers/
COPY --chown=appuser:appgroup research/ /app/research/
COPY --chown=appuser:appgroup tools/ /app/tools/
COPY --chown=appuser:appgroup pyproject.toml README.md /app/

# Create persistent storage directories with proper ownership
RUN mkdir -p /app/.aura_checkpoints \
             /app/.aura_artifacts \
             /app/.aura_traces \
             /app/.aura_skills \
             /app/.aura_knowledge \
    && chown -R appuser:appgroup /app

USER appuser

# Expose HTTP service port
EXPOSE 8000

# Healthcheck probe hitting unauthenticated liveness endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').getcode() == 200 else 1)"

# Canonical production entrypoint
ENTRYPOINT ["python", "-m", "app.server"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
