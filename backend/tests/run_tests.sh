#!/bin/bash
# Run the Herakles Play backend test suite.
#
# Usage (from repo root or backend/):
#   ./tests/run_tests.sh              # all tests
#   ./tests/run_tests.sh -k transcoder  # filter by name
#   ./tests/run_tests.sh --cov        # with coverage (requires pytest-cov)
#
# To run inside the running Docker container:
#   docker compose exec backend bash tests/run_tests.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${BACKEND_DIR}"

echo "=== Herakles Play — backend test suite ==="
echo "Backend: ${BACKEND_DIR}"
echo ""

python -m pytest tests/ -v --tb=short "$@"
