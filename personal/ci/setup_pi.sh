#!/usr/bin/env bash
set -euo pipefail

CONTROL_ROOT="${1:?usage: setup_pi.sh CONTROL_ROOT}"
PI_IMAGE="cmux-personal-pi-runner:0.83.0"

command -v docker >/dev/null || {
  echo "Docker is required for the Pi sandbox" >&2
  exit 1
}

docker build \
  --pull \
  --tag "$PI_IMAGE" \
  "$CONTROL_ROOT/personal/pi"
