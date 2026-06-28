#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-9000}"
WORKERS="${WORKERS:-1}"

python -m uvicorn asr_server.app:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --workers "${WORKERS}"

