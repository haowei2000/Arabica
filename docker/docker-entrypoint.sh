#!/bin/sh
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/logs /app/data/context
    chown -R appuser:appuser /app/logs /app/data
    exec gosu appuser "$0" "$@"
fi

echo "==> Starting structure services..."

PIDS=""

terminate() {
    echo "==> Received termination signal, stopping all services..."
    for pid in $PIDS; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    wait
    exit 0
}

trap terminate TERM INT

# ----------------------
# Wait for PostgreSQL
# ----------------------
if [ "$SKIP_MIGRATIONS" != "true" ]; then
    PG_HOST="${POSTGRES__HOST:-postgres}"
    PG_PORT="${POSTGRES__PORT:-5432}"
    PG_USER="${POSTGRES__USERNAME:-postgres}"
    RETRIES=30
    echo "==> Waiting for PostgreSQL at ${PG_HOST}:${PG_PORT}..."
    until pg_isready -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" 2>/dev/null; do
        RETRIES=$((RETRIES - 1))
        if [ "$RETRIES" -le 0 ]; then
            echo "==> ERROR: PostgreSQL did not become ready in time"
            exit 1
        fi
        echo "==> PostgreSQL not ready, retrying... ($RETRIES left)"
        sleep 2
    done
    echo "==> PostgreSQL is ready"
fi

# ----------------------
# Migrations
# ----------------------
if [ "$SKIP_MIGRATIONS" = "true" ]; then
    echo "==> Skipping database migrations (SKIP_MIGRATIONS=true)"
else
    cd /app
    echo "==> Running database migrations..."

    set +e
    CURRENT_VERSION=$(alembic current 2>/dev/null | grep -v "^INFO" | grep -v "^Using" | grep -v "^Context" | grep -v "^Will assume" || true)

    if [ -n "$CURRENT_VERSION" ]; then
        echo "==> Current database version: $CURRENT_VERSION"
        alembic upgrade heads 2>&1 || { echo "==> ERROR: Migration failed"; exit 1; }
    else
        echo "==> No alembic version found, attempting migration..."
        alembic upgrade head > /tmp/alembic_output.log 2>&1
        if [ $? -ne 0 ]; then
            if grep -q "DuplicateTable\|already exists" /tmp/alembic_output.log; then
                echo "==> Tables exist but no version recorded, stamping..."
                alembic stamp heads && alembic upgrade head || { echo "==> ERROR: Stamp/upgrade failed"; exit 1; }
            else
                echo "==> ERROR: Migration failed"; cat /tmp/alembic_output.log; exit 1
            fi
        fi
    fi
    set -e
    echo "==> Database migrations completed"
fi

# ----------------------
# Start custom command or default (all-in-one) mode
# ----------------------
if [ "$#" -gt 0 ]; then
    echo "==> Executing custom command: $@"
    exec "$@"
fi

# All-in-one mode: start all backend processes
cd /app

echo "==> Starting API server..."
structure-api &
API_PID=$!
PIDS="$PIDS $API_PID"

if [ "${EMBED_WORKER:-true}" != "true" ]; then
    echo "==> Starting event worker (standalone)..."
    structure-worker &
    WORKER_PID=$!
    PIDS="$PIDS $WORKER_PID"
else
    echo "==> Event worker runs embedded inside API (EMBED_WORKER=true)"
fi

if [ "${START_CELERY:-false}" = "true" ]; then
    echo "==> Starting Celery worker..."
    celery -A structure.celery_worker.celery_app worker --loglevel=info --concurrency=1 -Q celery,default,knowledge &
    CELERY_PID=$!
    PIDS="$PIDS $CELERY_PID"
fi

if [ "${START_MCP:-false}" = "true" ]; then
    echo "==> Starting MCP server..."
    structure-mcp &
    MCP_PID=$!
    PIDS="$PIDS $MCP_PID"
fi

echo "==> All services started (API: $API_PID${WORKER_PID:+, Worker: $WORKER_PID})"

# Monitor: exit container if any critical process dies
while true; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "==> API exited unexpectedly"; terminate
    fi
    if [ -n "$WORKER_PID" ] && ! kill -0 "$WORKER_PID" 2>/dev/null; then
        echo "==> Event worker exited unexpectedly"; terminate
    fi
    if [ -n "$CELERY_PID" ] && ! kill -0 "$CELERY_PID" 2>/dev/null; then
        echo "==> Celery worker exited unexpectedly"; terminate
    fi
    sleep 5
done
