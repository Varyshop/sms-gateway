#!/usr/bin/env bash
#
# install-release.sh — download the latest release APK from GitHub and install it
# on the connected device via ADB. No build toolchain (Node/Android SDK) needed.
#
# Usage:
#   ./scripts/install-release.sh            install the latest release
#   ./scripts/install-release.sh v1.3.0     install a specific tag
#   ./scripts/install-release.sh --force    re-download even if cached
#
# Downloaded APKs are cached per tag under .release-cache/ and reused on the next
# run instead of being re-downloaded.
#
# Requirements: gh (GitHub CLI, authenticated) and adb in PATH.
#
set -euo pipefail

REPO="Varyshop/sms-gateway-app"
ASSET="app-release.apk"
PACKAGE="com.varyshop.smsgatewayapp"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE_DIR="$SCRIPT_DIR/../.release-cache"

TAG=""
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -*) echo "Unknown option: $arg" >&2; exit 1 ;;
    *) TAG="$arg" ;;
  esac
done

for bin in gh adb; do
  command -v "$bin" >/dev/null || { echo "ERROR: '$bin' not found in PATH." >&2; exit 1; }
done

# Resolve the latest tag when none was given, so we can cache by tag.
if [ -z "$TAG" ]; then
  echo "==> Resolving latest release of $REPO"
  TAG="$(gh release view --repo "$REPO" --json tagName -q .tagName)"
fi
echo "    Release: $TAG"

mkdir -p "$CACHE_DIR"
APK="$CACHE_DIR/$TAG-$ASSET"

if [ -f "$APK" ] && [ "$FORCE" -eq 0 ]; then
  echo "==> Using cached APK: $APK"
else
  echo "==> Downloading $ASSET from $REPO release $TAG"
  gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --output "$APK" --clobber
fi
[ -f "$APK" ] || { echo "ERROR: APK not available at $APK." >&2; exit 1; }

echo "==> Installing on device (adb install -r)"
if ! adb devices | grep -qw "device"; then
  echo "ERROR: No device connected. Connect a phone via USB (with USB debugging) and retry." >&2
  exit 1
fi
adb install -r "$APK"

echo "==> Granting WRITE_SECURE_SETTINGS (for SMS limit feature)"
adb shell pm grant "$PACKAGE" android.permission.WRITE_SECURE_SETTINGS \
  || echo "    WARN: could not grant WRITE_SECURE_SETTINGS (Xiaomi/MIUI may block this). See sms-gateway-app/README.md"

echo "==> Done. Installed $TAG on device."
