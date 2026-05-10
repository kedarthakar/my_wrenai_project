#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Check API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "ERROR: ANTHROPIC_API_KEY not set."
  echo "Add it to web/.env:  ANTHROPIC_API_KEY=sk-ant-..."
  exit 1
fi

echo "Starting RAN NOC Intelligence at http://localhost:8000"
echo "Press Ctrl+C to stop."

# Open browser after short delay (background)
(sleep 2 && open "http://localhost:8000") &

uvicorn app:app --host 0.0.0.0 --port 8000
