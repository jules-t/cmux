#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 10 ]]; then
  echo "usage: $0 <source-root> <control-root> <dist-root> <derived-data> <source-sha> <base-tag> <personal-tag> <ghostty-helper> <remote-daemon-manifest> <upstream-main-sha>" >&2
  exit 2
fi

SOURCE_ROOT="$(cd "$1" && pwd)"
CONTROL_ROOT="$(cd "$2" && pwd)"
DIST_ROOT="$3"
DERIVED_DATA="$4"
TEST_DERIVED_DATA="${DERIVED_DATA}-tests"
SOURCE_SHA="$5"
BASE_TAG="$6"
PERSONAL_TAG="$7"
GHOSTTY_HELPER_SOURCE="$(cd "$(dirname "$8")" && pwd)/$(basename "$8")"
REMOTE_DAEMON_MANIFEST="$(cd "$(dirname "$9")" && pwd)/$(basename "$9")"
UPSTREAM_MAIN_SHA="${10}"
CONFIG="$CONTROL_ROOT/personal/config.json"
export PYTHONPATH="$CONTROL_ROOT${PYTHONPATH:+:$PYTHONPATH}"

APP_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["app_name"])' "$CONFIG")"
BUNDLE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle_identifier"])' "$CONFIG")"
ARCHIVE_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifact_name"])' "$CONFIG")"
MANIFEST_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["manifest_name"])' "$CONFIG")"

if [[ ! -f "$GHOSTTY_HELPER_SOURCE" ]]; then
  echo "real Ghostty CLI helper is missing: $GHOSTTY_HELPER_SOURCE" >&2
  exit 1
fi
# GitHub artifact downloads intentionally normalize regular files to mode 0644.
# Validate the payload itself here; install(1) restores its executable mode below.
lipo "$GHOSTTY_HELPER_SOURCE" -verify_arch arm64
if strings "$GHOSTTY_HELPER_SOURCE" | grep -Fq "ghostty CLI helper stub"; then
  echo "refusing placeholder Ghostty CLI helper" >&2
  exit 1
fi

mkdir -p "$DIST_ROOT" "$DERIVED_DATA" "$TEST_DERIVED_DATA"
cd "$SOURCE_ROOT"

./scripts/check-pbxproj.sh
./scripts/lint-pbxproj-test-wiring.sh
python3 -m json.tool Resources/Localizable.xcstrings >/dev/null

SMOKE_BINARY="${RUNNER_TEMP:-/tmp}/cmux-personal-syntax-smoke"
xcrun swiftc \
  "$CONTROL_ROOT/personal/ci/SyntaxSmoke.swift" \
  Sources/Panels/FilePreviewSyntaxCursor.swift \
  Sources/Panels/FilePreviewSyntaxGrammar.swift \
  Sources/Panels/FilePreviewSyntaxKeywords.swift \
  Sources/Panels/FilePreviewSyntaxLanguage.swift \
  Sources/Panels/FilePreviewSyntaxToken.swift \
  Sources/Panels/FilePreviewSyntaxTokenKind.swift \
  Sources/Panels/FilePreviewSyntaxTokenizer.swift \
  Sources/Panels/FilePreviewSyntaxTypes.swift \
  -o "$SMOKE_BINARY"
"$SMOKE_BINARY"

./scripts/install-rust-ci.sh
export PATH="$HOME/.cargo/bin:$PATH"
./scripts/download-prebuilt-ghosttykit.sh

SOURCE_PACKAGES_DIR="${RUNNER_TEMP:-/tmp}/cmux-personal-spm"
mkdir -p "$SOURCE_PACKAGES_DIR"

for attempt in 1 2 3; do
  if xcodebuild \
    -project cmux.xcodeproj \
    -scheme cmux \
    -configuration Release \
    -derivedDataPath "$DERIVED_DATA" \
    -clonedSourcePackagesDirPath "$SOURCE_PACKAGES_DIR" \
    -resolvePackageDependencies; then
    if [[ -d "$SOURCE_PACKAGES_DIR/artifacts/sparkle/Sparkle/Sparkle.xcframework" &&
          -d "$SOURCE_PACKAGES_DIR/artifacts/sentry-cocoa/Sentry/Sentry.xcframework" ]]; then
      break
    fi
    echo "package resolution completed without required binary artifacts; clearing the package cache" >&2
    rm -rf "$SOURCE_PACKAGES_DIR"
  fi
  if [[ "$attempt" -eq 3 ]]; then
    echo "failed to resolve Swift packages after 3 attempts" >&2
    exit 1
  fi
  echo "package resolution failed on attempt $attempt; retrying" >&2
  sleep $((attempt * 5))
done

CMUX_SKIP_ZIG_BUILD=1 xcodebuild \
  -project cmux.xcodeproj \
  -scheme cmux \
  -configuration Release \
  -destination 'generic/platform=macOS' \
  -derivedDataPath "$DERIVED_DATA" \
  -clonedSourcePackagesDirPath "$SOURCE_PACKAGES_DIR" \
  -disableAutomaticPackageResolution \
  ARCHS=arm64 \
  ONLY_ACTIVE_ARCH=YES \
  CODE_SIGNING_ALLOWED=NO \
  PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE_ID" \
  INFOPLIST_KEY_CFBundleName="$APP_NAME" \
  INFOPLIST_KEY_CFBundleDisplayName="$APP_NAME" \
  CMUX_SIDEBAR_EXTENSION_POINT_ID="${BUNDLE_ID}.cmux.sidebar" \
  build

BUILT_APP="$DERIVED_DATA/Build/Products/Release/cmux.app"
if [[ ! -d "$BUILT_APP" ]]; then
  echo "built app not found: $BUILT_APP" >&2
  exit 1
fi

PRESERVED_ROOT="${RUNNER_TEMP:-/tmp}/cmux-personal-preserved"
PRESERVED_APP="$PRESERVED_ROOT/cmux.app"
if [[ -e "$PRESERVED_ROOT" ]]; then
  rm -rf "$PRESERVED_ROOT"
fi
mkdir -p "$PRESERVED_ROOT"
ditto "$BUILT_APP" "$PRESERVED_APP"

CMUX_XCODEBUILD_NONINTERACTIVE_IDLE_TIMEOUT_SECONDS=1800 \
CMUX_XCODEBUILD_NONINTERACTIVE_POST_TEST_TIMEOUT_SECONDS=180 \
CMUX_APP_HOST_XCODEBUILD_ATTEMPTS=2 \
GITHUB_WORKSPACE="$SOURCE_ROOT" \
  "$SOURCE_ROOT/scripts/ci/run-in-console-session.sh" \
  "$SOURCE_ROOT/scripts/ci/run-app-host-xcodebuild.sh" \
  -project cmux.xcodeproj \
  -scheme cmux-unit \
  -configuration Debug \
  -derivedDataPath "$TEST_DERIVED_DATA" \
  -clonedSourcePackagesDirPath "$SOURCE_PACKAGES_DIR" \
  -disableAutomaticPackageResolution \
  -destination "platform=macOS" \
  CMUX_SKIP_ZIG_BUILD=1 \
  ARCHS=arm64 \
  ONLY_ACTIVE_ARCH=YES \
  -only-testing:cmuxTests/FilePreviewSyntaxHighlighterTests \
  -only-testing:cmuxTests/FilePreviewSyntaxHighlightSettingsFileStoreTests \
  test

PERSONAL_APP="$DIST_ROOT/$APP_NAME.app"
if [[ -e "$PERSONAL_APP" ]]; then
  rm -rf "$PERSONAL_APP"
fi
ditto "$PRESERVED_APP" "$PERSONAL_APP"

python3 "$CONTROL_ROOT/personal/package_bundle.py" \
  --plist "$PERSONAL_APP/Contents/Info.plist" \
  --config "$CONFIG" \
  --source-sha "$SOURCE_SHA" \
  --base-tag "$BASE_TAG" \
  --personal-tag "$PERSONAL_TAG" \
  --upstream-main-sha "$UPSTREAM_MAIN_SHA" \
  --remote-daemon-manifest "$REMOTE_DAEMON_MANIFEST"

GHOSTTY_HELPER="$PERSONAL_APP/Contents/Resources/bin/ghostty"
install -m 755 "$GHOSTTY_HELPER_SOURCE" "$GHOSTTY_HELPER"
lipo "$GHOSTTY_HELPER" -verify_arch arm64
if strings "$GHOSTTY_HELPER" | grep -Fq "ghostty CLI helper stub"; then
  echo "refusing placeholder Ghostty CLI helper" >&2
  exit 1
fi

xattr -cr "$PERSONAL_APP"
/usr/bin/codesign \
  --force \
  --deep \
  --sign - \
  --timestamp=none \
  --generate-entitlement-der \
  "$PERSONAL_APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$PERSONAL_APP"

ACTUAL_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PERSONAL_APP/Contents/Info.plist")"
ACTUAL_NAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleDisplayName' "$PERSONAL_APP/Contents/Info.plist")"
[[ "$ACTUAL_BUNDLE_ID" == "$BUNDLE_ID" ]]
[[ "$ACTUAL_NAME" == "$APP_NAME" ]]

APP_BINARY="$PERSONAL_APP/Contents/MacOS/cmux"
CLI_BINARY="$PERSONAL_APP/Contents/Resources/bin/cmux"
DIFF_SIDECAR="$PERSONAL_APP/Contents/Resources/bin/cmux-diff-sidecar"
[[ -x "$APP_BINARY" ]]
[[ -x "$CLI_BINARY" ]]
[[ -x "$GHOSTTY_HELPER" ]]
lipo "$APP_BINARY" -verify_arch arm64
lipo "$CLI_BINARY" -verify_arch arm64
lipo "$GHOSTTY_HELPER" -verify_arch arm64
./scripts/verify-diff-sidecar-artifact.sh "$DIFF_SIDECAR" --archs "arm64"
SDK_VERSION="$(otool -l "$APP_BINARY" | awk '/LC_BUILD_VERSION/ { in_version=1; next } in_version && /sdk / { print $2; exit }')"
[[ "$SDK_VERSION" == 26.* ]]
CMUX_CLI_BIN="$CLI_BINARY" python3 tests/test_cli_version_memory_guard.py
./scripts/verify-app-bundle-licenses.sh "$PERSONAL_APP"
CMUX_SMOKE_ALLOW_UNSUPPORTED_GUI=1 CMUX_SMOKE_DEBUG_LOGS=1 \
  ./scripts/smoke-launch-macos-app.sh "$PERSONAL_APP"
CMUX_SMOKE_DIRECT_EXEC=1 CMUX_SMOKE_DEBUG_LOGS=1 \
  ./scripts/smoke-launch-macos-app.sh "$PERSONAL_APP"

ARCHIVE="$DIST_ROOT/$ARCHIVE_NAME"
if [[ -e "$ARCHIVE" ]]; then
  rm -f "$ARCHIVE"
fi
(
  cd "$DIST_ROOT"
  ditto -c -k --sequesterRsrc --keepParent "$APP_NAME.app" "$ARCHIVE_NAME"
)

python3 "$CONTROL_ROOT/personal/build_manifest.py" \
  --config "$CONFIG" \
  --archive "$ARCHIVE" \
  --source-sha "$SOURCE_SHA" \
  --base-tag "$BASE_TAG" \
  --personal-tag "$PERSONAL_TAG" \
  --upstream-main-sha "$UPSTREAM_MAIN_SHA" \
  --output "$DIST_ROOT/$MANIFEST_NAME" \
  --checksum-output "$DIST_ROOT/$ARCHIVE_NAME.sha256"

rm -rf "$PERSONAL_APP"
echo "personal archive: $ARCHIVE"
