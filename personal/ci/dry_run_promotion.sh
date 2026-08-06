#!/usr/bin/env bash
set -euo pipefail

# Replay the personal commits onto a candidate upstream target in a throwaway
# worktree, using the same candidate_manager.py the pipeline uses. Nothing is
# pushed and no branch moves, so this is safe to run against a live checkout.

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <target-ref> [source-ref]" >&2
  echo "example: $0 v0.64.22" >&2
  exit 2
fi

TARGET_REF="$1"
SOURCE_REF="${2:-personal/stable}"
CONTROL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ ! -f "$CONTROL_ROOT/personal/state.json" ]]; then
  echo "run this from a control-plane checkout: $CONTROL_ROOT" >&2
  exit 1
fi

CURRENT_BASE="$(
  jq -er '.current_upstream_main_sha // .current_stable_tag' \
    "$CONTROL_ROOT/personal/state.json"
)"

WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/cmux-personal-dry-run.XXXXXX")"
REPORT="$WORKTREE/conflict-result.json"
BASELINE="$WORKTREE/baseline.json"
SOURCE="$WORKTREE/source"
trap 'git -C "$CONTROL_ROOT" worktree remove --force "$SOURCE" 2>/dev/null || true; rm -rf "$WORKTREE"' EXIT

git -C "$CONTROL_ROOT" worktree add --detach --quiet "$SOURCE" "$SOURCE_REF"

echo "replaying $SOURCE_REF from $CURRENT_BASE onto $TARGET_REF"
PYTHONPATH="$CONTROL_ROOT" python3 "$CONTROL_ROOT/personal/candidate_manager.py" prepare \
  --repo "$SOURCE" \
  --source-ref HEAD \
  --current-base-ref "$CURRENT_BASE" \
  --target-ref "$TARGET_REF" \
  --candidate-branch "candidate/dry-run" \
  --policy "$CONTROL_ROOT/personal/conflict-policy.json" \
  --baseline "$BASELINE" \
  --output "$REPORT"

jq -r '
  "status: \(.status)",
  "detail: \((.detail // "none") | split("\n") | last)",
  "agent eligible: \(.eligible_for_agent // false)",
  (if (.conflicted_paths // []) | length > 0
   then "conflicted paths:", (.conflicted_paths[] | "  \(.)")
   else "conflicted paths: none" end)
' "$REPORT"

# Mirror the local pipeline's verdict: a clean replay builds directly, an
# agent-eligible conflict goes to the resolver, anything else stops promotion.
STATUS="$(jq -r '.status' "$REPORT")"
if [[ "$STATUS" == "clean" ]]; then
  echo "verdict: the local candidate can build directly"
elif [[ "$(jq -r '.eligible_for_agent // false' "$REPORT")" == "true" ]]; then
  echo "verdict: promotion would go through the conflict resolver and review gate"
else
  echo "verdict: promotion would be blocked for human review" >&2
  exit 1
fi
