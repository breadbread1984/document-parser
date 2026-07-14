#!/usr/bin/env bash
# Local (non-Docker) dual-venv bootstrap — mirrors the container layout.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3.11}"
API_VENV="${API_VENV:-$ROOT/.venvs/api}"
MINERU_VENV="${MINERU_VENV:-$ROOT/.venvs/mineru}"
MOLSCRIBE_VENV="${MOLSCRIBE_VENV:-$ROOT/.venvs/molscribe}"

mkdir -p "$ROOT/.venvs" "$ROOT/data/jobs" "$ROOT/data/cache"

if [[ ! -x "$API_VENV/bin/python" ]]; then
  "$PY" -m venv "$API_VENV"
  "$API_VENV/bin/pip" install -U pip
  "$API_VENV/bin/pip" install -r requirements-api.txt
fi

if [[ ! -x "$MINERU_VENV/bin/mineru" ]]; then
  "$PY" -m venv "$MINERU_VENV"
  "$MINERU_VENV/bin/pip" install -U pip
  "$MINERU_VENV/bin/pip" install -r requirements-mineru.txt
fi

if [[ ! -x "$MOLSCRIBE_VENV/bin/python" ]]; then
  "$PY" -m venv "$MOLSCRIBE_VENV"
  "$MOLSCRIBE_VENV/bin/pip" install -U pip
  "$MOLSCRIBE_VENV/bin/pip" install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  "$MOLSCRIBE_VENV/bin/pip" install -r requirements-molscribe.txt
fi

echo "Ready."
echo "  export DATA_DIR=$ROOT/data MINERU_VENV=$MINERU_VENV MOLSCRIBE_VENV=$MOLSCRIBE_VENV"
echo "  $API_VENV/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"
