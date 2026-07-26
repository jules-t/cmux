#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="${1:-$(git rev-parse --show-toplevel)}"
ACTIONLINT_VERSION="1.7.12"
SHELLCHECK_VERSION="0.11.0"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)
    ACTIONLINT_PLATFORM="darwin_arm64"
    ACTIONLINT_SHA256="aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f"
    SHELLCHECK_PLATFORM="darwin.aarch64"
    SHELLCHECK_SHA256="339b930feb1ea764467013cc1f72d09cd6b869ebf1013296ba9055ab2ffbd26f"
    ;;
  Darwin-x86_64)
    ACTIONLINT_PLATFORM="darwin_amd64"
    ACTIONLINT_SHA256="5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644"
    SHELLCHECK_PLATFORM="darwin.x86_64"
    SHELLCHECK_SHA256="c2c15e08df0e8fbc374c335b230a7ee958c313fa5714817a59aa59f1aa594f51"
    ;;
  Linux-aarch64 | Linux-arm64)
    ACTIONLINT_PLATFORM="linux_arm64"
    ACTIONLINT_SHA256="325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6"
    SHELLCHECK_PLATFORM="linux.aarch64"
    SHELLCHECK_SHA256="68a8133197a50beb8803f8d42f9908d1af1c5540d4bb05fdfca8c1fa47decefc"
    ;;
  Linux-x86_64)
    ACTIONLINT_PLATFORM="linux_amd64"
    ACTIONLINT_SHA256="8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
    SHELLCHECK_PLATFORM="linux.x86_64"
    SHELLCHECK_SHA256="b7af85e41cc99489dcc21d66c6d5f3685138f06d34651e6d34b42ec6d54fe6f6"
    ;;
  *)
    echo "unsupported actionlint host: $(uname -s)-$(uname -m)" >&2
    exit 1
    ;;
esac

TEMPORARY_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/cmux-personal-control-check.XXXXXX")"
trap 'rm -rf "$TEMPORARY_DIRECTORY"' EXIT

ACTIONLINT_ARCHIVE="$TEMPORARY_DIRECTORY/actionlint.tar.gz"
ACTIONLINT_URL="https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_${ACTIONLINT_PLATFORM}.tar.gz"
SHELLCHECK_ARCHIVE="$TEMPORARY_DIRECTORY/shellcheck.tar.gz"
SHELLCHECK_URL="https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.${SHELLCHECK_PLATFORM}.tar.gz"
curl --fail --location --silent --show-error "$ACTIONLINT_URL" --output "$ACTIONLINT_ARCHIVE"
curl --fail --location --silent --show-error "$SHELLCHECK_URL" --output "$SHELLCHECK_ARCHIVE"
python3 - \
  "$ACTIONLINT_ARCHIVE" "$ACTIONLINT_SHA256" \
  "$SHELLCHECK_ARCHIVE" "$SHELLCHECK_SHA256" <<'PY'
import hashlib
import pathlib
import sys

for raw_path, expected in zip(sys.argv[1::2], sys.argv[2::2]):
    path = pathlib.Path(raw_path)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"{path.name} checksum mismatch: {actual} != {expected}")
PY
tar -xzf "$ACTIONLINT_ARCHIVE" -C "$TEMPORARY_DIRECTORY" actionlint
tar -xzf "$SHELLCHECK_ARCHIVE" -C "$TEMPORARY_DIRECTORY"
SHELLCHECK_BINARY="$TEMPORARY_DIRECTORY/shellcheck-v${SHELLCHECK_VERSION}/shellcheck"

cd "$REPOSITORY_ROOT"
shopt -s nullglob
WORKFLOW_FILES=(.github/workflows/*.yml .github/workflows/*.yaml)
shopt -u nullglob
if (( ${#WORKFLOW_FILES[@]} == 0 )); then
  echo "no GitHub Actions workflows found" >&2
  exit 1
fi
"$TEMPORARY_DIRECTORY/actionlint" \
  -no-color \
  -shellcheck="$SHELLCHECK_BINARY" \
  -pyflakes= \
  "${WORKFLOW_FILES[@]}"

bash -n personal/ci/*.sh
"$SHELLCHECK_BINARY" personal/ci/*.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v -s personal/tests -t .
PYTHONDONTWRITEBYTECODE=1 python3 personal/workflow_contracts.py --root "$REPOSITORY_ROOT"
