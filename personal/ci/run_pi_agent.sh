#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat >&2 <<'EOF'
usage: run_pi_agent.sh \
  --control DIR \
  --workspace-root DIR \
  --working-directory RELATIVE_DIR \
  --prompt FILE \
  --profile resolver|reviewer|smoke \
  [--output FILE]
EOF
}

CONTROL_ROOT=""
WORKSPACE_ROOT=""
WORKING_DIRECTORY=""
PROMPT_FILE=""
PROFILE=""
OUTPUT_FILE=""

while (( $# > 0 )); do
  case "$1" in
    --control) CONTROL_ROOT="${2:-}"; shift 2 ;;
    --workspace-root) WORKSPACE_ROOT="${2:-}"; shift 2 ;;
    --working-directory) WORKING_DIRECTORY="${2:-}"; shift 2 ;;
    --prompt) PROMPT_FILE="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --output) OUTPUT_FILE="${2:-}"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done

if [[ -z "$CONTROL_ROOT" || -z "$WORKSPACE_ROOT" || -z "$WORKING_DIRECTORY" ||
      -z "$PROMPT_FILE" || -z "$PROFILE" ]]; then
  usage
  exit 2
fi
if [[ "$PROFILE" != "resolver" && "$PROFILE" != "reviewer" && "$PROFILE" != "smoke" ]]; then
  echo "invalid Pi profile: $PROFILE" >&2
  exit 2
fi
if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "DEEPSEEK_API_KEY is required" >&2
  exit 1
fi
for command_name in bwrap node realpath; do
  command -v "$command_name" >/dev/null || {
    echo "required command is unavailable: $command_name" >&2
    exit 1
  }
done

CONTROL_ROOT="$(realpath "$CONTROL_ROOT")"
WORKSPACE_ROOT="$(realpath "$WORKSPACE_ROOT")"
PROMPT_FILE="$(realpath "$PROMPT_FILE")"
WORKING_ROOT="$(realpath "$WORKSPACE_ROOT/$WORKING_DIRECTORY")"

case "$PROMPT_FILE" in
  "$CONTROL_ROOT"/*) ;;
  *) echo "Pi prompt must be inside the trusted control checkout" >&2; exit 1 ;;
esac
case "$WORKING_ROOT" in
  "$WORKSPACE_ROOT" | "$WORKSPACE_ROOT"/*) ;;
  *) echo "Pi working directory escapes its sandbox root" >&2; exit 1 ;;
esac

PROMPT_RELATIVE="${PROMPT_FILE#"$CONTROL_ROOT"/}"
WORKING_RELATIVE="${WORKING_ROOT#"$WORKSPACE_ROOT"}"
WORKING_RELATIVE="${WORKING_RELATIVE#/}"
NODE_BINARY="$(realpath "$(command -v node)")"

if [[ "$PROFILE" == "resolver" ]]; then
  WORKSPACE_BIND=(--bind "$WORKSPACE_ROOT" /workspace)
else
  WORKSPACE_BIND=(--ro-bind "$WORKSPACE_ROOT" /workspace)
fi

OUTPUT_BIND=()
OUTPUT_ARGUMENT=()
if [[ -n "$OUTPUT_FILE" ]]; then
  OUTPUT_PARENT="$(realpath "$(dirname "$OUTPUT_FILE")")"
  OUTPUT_NAME="$(basename "$OUTPUT_FILE")"
  if [[ "$OUTPUT_NAME" == "." || "$OUTPUT_NAME" == ".." ]]; then
    echo "invalid Pi output path" >&2
    exit 1
  fi
  OUTPUT_FILE="$OUTPUT_PARENT/$OUTPUT_NAME"
  case "$OUTPUT_FILE" in
    "$CONTROL_ROOT" | "$CONTROL_ROOT"/* | "$WORKSPACE_ROOT" | "$WORKSPACE_ROOT"/*)
      echo "Pi structured output must be outside the mounted repositories" >&2
      exit 1
      ;;
  esac
  install -m 600 /dev/null "$OUTPUT_FILE"
  OUTPUT_BIND=(--bind "$OUTPUT_FILE" /pi-output.json)
  OUTPUT_ARGUMENT=(--output /pi-output.json)
fi

PI_PATH="$(dirname "$NODE_BINARY"):/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

printf '%s' "$DEEPSEEK_API_KEY" |
  env -u DEEPSEEK_API_KEY bwrap \
    --die-with-parent \
    --new-session \
    --unshare-pid \
    --unshare-ipc \
    --unshare-uts \
    --clearenv \
    --cap-drop ALL \
    --ro-bind /bin /bin \
    --ro-bind /etc /etc \
    --ro-bind /lib /lib \
    --ro-bind /lib64 /lib64 \
    --ro-bind /opt /opt \
    --ro-bind /run /run \
    --ro-bind /sbin /sbin \
    --ro-bind /usr /usr \
    --proc /proc \
    --dev /dev \
    --tmpfs /tmp \
    --dir /tmp/pi-agent-config \
    --dir /control \
    --dir /workspace \
    --ro-bind "$CONTROL_ROOT" /control \
    "${WORKSPACE_BIND[@]}" \
    "${OUTPUT_BIND[@]}" \
    --ro-bind /dev/null /usr/bin/su \
    --ro-bind /dev/null /usr/bin/sudo \
    --setenv CI true \
    --setenv GIT_EDITOR true \
    --setenv GIT_SEQUENCE_EDITOR true \
    --setenv GIT_TERMINAL_PROMPT 0 \
    --setenv HOME /tmp \
    --setenv LANG C.UTF-8 \
    --setenv LC_ALL C.UTF-8 \
    --setenv PATH "$PI_PATH" \
    --setenv PI_OFFLINE 1 \
    --setenv PI_TELEMETRY 0 \
    --setenv TMPDIR /tmp \
    --chdir "/workspace${WORKING_RELATIVE:+/$WORKING_RELATIVE}" \
    "$NODE_BINARY" /control/personal/pi/run_agent.mjs \
      --profile "$PROFILE" \
      --prompt-file "/control/$PROMPT_RELATIVE" \
      "${OUTPUT_ARGUMENT[@]}"
