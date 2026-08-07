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
for command_name in docker git realpath; do
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
PI_IMAGE="cmux-personal-pi-runner:0.83.0"
docker image inspect "$PI_IMAGE" >/dev/null || {
  echo "Pi sandbox image is unavailable; run setup_pi.sh first" >&2
  exit 1
}

if [[ "$PROFILE" == "resolver" ]]; then
  WORKSPACE_MOUNT="type=bind,source=$WORKSPACE_ROOT,target=/workspace"
else
  WORKSPACE_MOUNT="type=bind,source=$WORKSPACE_ROOT,target=/workspace,readonly"
fi

# Shared clones keep objects in a host-side cache and record that cache's
# absolute path in .git/objects/info/alternates. Mount each referenced object
# directory at the same absolute path so Git can use the cache in the sandbox
# without granting the agent write access to it.
ALTERNATE_MOUNTS=()
if ALTERNATES_FILE="$(
  git -C "$WORKING_ROOT" rev-parse \
    --path-format=absolute \
    --git-path objects/info/alternates 2>/dev/null
)" && [[ -f "$ALTERNATES_FILE" ]]; then
  GIT_OBJECTS_DIRECTORY="$(dirname "$(dirname "$ALTERNATES_FILE")")"
  while IFS= read -r alternate_objects || [[ -n "$alternate_objects" ]]; do
    if [[ -z "$alternate_objects" ]]; then
      continue
    fi
    if [[ "$alternate_objects" != /* ]]; then
      alternate_objects="$GIT_OBJECTS_DIRECTORY/$alternate_objects"
    fi
    if [[ ! -d "$alternate_objects" ]]; then
      echo "Git alternate object directory is unavailable: $alternate_objects" >&2
      exit 1
    fi
    alternate_objects="$(realpath "$alternate_objects")"
    if [[ "$alternate_objects" == *,* ]]; then
      echo "Git alternate object directory cannot contain a comma: $alternate_objects" >&2
      exit 1
    fi
    ALTERNATE_MOUNTS+=(
      --mount
      "type=bind,source=$alternate_objects,target=$alternate_objects,readonly"
    )
  done < "$ALTERNATES_FILE"
fi

OUTPUT_MOUNT=()
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
  OUTPUT_MOUNT=(--mount "type=bind,source=$OUTPUT_FILE,target=/pi-output.json")
  OUTPUT_ARGUMENT=(--output /pi-output.json)
fi

printf '%s' "$DEEPSEEK_API_KEY" |
  env -u DEEPSEEK_API_KEY docker run \
    --rm \
    --interactive \
    --pull never \
    --init \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --pids-limit 512 \
    --hostname pi-agent \
    --user "$(id -u):$(id -g)" \
    --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
    --mount "type=bind,source=$CONTROL_ROOT,target=/control,readonly" \
    --mount "$WORKSPACE_MOUNT" \
    "${ALTERNATE_MOUNTS[@]}" \
    "${OUTPUT_MOUNT[@]}" \
    --env CI=true \
    --env GIT_EDITOR=true \
    --env GIT_PAGER=cat \
    --env GIT_SEQUENCE_EDITOR=true \
    --env GIT_TERMINAL_PROMPT=0 \
    --env HOME=/tmp \
    --env LANG=C.UTF-8 \
    --env LC_ALL=C.UTF-8 \
    --env PI_OFFLINE=1 \
    --env PI_TELEMETRY=0 \
    --env TMPDIR=/tmp \
    --workdir "/workspace${WORKING_RELATIVE:+/$WORKING_RELATIVE}" \
    "$PI_IMAGE" \
      --profile "$PROFILE" \
      --prompt-file "/control/$PROMPT_RELATIVE" \
      --control-root /control \
      "${OUTPUT_ARGUMENT[@]}"
