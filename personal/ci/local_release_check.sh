#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: personal/ci/local_release_check.sh [--check] [--validate-only] [--channel <auto|stable|main>] [--output-root <directory>]

Prepare or reuse the exact candidate produced by the local integration,
DeepSeek conflict-resolution, and independent-review gates. Perform the Release
build, selected unit tests, smoke launches, packaging, and asset checks locally.
This command never changes a GitHub branch or release.

Options:
  --check                    Check local prerequisites without building.
  --validate-only            Build and verify without installing the result.
  --channel <channel>        Candidate channel to build (default: auto).
  --output-root <directory>  Persistent build runs and caches.
EOF
}

CONTROL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_ROOT="${CMUX_PERSONAL_DATA_ROOT:-$HOME/.local/share/cmux-personal}"
OUTPUT_ROOT="$DATA_ROOT/builds"
CHECK_ONLY="false"
INSTALL_RESULT="true"
CANDIDATE_CHANNEL="auto"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      CHECK_ONLY="true"
      shift
      ;;
    --validate-only)
      INSTALL_RESULT="false"
      shift
      ;;
    --channel)
      CANDIDATE_CHANNEL="${2:-}"
      shift 2
      ;;
    --output-root)
      OUTPUT_ROOT="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$CANDIDATE_CHANNEL" != "auto" &&
      "$CANDIDATE_CHANNEL" != "stable" &&
      "$CANDIDATE_CHANNEL" != "main" ]]; then
  echo "candidate channel must be auto, stable, or main" >&2
  exit 2
fi

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "local release validation requires an Apple Silicon Mac" >&2
  exit 1
fi
for command in curl git gh jq python3 xcrun xcodebuild; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "missing required command: $command" >&2
    exit 1
  fi
done

XCODE_DEVELOPER_DIR=""
while IFS= read -r app; do
  developer_dir="$app/Contents/Developer"
  sdk_version="$(DEVELOPER_DIR="$developer_dir" xcrun --sdk macosx --show-sdk-version 2>/dev/null || true)"
  if [[ "$sdk_version" == 26.* ]]; then
    XCODE_DEVELOPER_DIR="$developer_dir"
  fi
done < <(find /Applications -maxdepth 1 -name 'Xcode*.app' -print 2>/dev/null | sort)
if [[ -z "$XCODE_DEVELOPER_DIR" ]]; then
  cat >&2 <<'EOF'
Xcode 26 is required before the local release build can run.
Install the current stable Xcode 26 in /Applications, open it once to finish
component installation, then run:
  sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
  sudo xcodebuild -license accept
Afterward, rerun: personal/ci/local_release_check.sh --check
EOF
  exit 1
fi
export DEVELOPER_DIR="$XCODE_DEVELOPER_DIR"
if [[ "$(xcrun --sdk macosx --show-sdk-version)" != 26.* ]]; then
  echo "selected Xcode does not provide the required macOS 26 SDK" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI authentication is required; run: gh auth login" >&2
  exit 1
fi
if [[ "$CHECK_ONLY" == "true" ]]; then
  "$CONTROL_ROOT/personal/ci/local_sync.sh" --check --data-root "$DATA_ROOT"
  echo "local release prerequisites: ready"
  xcodebuild -version
  exit 0
fi

OUTPUT_ROOT="$(mkdir -p "$OUTPUT_ROOT" && cd "$OUTPUT_ROOT" && pwd)"
LOCK_DIRECTORY="$OUTPUT_ROOT/.validation-lock"
if ! mkdir "$LOCK_DIRECTORY" 2>/dev/null; then
  echo "another local release validation appears to be running: $LOCK_DIRECTORY" >&2
  exit 1
fi
cleanup() {
  rmdir "$LOCK_DIRECTORY" >/dev/null 2>&1 || true
}
trap cleanup EXIT

UPSTREAM_REPOSITORY="$(jq -er '.upstream_repository' "$CONTROL_ROOT/personal/config.json")"
FORK_REPOSITORY="$(jq -er '.fork_repository' "$CONTROL_ROOT/personal/config.json")"
ORIGIN_URL="$(git -C "$CONTROL_ROOT" remote get-url origin)"

SELECTION="$(mktemp "$OUTPUT_ROOT/.selection.XXXXXX")"
trap 'rm -f "$SELECTION"; cleanup' EXIT
"$CONTROL_ROOT/personal/ci/local_sync.sh" \
  --channel "$CANDIDATE_CHANNEL" \
  --data-root "$DATA_ROOT" \
  --output "$SELECTION"

CHANNEL="$(jq -er '.candidate.channel' "$SELECTION")"
SOURCE_SHA="$(jq -er '.candidate.source_sha' "$SELECTION")"
PERSONAL_SOURCE_SHA="$(jq -er '.candidate.personal_source_sha' "$SELECTION")"
BASE_TAG="$(jq -er '.candidate.base_tag' "$SELECTION")"
UPSTREAM_SHA="$(jq -r '.candidate.upstream_main_sha // empty' "$SELECTION")"
RUNTIME_MANIFEST_ASSET="$(jq -er '.candidate.runtime_manifest_asset' "$SELECTION")"
INTEGRATION_STATUS="$(jq -er '.candidate.integration_status' "$SELECTION")"
CANDIDATE_RUN_ROOT="$(jq -er '.run_directory' "$SELECTION")"
CANDIDATE_BUNDLE="$CANDIDATE_RUN_ROOT/candidate.bundle"

git -C "$CONTROL_ROOT" fetch --force --no-filter origin \
  '+refs/heads/personal/stable:refs/remotes/origin/personal/stable'
if [[ "$(git -C "$CONTROL_ROOT" rev-parse 'refs/remotes/origin/personal/stable^{commit}')" != "$PERSONAL_SOURCE_SHA" ]]; then
  echo "personal/stable moved after the local candidate was reviewed; sync again" >&2
  exit 1
fi

RUN_ID="$(date -u '+%Y%m%dT%H%M%SZ')-${CHANNEL}-${SOURCE_SHA:0:12}-$$"
RUN_ROOT="$OUTPUT_ROOT/runs/$RUN_ID"
METADATA="$RUN_ROOT/metadata"
ASSETS="$RUN_ROOT/assets"
CACHE="$OUTPUT_ROOT/cache"
WORKTREE="$RUN_ROOT/source"
mkdir -p "$METADATA" "$ASSETS" "$CACHE"
cp "$SELECTION" "$METADATA/local-candidate.json"
cp "$CANDIDATE_BUNDLE" "$RUN_ROOT/candidate.bundle"
PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/candidate_manager.py" \
  clone-bundle \
  --bundle "$RUN_ROOT/candidate.bundle" \
  --destination "$WORKTREE" \
  --expected-commit "$SOURCE_SHA"
git -C "$WORKTREE" remote set-url origin "$ORIGIN_URL"
git -C "$WORKTREE" remote add upstream "https://github.com/${UPSTREAM_REPOSITORY}.git"
if [[ "$(git -C "$WORKTREE" rev-parse HEAD)" != "$SOURCE_SHA" ]]; then
  echo "local source clone does not match the reviewed candidate SHA" >&2
  exit 1
fi
git -C "$WORKTREE" fetch --force --no-filter --no-tags upstream \
  "refs/tags/${BASE_TAG}:refs/tags/${BASE_TAG}"
git -C "$WORKTREE" merge-base --is-ancestor "$BASE_TAG" "$SOURCE_SHA"
if [[ "$CHANNEL" == "main" ]]; then
  git -C "$WORKTREE" merge-base --is-ancestor "$BASE_TAG" "$UPSTREAM_SHA"
  git -C "$WORKTREE" merge-base --is-ancestor "$UPSTREAM_SHA" "$SOURCE_SHA"
  if ! git -C "$WORKTREE" diff --quiet "$UPSTREAM_SHA" "$SOURCE_SHA" -- daemon/remote; then
    echo "personal daemon changes cannot reuse verified upstream-main runtime assets" >&2
    exit 1
  fi
fi
git -C "$WORKTREE" bundle verify "$RUN_ROOT/candidate.bundle"

RUNTIME_DIRECTORY="$METADATA/official-runtime"
mkdir -p "$RUNTIME_DIRECTORY"
if [[ "$CHANNEL" == "main" ]]; then
  gh release download nightly \
    --repo "$UPSTREAM_REPOSITORY" \
    --pattern "$RUNTIME_MANIFEST_ASSET" \
    --dir "$RUNTIME_DIRECTORY"
  gh attestation verify "$RUNTIME_DIRECTORY/$RUNTIME_MANIFEST_ASSET" \
    --repo "$UPSTREAM_REPOSITORY" \
    --signer-workflow "$UPSTREAM_REPOSITORY/.github/workflows/nightly.yml" \
    --signer-digest "$UPSTREAM_SHA" \
    --source-ref refs/heads/main \
    --source-digest "$UPSTREAM_SHA"
  RUNTIME_BUILD="${RUNTIME_MANIFEST_ASSET#cmuxd-remote-manifest-}"
  RUNTIME_BUILD="${RUNTIME_BUILD%.json}"
  PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/official_runtime_manifest.py" \
    --manifest "$RUNTIME_DIRECTORY/$RUNTIME_MANIFEST_ASSET" \
    --repository "$UPSTREAM_REPOSITORY" \
    --base-tag "$BASE_TAG" \
    --channel nightly \
    --runtime-build "$RUNTIME_BUILD"
else
  gh release download "$BASE_TAG" \
    --repo "$UPSTREAM_REPOSITORY" \
    --pattern cmuxd-remote-manifest.json \
    --dir "$RUNTIME_DIRECTORY"
  BASE_SOURCE_SHA="$(git -C "$WORKTREE" rev-parse "${BASE_TAG}^{commit}")"
  gh attestation verify "$RUNTIME_DIRECTORY/cmuxd-remote-manifest.json" \
    --repo "$UPSTREAM_REPOSITORY" \
    --signer-workflow "$UPSTREAM_REPOSITORY/.github/workflows/release.yml" \
    --signer-digest "$BASE_SOURCE_SHA" \
    --source-ref "refs/tags/${BASE_TAG}" \
    --source-digest "$BASE_SOURCE_SHA"
  PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/official_runtime_manifest.py" \
    --manifest "$RUNTIME_DIRECTORY/cmuxd-remote-manifest.json" \
    --repository "$UPSTREAM_REPOSITORY" \
    --base-tag "$BASE_TAG" \
    --channel stable
fi

PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/release_naming.py" \
  --repo "$WORKTREE" \
  --base-tag "$BASE_TAG" \
  --remote "$ORIGIN_URL" \
  --repository "$FORK_REPOSITORY" \
  --local-build-root "$OUTPUT_ROOT/runs" \
  --output "$METADATA/release-name.json"
PERSONAL_TAG="$(jq -er '.personal_tag' "$METADATA/release-name.json")"

echo "Preparing Xcode, submodules, Zig, and the real Ghostty helper"
git -C "$WORKTREE" submodule update --init --recursive
TOOL_ENV="$METADATA/tool-env"
: > "$TOOL_ENV"
GITHUB_ENV="$TOOL_ENV" \
CMUX_CI_REQUIRED_MACOS_SDK_MAJOR=26 \
CMUX_CI_DEVELOPER_DIR="$DEVELOPER_DIR" \
  "$WORKTREE/scripts/select-ci-xcode.sh"
set -a
# shellcheck disable=SC1090
source "$TOOL_ENV"
set +a
ZIG_REQUIRED="$(/bin/bash "$WORKTREE/scripts/ghostty-zig-version.sh" "$WORKTREE")"
CACHED_ZIG="$CACHE/zig/zig-aarch64-macos-${ZIG_REQUIRED}/zig"
if [[ -x "$CACHED_ZIG" ]]; then
  CACHED_ZIG_DIRECTORY="$(dirname "$CACHED_ZIG")"
  export PATH="$CACHED_ZIG_DIRECTORY:$PATH"
fi
GITHUB_ENV="$TOOL_ENV" \
RUNNER_TEMP="$CACHE" \
ZIG_REQUIRED="$ZIG_REQUIRED" \
ZIG_FORCE_LOCAL_INSTALL=0 \
ZIG_INSTALL_ROOT="$CACHE/zig" \
  "$WORKTREE/scripts/install-zig-ci.sh"
set -a
# shellcheck disable=SC1090
source "$TOOL_ENV"
set +a
if [[ -n "${CMUX_ZIG:-}" ]]; then
  ZIG_DIRECTORY="$(dirname "$CMUX_ZIG")"
  export PATH="$ZIG_DIRECTORY:$PATH"
fi
mkdir -p "$CACHE/ghostty-helper"
CMUX_ZIG="${CMUX_ZIG:-}" "$WORKTREE/scripts/build-ghostty-cli-helper.sh" \
  --target aarch64-macos \
  --output "$CACHE/ghostty-helper/ghostty"

export CARGO_HOME="$CACHE/cargo"
export RUSTUP_HOME="$CACHE/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
if [[ ! -x "$CARGO_HOME/bin/rustup" ]]; then
  echo "Installing Rust in the local build cache without modifying shell profiles"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | /bin/sh -s -- -y --no-modify-path --profile minimal --default-toolchain stable
fi

echo "Building and validating $SOURCE_SHA locally; no release has been started"
env \
  -u GITHUB_WORKFLOW_REF \
  -u GITHUB_RUN_ID \
  -u GITHUB_RUN_ATTEMPT \
  CMUX_PERSONAL_BUILD_ORIGIN=local \
  RUNNER_TEMP="$CACHE" \
  "$CONTROL_ROOT/personal/ci/build_personal.sh" \
  "$WORKTREE" \
  "$CONTROL_ROOT" \
  "$ASSETS" \
  "$CACHE/derived-data" \
  "$SOURCE_SHA" \
  "$BASE_TAG" \
  "$PERSONAL_TAG" \
  "$CACHE/ghostty-helper/ghostty" \
  "$RUNTIME_DIRECTORY/$RUNTIME_MANIFEST_ASSET" \
  "$UPSTREAM_SHA" \
  "$RUNTIME_MANIFEST_ASSET"

PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/local_validation.py" create \
  --root "$RUN_ROOT" \
  --assets "$ASSETS" \
  --candidate-bundle "$RUN_ROOT/candidate.bundle" \
  --candidate "$METADATA/local-candidate.json" \
  --config "$CONTROL_ROOT/personal/config.json" \
  --source-sha "$SOURCE_SHA" \
  --base-tag "$BASE_TAG" \
  --personal-tag "$PERSONAL_TAG" \
  --output "$RUN_ROOT/validation-receipt.json"

if [[ "$INSTALL_RESULT" == "true" ]]; then
  PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/local_installer.py" \
    --receipt "$RUN_ROOT/validation-receipt.json" \
    --config "$CONTROL_ROOT/personal/config.json"
fi

cat <<EOF

Local release validation passed.
Candidate channel: $CHANNEL
Integration gate: $INTEGRATION_STATUS (local resolver/reviewer)
No GitHub branch or release was changed by this local command.
Receipt: $RUN_ROOT/validation-receipt.json
Archive: $ASSETS/cmux-personal-macos-arm64.zip
EOF
