#!/bin/sh
set -e

echo "==> Starting aiwen services..."

terminate() {
    echo "==> Received termination signal"
    echo "==> Stopping services..."

    kill -TERM "$API_PID" "$MCP_PID" 2>/dev/null || true
    wait
    exit 0
}

trap terminate TERM INT

# ----------------------
# Migrations
# ----------------------
cd /app
echo "==> Running database migrations..."

# Temporarily disable exit on error for migration handling
set +e

# Get current alembic version
CURRENT_VERSION=$(uv run alembic current 2>/dev/null | grep -v "^INFO" | grep -v "^Using" | grep -v "^Context" | grep -v "^Will assume" || true)

if [ -n "$CURRENT_VERSION" ]; then
    # Version exists, normal upgrade
    echo "==> Current database version: $CURRENT_VERSION"
    echo "==> Upgrading to head..."

    uv run alembic upgrade head 2>&1
    if [ $? -eq 0 ]; then
        echo "==> Database migrations completed successfully"
    else
        echo "==> ERROR: Database migration failed"
        exit 1
    fi
else
    # No version recorded
    echo "==> No alembic version found in database"
    echo "==> Attempting migration..."

    # Try direct upgrade (works if database is empty)
    # Save output to check for errors
    uv run alembic upgrade head > /tmp/alembic_output.log 2>&1
    UPGRADE_EXIT_CODE=$?

    if [ $UPGRADE_EXIT_CODE -eq 0 ]; then
        echo "==> Database migrations completed successfully"
    else
        # Migration failed, check if it's because tables exist
        if grep -q "DuplicateTable\|already exists" /tmp/alembic_output.log; then
            echo "==> Tables already exist but no version recorded"
            echo "==> Stamping database as current version..."

            if uv run alembic stamp head; then
                echo "==> Database stamped as head"
                echo "==> Applying any new migrations..."

                if uv run alembic upgrade head; then
                    echo "==> Database migrations completed successfully"
                else
                    echo "==> WARNING: Could not apply new migrations after stamp"
                fi
            else
                echo "==> ERROR: Failed to stamp database version"
                exit 1
            fi
        else
            echo "==> ERROR: Database migration failed with unexpected error"
            echo "==> Error details:"
            cat /tmp/alembic_output.log
            exit 1
        fi
    fi
fi

# Re-enable exit on error
set -e

# ----------------------
# Start services
# ----------------------
cd /app/src

uv run aiwen-mcp &
MCP_PID=$!

uv run aiwen-api &
API_PID=$!

uv run aiwen-worker &
WORKER_PID=$!

echo "==> All services started (MCP PID: $MCP_PID, API PID: $API_PID)"

# ----------------------
# POSIX-compatible wait
# ----------------------
while true; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "==> API exited"
        break
    fi

    if ! kill -0 "$MCP_PID" 2>/dev/null; then
        echo "==> MCP exited"
        break
    fi
    if ! kill -0 "$WORKER_PID" 2>/dev/null; then
        echo "==> Worker exited"
        break
    fi
    sleep 1
done

terminate
