#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: personal/ci/ensure_pi_sandbox.sh --control-root <directory> [--scheduled]

Verify the local Docker-backed Pi sandbox. A scheduled macOS run may start
Docker Desktop and wait briefly for it; an interactive run only reports that
the runtime is stopped.
EOF
}

CONTROL_ROOT=""
SCHEDULED="false"
DOCKER_IMAGE="cmux-personal-pi-runner:0.83.0"

while (( $# > 0 )); do
  case "$1" in
    --control-root) CONTROL_ROOT="${2:-}"; shift 2 ;;
    --scheduled) SCHEDULED="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$CONTROL_ROOT" || ! -d "$CONTROL_ROOT" ]]; then
  echo "a valid --control-root directory is required" >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker or an equivalent Docker-compatible runtime is required for Pi" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  if [[ "$SCHEDULED" != "true" ]]; then
    echo "the Docker-compatible runtime is installed but is not running" >&2
    exit 1
  fi
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "the Docker-compatible runtime is installed but is not running" >&2
    exit 1
  fi

  docker_app="${CMUX_PERSONAL_DOCKER_APP:-/Applications/Docker.app}"
  open_command="${CMUX_PERSONAL_OPEN_COMMAND:-/usr/bin/open}"
  attempts="${CMUX_PERSONAL_DOCKER_START_ATTEMPTS:-60}"
  delay_seconds="${CMUX_PERSONAL_DOCKER_START_DELAY_SECONDS:-2}"
  if [[ ! -d "$docker_app" ]]; then
    echo "Docker Desktop is not installed at $docker_app" >&2
    exit 1
  fi
  if [[ ! -x "$open_command" ]]; then
    echo "cannot start Docker Desktop because open is unavailable: $open_command" >&2
    exit 1
  fi
  if [[ ! "$attempts" =~ ^[1-9][0-9]*$ ]]; then
    echo "Docker start attempts must be a positive integer" >&2
    exit 2
  fi
  if [[ ! "$delay_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Docker start delay must be a non-negative number" >&2
    exit 2
  fi

  echo "scheduled conflict resolution needs Docker; starting Docker Desktop"
  "$open_command" -gj "$docker_app"
  docker_ready="false"
  for (( attempt = 1; attempt <= attempts; attempt++ )); do
    if docker info >/dev/null 2>&1; then
      docker_ready="true"
      break
    fi
    if (( attempt < attempts )); then
      sleep "$delay_seconds"
    fi
  done
  if [[ "$docker_ready" != "true" ]]; then
    echo "Docker Desktop did not become ready after $attempts attempts" >&2
    exit 1
  fi
fi

if ! docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
  "$CONTROL_ROOT/personal/ci/setup_pi.sh" "$CONTROL_ROOT"
fi
