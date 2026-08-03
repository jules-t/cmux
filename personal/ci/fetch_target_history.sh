#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <repository-root>" >&2
  exit 2
fi

REPOSITORY_ROOT="$1"
if [[ "$(git -C "$REPOSITORY_ROOT" rev-parse --is-inside-work-tree)" != "true" ]]; then
  echo "target is not a Git worktree: $REPOSITORY_ROOT" >&2
  exit 1
fi

TARGET_SHA="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
if [[ "$(git -C "$REPOSITORY_ROOT" rev-parse --is-shallow-repository)" == "true" ]]; then
  git -C "$REPOSITORY_ROOT" \
    -c protocol.version=2 \
    fetch \
    --filter=blob:none \
    --no-tags \
    --no-recurse-submodules \
    --unshallow \
    origin \
    "$TARGET_SHA"
fi

if [[ "$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)" != "$TARGET_SHA" ]]; then
  echo "target checkout moved while fetching history" >&2
  exit 1
fi
if [[ "$(git -C "$REPOSITORY_ROOT" rev-parse --is-shallow-repository)" == "true" ]]; then
  echo "target history is still shallow after the immutable fetch" >&2
  exit 1
fi

echo "target history ready: $TARGET_SHA"
