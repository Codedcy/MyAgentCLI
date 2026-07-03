#!/usr/bin/env bash
# Lint the project using ruff.
# Part of the code-review skill — runs static analysis before a review.
#
# Usage: bash lint.sh [path]
#   path: optional directory or file to lint (defaults to current directory)

set -euo pipefail

TARGET="${1:-.}"

echo "=== Running ruff check on ${TARGET} ==="
ruff check "${TARGET}" 2>&1 || {
    echo "Lint issues found. Review the output above."
    exit 1
}
echo "Lint passed."
