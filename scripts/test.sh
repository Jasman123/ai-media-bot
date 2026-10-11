#!/usr/bin/env bash
# Lint + full test suite (unit, fixture, integration, e2e). Extra args go to pytest, e.g. `scripts/test.sh -m unit`.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-.venv/bin/python}
scripts/test-services.sh up
DIRS=(app tests)
[ -d alembic ] && DIRS+=(alembic)
"$PY" -m ruff check --output-format concise "${DIRS[@]}"
"$PY" -m pytest "$@"
