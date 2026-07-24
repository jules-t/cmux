#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 8 ]]; then
  echo "usage: $0 <source-root> <control-root> <dist-root> <derived-data> <source-sha> <base-tag> <personal-tag> <ghostty-helper>" >&2
  exit 2
fi

SOURCE_ROOT="$(cd "$1" && pwd)"
CONTROL_ROOT="$(cd "$2" && pwd)"
DIST_ROOT="$3"
DERIVED_DATA="$4"
SOURCE_SHA="$5"
BASE_TAG="$6"
PERSONAL_TAG="$7"
GHOSTTY_HELPER_SOURCE="$(cd "$(dirname "$8")" && pwd)/$(basename "$8")"
CONFIG="$CONTROL_ROOT/personal/config.json"

APP_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["app_name"])' "$CONFIG")"
BUNDLE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle_identifier"])' "$CONFIG")"
ARCHIVE_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifact_name"])' "$CONFIG")"
MANIFEST_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["manifest_name"])' "$CONFIG")"

mkdir -p "$DIST_ROOT" "$DERIVED_DATA"
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
CMUX_SKIP_ZIG_BUILD=1 xcodebuild \
  -project cmux.xcodeproj \
  -scheme cmux \
  -configuration Release \
  -destination 'generic/platform=macOS' \
  -derivedDataPath "$DERIVED_DATA" \
  -clonedSourcePackagesDirPath "$SOURCE_PACKAGES_DIR" \
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

scripts/ci/run-in-console-session.sh \
  scripts/ci/run-app-host-xcodebuild.sh \
  -project cmux.xcodeproj \
  -scheme cmux-unit \
  -configuration Release \
  -derivedDataPath "$DERIVED_DATA" \
  -clonedSourcePackagesDirPath "$SOURCE_PACKAGES_DIR" \
  -destination "platform=macOS" \
  CMUX_SKIP_ZIG_BUILD=1 \
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
  --personal-tag "$PERSONAL_TAG"

if [[ ! -f "$GHOSTTY_HELPER_SOURCE" || ! -x "$GHOSTTY_HELPER_SOURCE" ]]; then
  echo "real Ghostty CLI helper is missing or non-executable: $GHOSTTY_HELPER_SOURCE" >&2
  exit 1
fi
GHOSTTY_HELPER="$PERSONAL_APP/Contents/Resources/bin/ghostty"
install -m 755 "$GHOSTTY_HELPER_SOURCE" "$GHOSTTY_HELPER"
lipo "$GHOSTTY_HELPER" -verify_arch arm64

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
[[ -x "$APP_BINARY" ]]
[[ -x "$CLI_BINARY" ]]
[[ -x "$GHOSTTY_HELPER" ]]
lipo "$APP_BINARY" -verify_arch arm64
lipo "$CLI_BINARY" -verify_arch arm64
lipo "$GHOSTTY_HELPER" -verify_arch arm64

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
  --output "$DIST_ROOT/$MANIFEST_NAME" \
  --checksum-output "$DIST_ROOT/$ARCHIVE_NAME.sha256"

rm -rf "$PERSONAL_APP"
echo "personal archive: $ARCHIVE"
