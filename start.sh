#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
    echo "error: .venv missing or uvicorn not installed" >&2
    echo "run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

PORT="${PORT:-8000}"
echo "starting video-search on http://localhost:${PORT}"
exec .venv/bin/uvicorn server:app --host 0.0.0.0 --port "$PORT"
