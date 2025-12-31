#!/bin/bash
# scripts/start_worker.sh
#
# Script to start the agent worker

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Start the worker using the new CLI entry point
echo "Starting Agent Worker..."
python -m src.aiwen.workers

echo "Worker stopped."