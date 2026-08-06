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

# A blobless checkout can have the complete commit graph while still depending
# on lazy object fetches from its promisor remote. GitHub does not guarantee
# that an individual historical object can be fetched by object ID, even when
# the commit which references it is present in a fork. Rebase then fails with
# "upload-pack: not our ref" after several commits have already been replayed.
# Re-fetch this one immutable history without a filter so candidate preparation
# is self-contained, while still avoiding unrelated branches and tags.
if GIT_NO_LAZY_FETCH=1 git -C "$REPOSITORY_ROOT" rev-list \
  --objects \
  --missing=print \
  "$TARGET_SHA" | grep '^?' >/dev/null; then
  git -C "$REPOSITORY_ROOT" \
    -c protocol.version=2 \
    fetch \
    --refetch \
    --no-filter \
    --no-tags \
    --no-recurse-submodules \
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
if GIT_NO_LAZY_FETCH=1 git -C "$REPOSITORY_ROOT" rev-list \
  --objects \
  --missing=print \
  "$TARGET_SHA" | grep '^?' >/dev/null; then
  echo "target history still depends on missing promisor objects" >&2
  exit 1
fi

echo "target history ready: $TARGET_SHA"
