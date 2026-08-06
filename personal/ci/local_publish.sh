#!/usr/bin/env bash
set -euo pipefail

CONTROL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ $# -ne 1 ]]; then
  echo "usage: personal/ci/local_publish.sh <validation-receipt.json>" >&2
  exit 2
fi

PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/local_publisher.py" \
  --receipt "$1" \
  --config "$CONTROL_ROOT/personal/config.json" \
  --repository "$CONTROL_ROOT"
