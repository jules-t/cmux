#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="${1:-$(git rev-parse --show-toplevel)}"
SHELLCHECK_VERSION="0.11.0"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)
    SHELLCHECK_PLATFORM="darwin.aarch64"
    SHELLCHECK_SHA256="339b930feb1ea764467013cc1f72d09cd6b869ebf1013296ba9055ab2ffbd26f"
    ;;
  Darwin-x86_64)
    SHELLCHECK_PLATFORM="darwin.x86_64"
    SHELLCHECK_SHA256="c2c15e08df0e8fbc374c335b230a7ee958c313fa5714817a59aa59f1aa594f51"
    ;;
  Linux-aarch64 | Linux-arm64)
    SHELLCHECK_PLATFORM="linux.aarch64"
    SHELLCHECK_SHA256="68a8133197a50beb8803d42f9908d1af1c5540d4bb05fdfca8c1fa47decefc"
    ;;
  Linux-x86_64)
    SHELLCHECK_PLATFORM="linux.x86_64"
    SHELLCHECK_SHA256="b7af85e41cc99489dcc21d66c6d5f3685138f06d34651e6d34b42ec6d54fe6f6"
    ;;
  *)
    echo "unsupported shellcheck host: $(uname -s)-$(uname -m)" >&2
    exit 1
    ;;
esac

TEMPORARY_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/cmux-personal-control-check.XXXXXX")"
trap 'rm -rf "$TEMPORARY_DIRECTORY"' EXIT

SHELLCHECK_ARCHIVE="$TEMPORARY_DIRECTORY/shellcheck.tar.gz"
SHELLCHECK_URL="https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.${SHELLCHECK_PLATFORM}.tar.gz"
curl --fail --location --silent --show-error "$SHELLCHECK_URL" --output "$SHELLCHECK_ARCHIVE"
python3 - "$SHELLCHECK_ARCHIVE" "$SHELLCHECK_SHA256" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
actual = hashlib.sha256(path.read_bytes()).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit(f"{path.name} checksum mismatch: {actual} != {sys.argv[2]}")
PY
tar -xzf "$SHELLCHECK_ARCHIVE" -C "$TEMPORARY_DIRECTORY"
SHELLCHECK_BINARY="$TEMPORARY_DIRECTORY/shellcheck-v${SHELLCHECK_VERSION}/shellcheck"

cd "$REPOSITORY_ROOT"
shopt -s nullglob
PERSONAL_WORKFLOWS=(.github/workflows/personal-*.yml .github/workflows/personal-*.yaml)
shopt -u nullglob
if (( ${#PERSONAL_WORKFLOWS[@]} > 0 )); then
  echo "personal GitHub workflows are forbidden by the local-first architecture" >&2
  exit 1
fi
bash -n personal/ci/*.sh
"$SHELLCHECK_BINARY" personal/ci/*.sh
npm ci \
  --prefix personal/pi \
  --ignore-scripts \
  --no-audit \
  --no-fund
npm --prefix personal/pi run check
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v -s personal/tests -t .
