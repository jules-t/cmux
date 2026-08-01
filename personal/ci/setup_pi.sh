#!/usr/bin/env bash
set -euo pipefail

CONTROL_ROOT="${1:?usage: setup_pi.sh CONTROL_ROOT}"

if ! command -v bwrap >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install --yes --no-install-recommends bubblewrap
fi

npm ci \
  --prefix "$CONTROL_ROOT/personal/pi" \
  --ignore-scripts \
  --no-audit \
  --no-fund
npm --prefix "$CONTROL_ROOT/personal/pi" run check
