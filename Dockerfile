# ── Backend – production Dockerfile ─────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtual env for a clean copy
COPY backend/requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Non-root user for security
RUN useradd -m -u 1001 appuser

# Copy virtual env from builder
COPY --from=builder /opt/venv /opt/venv

# Copy backend source
COPY backend/ ./backend/

# Make sure the venv is on PATH
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app/backend"
ENV PYTHONUNBUFFERED=1

# Default environment (override in docker-compose or runtime)
ENV ENVIRONMENT=production
ENV LOG_LEVEL=INFO

WORKDIR /app/backend

USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/api/health')"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
