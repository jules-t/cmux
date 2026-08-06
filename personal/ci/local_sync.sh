#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: personal/ci/local_sync.sh [options]

Monitor upstream and prepare locally reviewed cmux Personal candidates. Nothing
is built, published, or pushed to GitHub by this command.

Options:
  --channel <auto|stable|main>  Channels to inspect (default: auto, meaning both)
  --target-tag <vX.Y.Z>        Retry one exact official stable release
  --output <file>              Write the selected local candidate record
  --data-root <directory>      Local state and candidates directory
  --scheduled                  Suppress automatic retries of a blocked stable target
  --check                      Check local monitor/resolver prerequisites
  --smoke                      Run the sandboxed DeepSeek connectivity smoke test
  --configure-deepseek         Store the DeepSeek API key in macOS Keychain
EOF
}

CONTROL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$CONTROL_ROOT/personal/config.json"
DATA_ROOT="${CMUX_PERSONAL_DATA_ROOT:-$HOME/.local/share/cmux-personal}"
CHANNEL="auto"
TARGET_TAG=""
OUTPUT_FILE=""
SCHEDULED="false"
CHECK_ONLY="false"
SMOKE_ONLY="false"
CONFIGURE_DEEPSEEK="false"
KEYCHAIN_SERVICE="com.jules.cmux-personal.deepseek"

while (( $# > 0 )); do
  case "$1" in
    --channel) CHANNEL="${2:-}"; shift 2 ;;
    --target-tag) TARGET_TAG="${2:-}"; shift 2 ;;
    --output) OUTPUT_FILE="${2:-}"; shift 2 ;;
    --data-root) DATA_ROOT="${2:-}"; shift 2 ;;
    --scheduled) SCHEDULED="true"; shift ;;
    --check) CHECK_ONLY="true"; shift ;;
    --smoke) SMOKE_ONLY="true"; shift ;;
    --configure-deepseek) CONFIGURE_DEEPSEEK="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$CHANNEL" != "auto" && "$CHANNEL" != "stable" && "$CHANNEL" != "main" ]]; then
  echo "channel must be auto, stable, or main" >&2
  exit 2
fi
if [[ -n "$TARGET_TAG" && ! "$TARGET_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "target tag must look like vX.Y.Z" >&2
  exit 2
fi
if [[ -n "$TARGET_TAG" && "$CHANNEL" == "main" ]]; then
  echo "--target-tag can only be used with the stable or auto channel" >&2
  exit 2
fi

if [[ "$CONFIGURE_DEEPSEEK" == "true" ]]; then
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "DeepSeek Keychain setup requires macOS" >&2
    exit 1
  fi
  read -r -s -p "DeepSeek API key: " DEEPSEEK_KEY
  echo
  if [[ -z "$DEEPSEEK_KEY" ]]; then
    echo "the DeepSeek API key cannot be empty" >&2
    exit 1
  fi
  security add-generic-password \
    -U \
    -a "$(id -un)" \
    -s "$KEYCHAIN_SERVICE" \
    -w "$DEEPSEEK_KEY" >/dev/null
  unset DEEPSEEK_KEY
  echo "stored the cmux Personal DeepSeek key in macOS Keychain"
  exit 0
fi

for command_name in git gh jq python3; do
  command -v "$command_name" >/dev/null || {
    echo "required command is unavailable: $command_name" >&2
    exit 1
  }
done
if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI authentication is required; run: gh auth login" >&2
  exit 1
fi

load_deepseek_key() {
  if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
    return
  fi
  if [[ "$(uname -s)" == "Darwin" ]]; then
    DEEPSEEK_API_KEY="$(
      security find-generic-password \
        -a "$(id -un)" \
        -s "$KEYCHAIN_SERVICE" \
        -w 2>/dev/null || true
    )"
    export DEEPSEEK_API_KEY
  fi
  if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
    echo "DeepSeek API key is not configured for local conflict resolution." >&2
    echo "Run: personal/ci/local_sync.sh --configure-deepseek" >&2
    return 1
  fi
}

require_pi_sandbox() {
  command -v docker >/dev/null || {
    echo "Docker or an equivalent Docker-compatible runtime is required for Pi" >&2
    return 1
  }
  if ! docker info >/dev/null 2>&1; then
    echo "the Docker-compatible runtime is installed but is not running" >&2
    return 1
  fi
  if ! docker image inspect cmux-personal-pi-runner:0.83.0 >/dev/null 2>&1; then
    "$CONTROL_ROOT/personal/ci/setup_pi.sh" "$CONTROL_ROOT"
  fi
}

if [[ "$CHECK_ONLY" == "true" ]]; then
  prerequisite_status=0
  load_deepseek_key || prerequisite_status=1
  require_pi_sandbox || prerequisite_status=1
  if [[ $prerequisite_status -ne 0 ]]; then
    exit "$prerequisite_status"
  fi
  echo "local monitor and automatic conflict resolver prerequisites: ready"
  exit 0
fi

if [[ "$SMOKE_ONLY" == "true" ]]; then
  load_deepseek_key
  require_pi_sandbox
  SMOKE_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/cmux-personal-smoke.XXXXXX")"
  trap 'rm -f "$SMOKE_OUTPUT"' EXIT
  "$CONTROL_ROOT/personal/ci/run_pi_agent.sh" \
    --control "$CONTROL_ROOT" \
    --workspace-root "$CONTROL_ROOT" \
    --working-directory personal \
    --prompt "$CONTROL_ROOT/.github/pi/prompts/deepseek-smoke.txt" \
    --profile smoke \
    --output "$SMOKE_OUTPUT"
  PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/agent_output.py" \
    smoke "$SMOKE_OUTPUT"
  echo "local sandboxed DeepSeek smoke test passed"
  exit 0
fi

if [[ "$CHANNEL" == "auto" ]]; then
  stable_arguments=(--channel stable --data-root "$DATA_ROOT")
  main_arguments=(--channel main --data-root "$DATA_ROOT")
  if [[ "$SCHEDULED" == "true" ]]; then
    stable_arguments+=(--scheduled)
    main_arguments+=(--scheduled)
  fi
  if [[ -n "$TARGET_TAG" ]]; then
    stable_arguments+=(--target-tag "$TARGET_TAG")
  fi

  set +e
  "$CONTROL_ROOT/personal/ci/local_sync.sh" "${stable_arguments[@]}"
  stable_status=$?
  "$CONTROL_ROOT/personal/ci/local_sync.sh" "${main_arguments[@]}"
  main_status=$?
  set -e

  mkdir -p "$DATA_ROOT"
  DATA_ROOT="$(cd "$DATA_ROOT" && pwd)"
  git -C "$CONTROL_ROOT" fetch --force --no-filter origin \
    '+refs/heads/personal-control:refs/remotes/origin/personal-control' \
    '+refs/heads/personal/stable:refs/remotes/origin/personal/stable'
  personal_source_sha="$(
    git -C "$CONTROL_ROOT" rev-parse 'refs/remotes/origin/personal/stable^{commit}'
  )"
  current_stable_tag="$(
    git -C "$CONTROL_ROOT" show \
      refs/remotes/origin/personal-control:personal/state.json |
      jq -er '.current_stable_tag'
  )"
  selection_channel="auto"
  if [[ $stable_status -ne 0 && $main_status -eq 0 ]]; then
    selection_channel="main"
  elif [[ $main_status -ne 0 && $stable_status -eq 0 ]]; then
    selection_channel="stable"
  elif [[ $stable_status -ne 0 && $main_status -ne 0 ]]; then
    selection_channel="none"
  fi
  auto_selection="$(mktemp "${TMPDIR:-/tmp}/cmux-personal-auto-selection.XXXXXX")"
  if [[ "$selection_channel" != "none" ]] && \
    PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/local_candidate.py" \
    select \
    --data-root "$DATA_ROOT" \
    --personal-source-sha "$personal_source_sha" \
    --channel "$selection_channel" \
    --minimum-base-tag "$current_stable_tag" \
    --output "$auto_selection" >/dev/null 2>&1; then
    if [[ -n "$OUTPUT_FILE" ]]; then
      output_parent="$(dirname "$OUTPUT_FILE")"
      mkdir -p "$output_parent"
      install -m 600 "$auto_selection" "$OUTPUT_FILE"
    fi
    jq '{channel: .candidate.channel, source_sha: .candidate.source_sha, base_tag: .candidate.base_tag, integration_status: .candidate.integration_status, run_directory}' "$auto_selection"
    rm -f "$auto_selection"
    if [[ $stable_status -ne 0 || $main_status -ne 0 ]]; then
      echo "one channel stopped, but the selected channel has a valid local candidate" >&2
    fi
    exit 0
  fi
  rm -f "$auto_selection"
  if [[ $stable_status -ne 0 || $main_status -ne 0 ]]; then
    echo "no local candidate is available and at least one channel stopped" >&2
    exit 1
  fi
  if [[ -n "$OUTPUT_FILE" ]]; then
    echo "no locally reviewed candidate is available to build" >&2
    exit 1
  fi
  echo "no new local candidate is waiting"
  exit 0
fi

mkdir -p "$DATA_ROOT/candidates/runs"
DATA_ROOT="$(cd "$DATA_ROOT" && pwd)"
LOCK_DIRECTORY="$DATA_ROOT/sync.lock"
if ! mkdir "$LOCK_DIRECTORY" 2>/dev/null; then
  if [[ "$SCHEDULED" == "true" ]]; then
    echo "another cmux Personal sync is already running"
    exit 0
  fi
  echo "another cmux Personal sync is already running: $LOCK_DIRECTORY" >&2
  exit 1
fi

TEMPORARY_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/cmux-personal-sync.XXXXXX")"
FAILED_STAGE="monitoring upstream"
cleanup() {
  status=$?
  rm -rf "$TEMPORARY_ROOT"
  rmdir "$LOCK_DIRECTORY" 2>/dev/null || true
  if [[ $status -ne 0 && "$(uname -s)" == "Darwin" ]]; then
    /usr/bin/osascript -e \
      "display notification \"Stopped while ${FAILED_STAGE}. See ~/Library/Logs/cmux-personal.\" with title \"cmux Personal sync\"" \
      >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT

notify_candidate() {
  channel_name="$1"
  base_tag="$2"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    /usr/bin/osascript -e \
      "display notification \"${channel_name} candidate for ${base_tag} is ready to build locally.\" with title \"cmux Personal\"" \
      >/dev/null 2>&1 || true
  fi
}

save_monitor_state() {
  install -m 600 "$STATE_FILE" "$DATA_ROOT/monitor-state.json"
}

mark_stable_attempt() {
  status="$1"
  detail="$2"
  next_state="$TEMPORARY_ROOT/next-state.json"
  jq \
    --arg status "$status" \
    --arg detail "$detail" \
    --arg updated_at "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    '.attempt.status = $status
     | .attempt.workflow_run_id = "local"
     | .attempt.updated_at = $updated_at
     | .attempt.detail = $detail' \
    "$STATE_FILE" > "$next_state"
  mv "$next_state" "$STATE_FILE"
  save_monitor_state
}

FAILED_STAGE="fetching your fork state"
git -C "$CONTROL_ROOT" fetch --force --no-filter origin \
  '+refs/heads/personal-control:refs/remotes/origin/personal-control' \
  '+refs/heads/personal/stable:refs/remotes/origin/personal/stable'
REMOTE_STATE="$TEMPORARY_ROOT/remote-state.json"
STATE_FILE="$TEMPORARY_ROOT/state.json"
git -C "$CONTROL_ROOT" show \
  refs/remotes/origin/personal-control:personal/state.json > "$REMOTE_STATE"
if [[ -f "$DATA_ROOT/monitor-state.json" ]]; then
  jq -s '
    .[0] as $remote
    | .[1] as $local
    | $remote
    | .pending_observation = ($local.pending_observation // .pending_observation)
    | .attempt = ($local.attempt // .attempt)
    | .last_check = ($local.last_check // .last_check)
  ' "$REMOTE_STATE" "$DATA_ROOT/monitor-state.json" > "$STATE_FILE"
else
  install -m 600 "$REMOTE_STATE" "$STATE_FILE"
fi

PERSONAL_SOURCE_SHA="$(
  git -C "$CONTROL_ROOT" rev-parse 'refs/remotes/origin/personal/stable^{commit}'
)"
CURRENT_SOURCE_REF="$(jq -er '.current_upstream_main_sha // .current_stable_tag' "$STATE_FILE")"
CURRENT_STABLE_TAG="$(jq -er '.current_stable_tag' "$STATE_FILE")"
RECORDED_PERSONAL_SHA="$(jq -r '.personal_stable_sha // empty' "$STATE_FILE")"
ORIGIN_URL="$(git -C "$CONTROL_ROOT" remote get-url origin)"
UPSTREAM_REPOSITORY="$(jq -er '.upstream_repository' "$CONFIG")"
if [[ ! "$CURRENT_STABLE_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "persisted stable tag is invalid" >&2
  exit 1
fi
if [[ ! "$CURRENT_SOURCE_REF" =~ ^[0-9a-f]{40}$ &&
      ! "$CURRENT_SOURCE_REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "persisted personal source base is invalid" >&2
  exit 1
fi
if [[ ! "$UPSTREAM_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "configured upstream repository is invalid" >&2
  exit 1
fi
UPSTREAM_URL="https://github.com/${UPSTREAM_REPOSITORY}.git"
git -C "$CONTROL_ROOT" fetch --force upstream \
  "refs/tags/${CURRENT_STABLE_TAG}:refs/tags/${CURRENT_STABLE_TAG}"
CURRENT_SOURCE_SHA="$(
  git -C "$CONTROL_ROOT" rev-parse "${CURRENT_SOURCE_REF}^{commit}"
)"

candidate_is_ready() {
  candidate_channel="$1"
  target_sha="$2"
  PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/local_candidate.py" \
    select \
    --data-root "$DATA_ROOT" \
    --personal-source-sha "$PERSONAL_SOURCE_SHA" \
    --channel "$candidate_channel" \
    --target-sha "$target_sha" \
    --minimum-base-tag "$CURRENT_STABLE_TAG" \
    --output "$TEMPORARY_ROOT/existing-${candidate_channel}.json" \
    >/dev/null 2>&1
}

prepare_source_cache() {
  source_cache="$DATA_ROOT/candidates/source-cache.git"
  if ! git -C "$source_cache" rev-parse --is-bare-repository >/dev/null 2>&1; then
    mkdir -p "$(dirname "$source_cache")"
    git init --bare --quiet "$source_cache"
    git -C "$source_cache" remote add origin "$ORIGIN_URL"
  else
    git -C "$source_cache" remote set-url origin "$ORIGIN_URL"
  fi

  git -C "$source_cache" fetch \
    --force \
    --no-filter \
    --no-tags \
    --no-recurse-submodules \
    origin \
    'refs/heads/personal/stable:refs/heads/personal-stable-cache'
  git -C "$source_cache" symbolic-ref HEAD refs/heads/personal-stable-cache

  if GIT_NO_LAZY_FETCH=1 git -C "$source_cache" rev-list \
    --objects \
    --missing=print \
    "$PERSONAL_SOURCE_SHA" | grep '^?' >/dev/null; then
    git -C "$source_cache" fetch \
      --force \
      --refetch \
      --no-filter \
      --no-tags \
      --no-recurse-submodules \
      origin \
      'refs/heads/personal/stable:refs/heads/personal-stable-cache'
  fi
  if GIT_NO_LAZY_FETCH=1 git -C "$source_cache" rev-list \
    --objects \
    --missing=print \
    "$PERSONAL_SOURCE_SHA" | grep '^?' >/dev/null; then
    echo "personal source cache still depends on missing objects" >&2
    return 1
  fi
  printf '%s\n' "$source_cache"
}

prepare_candidate() {
  candidate_channel="$1"
  target_sha="$2"
  base_tag="$3"
  runtime_manifest_asset="$4"
  upstream_main_sha="$5"

  if candidate_is_ready "$candidate_channel" "$target_sha"; then
    echo "local $candidate_channel candidate is already ready for $target_sha"
    return 0
  fi

  FAILED_STAGE="preparing the $candidate_channel candidate"
  run_id="$(date -u '+%Y%m%dT%H%M%SZ')-${candidate_channel}-${target_sha:0:12}-$$"
  run_root="$DATA_ROOT/candidates/runs/$run_id"
  source_root="$run_root/source"
  mkdir -p "$run_root"
  source_cache="$(prepare_source_cache)"
  git clone --shared --no-checkout --quiet "$source_cache" "$source_root"
  git -C "$source_root" remote set-url origin "$ORIGIN_URL"
  git -C "$source_root" switch --detach --quiet "$PERSONAL_SOURCE_SHA"
  "$CONTROL_ROOT/personal/ci/fetch_target_history.sh" "$source_root"
  if [[ "$(git -C "$source_root" rev-parse HEAD)" != "$PERSONAL_SOURCE_SHA" ]]; then
    echo "personal/stable moved while preparing the local candidate" >&2
    return 1
  fi
  git -C "$source_root" remote add upstream "$UPSTREAM_URL"
  git -C "$source_root" fetch --force --no-tags upstream \
    "refs/tags/${base_tag}:refs/tags/${base_tag}"
  if [[ "$candidate_channel" == "main" ]]; then
    git -C "$source_root" fetch --force --no-tags upstream \
      'refs/tags/nightly:refs/tags/upstream-nightly'
    candidate_branch="candidate/main-${target_sha:0:12}"
  else
    candidate_branch="candidate/personal-${base_tag#v}"
  fi
  git -C "$source_root" cat-file -e "${CURRENT_SOURCE_SHA}^{commit}"
  git -C "$source_root" cat-file -e "${target_sha}^{commit}"

  baseline="$run_root/baseline.json"
  conflict_result="$run_root/conflict-result.json"
  candidate_bundle="$run_root/candidate.bundle"
  PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/candidate_manager.py" \
    prepare \
    --repo "$source_root" \
    --source-ref HEAD \
    --current-base-ref "$CURRENT_SOURCE_SHA" \
    --target-ref "$target_sha" \
    --candidate-branch "$candidate_branch" \
    --policy "$CONTROL_ROOT/personal/conflict-policy.json" \
    --baseline "$baseline" \
    --output "$conflict_result" \
    --bundle "$candidate_bundle" \
    --leave-conflicts

  candidate_status="$(jq -er '.status' "$conflict_result")"
  integration_status="clean"
  if [[ "$candidate_status" == "conflict" ]]; then
    if [[ "$(jq -r '.eligible_for_agent // false' "$conflict_result")" != "true" ]]; then
      jq -r '.reasons[]?' "$conflict_result" >&2
      if [[ "$candidate_channel" == "stable" ]]; then
        mark_stable_attempt "blocked" "conflict policy requires manual review"
      fi
      echo "candidate conflict is outside the automatic resolution policy" >&2
      return 1
    fi

    FAILED_STAGE="resolving $candidate_channel conflicts with DeepSeek"
    load_deepseek_key
    require_pi_sandbox
    cp "$baseline" "$source_root/personal-conflict-baseline.json"
    cp "$conflict_result" "$source_root/personal-conflict-result.json"
    cp "$CONTROL_ROOT/personal/conflict-policy.json" \
      "$source_root/personal-conflict-policy.json"
    "$CONTROL_ROOT/personal/ci/run_pi_agent.sh" \
      --control "$CONTROL_ROOT" \
      --workspace-root "$source_root" \
      --working-directory . \
      --prompt "$CONTROL_ROOT/.github/pi/prompts/resolve-cmux-conflicts.txt" \
      --profile resolver

    resolver_output="$run_root/resolver-output.json"
    PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/resolver_report.py" \
      collect \
      --source "$source_root/.cmux-resolver-output.json" \
      --destination "$resolver_output"
    PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/resolver_report.py" \
      require-resolved --report "$resolver_output"
    verification="$run_root/verification.json"
    PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/candidate_manager.py" \
      verify \
      --repo "$source_root" \
      --policy "$CONTROL_ROOT/personal/conflict-policy.json" \
      --baseline "$baseline" \
      --output "$verification" \
      --bundle "$candidate_bundle"

    FAILED_STAGE="independently reviewing the DeepSeek resolution"
    review_context="$run_root/review-context"
    mkdir -p "$review_context"
    cp "$baseline" "$review_context/baseline.json"
    cp "$conflict_result" "$review_context/conflict-result.json"
    cp "$resolver_output" "$review_context/resolver-output.json"
    cp "$CONTROL_ROOT/personal/conflict-policy.json" \
      "$review_context/conflict-policy.json"
    reviewer_output="$TEMPORARY_ROOT/reviewer-${run_id}.json"
    "$CONTROL_ROOT/personal/ci/run_pi_agent.sh" \
      --control "$CONTROL_ROOT" \
      --workspace-root "$run_root" \
      --working-directory source \
      --prompt "$CONTROL_ROOT/.github/pi/prompts/review-cmux-resolution.txt" \
      --profile reviewer \
      --output "$reviewer_output"
    PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/agent_output.py" \
      reviewer "$reviewer_output"
    cp "$reviewer_output" "$run_root/reviewer-output.json"
    PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/conflict_policy.py" \
      review-gate \
      --resolver "$resolver_output" \
      --reviewer "$run_root/reviewer-output.json" \
      --output "$run_root/review-gate.json"
    integration_status="resolved_and_reviewed"
  elif [[ "$candidate_status" != "clean" ]]; then
    echo "candidate manager returned unexpected status: $candidate_status" >&2
    return 1
  fi

  rm -f \
    "$source_root/personal-conflict-baseline.json" \
    "$source_root/personal-conflict-result.json" \
    "$source_root/personal-conflict-policy.json"
  candidate_sha="$(git -C "$source_root" rev-parse HEAD)"
  record="$run_root/candidate.json"
  create_arguments=(
    create
    --channel "$candidate_channel"
    --source-sha "$candidate_sha"
    --personal-source-sha "$PERSONAL_SOURCE_SHA"
    --target-sha "$target_sha"
    --candidate-branch "$candidate_branch"
    --base-tag "$base_tag"
    --runtime-manifest-asset "$runtime_manifest_asset"
    --integration-status "$integration_status"
    --run-directory "$run_root"
    --output "$record"
  )
  if [[ -n "$upstream_main_sha" ]]; then
    create_arguments+=(--upstream-main-sha "$upstream_main_sha")
  fi
  PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/local_candidate.py" \
    "${create_arguments[@]}"
  install -m 600 "$record" "$DATA_ROOT/candidates/${candidate_channel}.json"
  if [[ "$candidate_channel" == "stable" ]]; then
    mark_stable_attempt "awaiting_local_build" "local candidate passed integration gates"
  fi
  notify_candidate "$candidate_channel" "$base_tag"
  echo "local $candidate_channel candidate ready: $candidate_sha"
}

sync_stable() {
  FAILED_STAGE="observing the official stable release"
  observation="$TEMPORARY_ROOT/stable-observation.json"
  monitor_arguments=(
    --config "$CONFIG"
    --state "$STATE_FILE"
    --output "$observation"
  )
  if [[ -n "$TARGET_TAG" ]]; then
    monitor_arguments+=(--target-tag "$TARGET_TAG" --retry)
  elif [[ "$SCHEDULED" != "true" ]]; then
    monitor_arguments+=(--retry)
  fi
  PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/release_monitor.py" \
    "${monitor_arguments[@]}"
  save_monitor_state

  status="$(jq -er '.status' "$observation")"
  ready="$(jq -r '.ready' "$observation")"
  target_tag="$(jq -r '.target_tag // empty' "$observation")"
  if [[ "$ready" != "true" ]]; then
    if [[ "$status" == "current" && -z "$(jq -r '.current_upstream_main_sha // empty' "$STATE_FILE")" && "$PERSONAL_SOURCE_SHA" != "$RECORDED_PERSONAL_SHA" ]]; then
      target_tag="$CURRENT_STABLE_TAG"
    else
      echo "stable monitor: $(jq -r '.detail' "$observation")"
      return 0
    fi
  fi

  git -C "$CONTROL_ROOT" fetch --force upstream \
    "refs/tags/${target_tag}:refs/tags/${target_tag}"
  target_sha="$(git -C "$CONTROL_ROOT" rev-parse "${target_tag}^{commit}")"
  prepare_candidate \
    stable \
    "$target_sha" \
    "$target_tag" \
    cmuxd-remote-manifest.json \
    ""
}

sync_main() {
  FAILED_STAGE="observing the fully published upstream main revision"
  git -C "$CONTROL_ROOT" fetch --force upstream \
    '+refs/heads/main:refs/remotes/upstream/main' \
    '+refs/tags/nightly:refs/tags/upstream-nightly' \
    '+refs/tags/v*:refs/tags/v*'
  latest_main_sha="$(
    git -C "$CONTROL_ROOT" rev-parse 'refs/remotes/upstream/main^{commit}'
  )"
  upstream_sha="$(
    git -C "$CONTROL_ROOT" rev-parse 'refs/tags/upstream-nightly^{commit}'
  )"
  git -C "$CONTROL_ROOT" merge-base --is-ancestor "$upstream_sha" "$latest_main_sha"
  base_tag="$(
    git -C "$CONTROL_ROOT" describe \
      --tags \
      --match 'v[0-9]*' \
      --abbrev=0 \
      "$upstream_sha"
  )"
  current_base_sha="$CURRENT_SOURCE_SHA"
  git -C "$CONTROL_ROOT" merge-base --is-ancestor \
    "$current_base_sha" "$PERSONAL_SOURCE_SHA"
  git -C "$CONTROL_ROOT" merge-base --is-ancestor \
    "$current_base_sha" "$upstream_sha"

  if [[ "$current_base_sha" == "$upstream_sha" && "$PERSONAL_SOURCE_SHA" == "$RECORDED_PERSONAL_SHA" ]]; then
    echo "main monitor: personal/stable already uses the fully published upstream revision"
    return 0
  fi
  if candidate_is_ready main "$upstream_sha"; then
    echo "local main candidate is already ready for $upstream_sha"
    return 0
  fi

  runtime_directory="$TEMPORARY_ROOT/upstream-runtime"
  mkdir -p "$runtime_directory"
  gh release download nightly \
    --repo "$UPSTREAM_REPOSITORY" \
    --pattern cmuxd-remote-manifest.json \
    --dir "$runtime_directory"
  runtime_build="$(
    PYTHONPATH="$CONTROL_ROOT" python3 \
      "$CONTROL_ROOT/personal/official_runtime_manifest.py" \
      --manifest "$runtime_directory/cmuxd-remote-manifest.json" \
      --repository "$UPSTREAM_REPOSITORY" \
      --base-tag "$base_tag" \
      --channel nightly \
      --print-runtime-build
  )"
  runtime_manifest_asset="cmuxd-remote-manifest-${runtime_build}.json"
  gh release download nightly \
    --repo "$UPSTREAM_REPOSITORY" \
    --pattern "$runtime_manifest_asset" \
    --dir "$runtime_directory"
  cmp "$runtime_directory/cmuxd-remote-manifest.json" \
    "$runtime_directory/$runtime_manifest_asset"
  gh attestation verify "$runtime_directory/$runtime_manifest_asset" \
    --repo "$UPSTREAM_REPOSITORY" \
    --signer-workflow "$UPSTREAM_REPOSITORY/.github/workflows/nightly.yml" \
    --signer-digest "$upstream_sha" \
    --source-ref refs/heads/main \
    --source-digest "$upstream_sha"
  PYTHONPATH="$CONTROL_ROOT" python3 \
    "$CONTROL_ROOT/personal/official_runtime_manifest.py" \
    --manifest "$runtime_directory/$runtime_manifest_asset" \
    --repository "$UPSTREAM_REPOSITORY" \
    --base-tag "$base_tag" \
    --channel nightly \
    --runtime-build "$runtime_build" >/dev/null

  observed_again="$(
    git ls-remote "$UPSTREAM_URL" refs/tags/nightly | awk 'NR == 1 { print $1 }'
  )"
  if [[ "$observed_again" != "$upstream_sha" ]]; then
    echo "upstream nightly moved while its runtime provenance was verified" >&2
    return 1
  fi
  prepare_candidate \
    main \
    "$upstream_sha" \
    "$base_tag" \
    "$runtime_manifest_asset" \
    "$upstream_sha"
}

if [[ "$CHANNEL" == "auto" || "$CHANNEL" == "stable" ]]; then
  sync_stable
fi
if [[ "$CHANNEL" == "auto" || "$CHANNEL" == "main" ]]; then
  sync_main
fi

selected="$TEMPORARY_ROOT/selected.json"
if PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/local_candidate.py" \
  select \
  --data-root "$DATA_ROOT" \
  --personal-source-sha "$PERSONAL_SOURCE_SHA" \
  --channel "$CHANNEL" \
  --minimum-base-tag "$CURRENT_STABLE_TAG" \
  --output "$selected" >/dev/null 2>&1; then
  if [[ -n "$OUTPUT_FILE" ]]; then
    output_parent="$(dirname "$OUTPUT_FILE")"
    mkdir -p "$output_parent"
    install -m 600 "$selected" "$OUTPUT_FILE"
  fi
  jq '{channel: .candidate.channel, source_sha: .candidate.source_sha, base_tag: .candidate.base_tag, integration_status: .candidate.integration_status, run_directory}' "$selected"
elif [[ -n "$OUTPUT_FILE" ]]; then
  echo "no locally reviewed candidate is available to build" >&2
  exit 1
else
  echo "no new local candidate is waiting"
fi
