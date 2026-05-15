#!/usr/bin/env docker
# syntax=docker/dockerfile:1.4
# Optimized Multi-stage Dockerfile for AI630 Project
# Supports both amd64 and arm64 architectures
# Optimized for build cache and image size

# ============================================================================
# Build Arguments
# ============================================================================
ARG PYTHON_VERSION=3.12
ARG TARGETARCH

# ============================================================================
# Stage 1: Base Image Selection
# ============================================================================
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS base

# Set working directory
WORKDIR /app

# Environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PYTHON_DOWNLOADS=never

# ============================================================================
# Stage 2: System Dependencies
# ============================================================================
FROM base AS system-deps

# Configure Debian mirror (使用华为云镜像加速)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eux; \
    sed -i 's|http://deb.debian.org/debian|https://repo.huaweicloud.com/debian|g' /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential \
        libglib2.0-0 \
        libglib2.0-dev \
        postgresql-client \
        curl \
        ca-certificates; \
    rm -rf /var/lib/apt/lists/*

# ============================================================================
# Stage 3: Python Dependencies Builder
# ============================================================================
FROM system-deps AS python-deps

# UV configuration for faster builds
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy

# Copy only dependency files first (for better caching)
COPY pyproject.toml uv.lock README.md ./

# Install Python dependencies with cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ============================================================================
# Stage 4: Application Builder
# ============================================================================
FROM python-deps AS app-builder

# Copy application source code
COPY src/ ./src/
COPY alembic.ini ./
COPY docker/docker-entrypoint.sh ./docker/docker-entrypoint.sh

# Install application in editable mode (--no-deps: deps already installed by uv sync above)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -e . --no-deps

# Pre-install dependencies for the entrypoints
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Make entrypoint executable
RUN chmod +x ./docker/docker-entrypoint.sh

# Compile Python files to bytecode for faster startup (optional)
RUN python -m compileall -b src/

# ============================================================================
# Stage 5: Runtime Image (Minimal)
# ============================================================================
FROM base AS runtime

# Install runtime and build dependencies (needed for compiled packages like asyncmy)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eux; \
    sed -i 's|http://deb.debian.org/debian|https://repo.huaweicloud.com/debian|g' /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        postgresql-client \
        curl \
        build-essential \
        python3-dev \
        rustc \
        cargo \
        gosu \
        file; \
    rm -rf /var/lib/apt/lists/*

# Create non-root user for security BEFORE copying files
RUN groupadd -g 1000 appuser && \
    useradd -r -u 1000 -g appuser -s /bin/sh -d /app appuser

# Copy virtual environment and application from builder with explicit ownership
COPY --from=app-builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=app-builder --chown=appuser:appuser /app/src /app/src
COPY --from=app-builder --chown=appuser:appuser /app/alembic.ini /app/alembic.ini
COPY --from=app-builder --chown=appuser:appuser /app/docker/docker-entrypoint.sh /app/docker/docker-entrypoint.sh
COPY --from=app-builder --chown=appuser:appuser /app/pyproject.toml /app/pyproject.toml
COPY --from=app-builder --chown=appuser:appuser /app/uv.lock /app/uv.lock
COPY --from=app-builder --chown=appuser:appuser /app/README.md /app/README.md

# Fix venv Python symlinks to point to system Python
RUN set -eux; \
    SYSPYTHON=$(command -v python3.14 || command -v python3 || echo ""); \
    if [ -n "$SYSPYTHON" ]; then \
        ln -sf "$SYSPYTHON" /app/.venv/bin/python3; \
        ln -sf "$SYSPYTHON" /app/.venv/bin/python; \
    fi; \
    chown -R appuser:appuser /app/.venv/bin/python*

# Create logs directory with proper permissions
RUN mkdir -p /app/logs && chown -R appuser:appuser /app/logs

# Final recursive chown to ensure everything is owned by appuser
RUN chown -R appuser:appuser /app

# Start as root so the entrypoint can fix ownership of mounted volumes, then
# re-exec as appuser before launching application processes.
USER root

# Set PATH to include virtual environment
ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/.venv"

WORKDIR /app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

# Expose ports
EXPOSE 8000 9000

# Entrypoint
ENTRYPOINT ["./docker/docker-entrypoint.sh"]

# Labels for metadata
LABEL maintainer="wanghaowei" \
      org.opencontainers.image.title="AI630 Service" \
      org.opencontainers.image.description="FastAPI backend for AI agent system" \
      org.opencontainers.image.vendor="艾普工华(武汉)有限责任公司" \
      org.opencontainers.image.version="2.0.0"
