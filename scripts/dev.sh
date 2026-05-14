#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

EXPLICIT_SOP_DATA_ROOT="${SOP_DATA_ROOT:-}"
EXPLICIT_SOP_FRONTEND_DIST="${SOP_FRONTEND_DIST:-}"
EXPLICIT_SOP_MODELS_PATH="${SOP_MODELS_PATH:-}"
EXPLICIT_SOP_HOST="${SOP_HOST:-}"
EXPLICIT_SOP_PORT="${SOP_PORT:-}"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

if [ -n "$EXPLICIT_SOP_DATA_ROOT" ]; then
  SOP_DATA_ROOT="$EXPLICIT_SOP_DATA_ROOT"
fi
if [ -n "$EXPLICIT_SOP_FRONTEND_DIST" ]; then
  SOP_FRONTEND_DIST="$EXPLICIT_SOP_FRONTEND_DIST"
fi
if [ -n "$EXPLICIT_SOP_MODELS_PATH" ]; then
  SOP_MODELS_PATH="$EXPLICIT_SOP_MODELS_PATH"
fi
if [ -n "$EXPLICIT_SOP_HOST" ]; then
  SOP_HOST="$EXPLICIT_SOP_HOST"
fi
if [ -n "$EXPLICIT_SOP_PORT" ]; then
  SOP_PORT="$EXPLICIT_SOP_PORT"
fi

PYTHON_BIN="${PYTHON:-python}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

if [ "${SOP_DATA_ROOT:-}" = "/data/jobs" ]; then
  SOP_DATA_ROOT="$ROOT_DIR/data/jobs"
fi

export SOP_DATA_ROOT="${SOP_DATA_ROOT:-$ROOT_DIR/data/jobs}"
export SOP_FRONTEND_DIST="${SOP_FRONTEND_DIST:-$ROOT_DIR/frontend/dist}"
export SOP_MODELS_PATH="${SOP_MODELS_PATH:-$ROOT_DIR/backend/models.json}"
export SOP_HOST="${SOP_HOST:-127.0.0.1}"
export SOP_PORT="${SOP_PORT:-7860}"

if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
  npm --prefix "$ROOT_DIR/frontend" install --legacy-peer-deps
fi

npm --prefix "$ROOT_DIR/frontend" run build

exec "$PYTHON_BIN" -m uvicorn backend.app.main:app --host "$SOP_HOST" --port "$SOP_PORT"
