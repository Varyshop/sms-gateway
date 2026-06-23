#!/usr/bin/env bash
#
# setup.sh — initialize, build and install the SMS Gateway Android app on a connected device.
#
# Runs the full local flow in one go:
#   0. init the sms-gateway-app submodule (git submodule update --init --recursive)
#   1. install JS dependencies (npm)
#   2. prebuild the native Android project
#   3. build the release APK (gradle)
#   4. install the APK on the connected device via ADB
#   5. grant WRITE_SECURE_SETTINGS (for the SMS limit feature)
#
# Run from the sms-gateway module root (extra/sms):
#   ./scripts/setup.sh              full flow
#   ./scripts/setup.sh --clean      prebuild with --clean (after app.json / native changes)
#   ./scripts/setup.sh --no-install build only, skip ADB install
#
set -euo pipefail

# Module root = parent of this script's directory (extra/sms)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$ROOT/sms-gateway-app"

CLEAN=""
DO_INSTALL=1
PACKAGE="com.varyshop.smsgatewayapp"
APK_PATH="android/app/build/outputs/apk/release/app-release.apk"

for arg in "$@"; do
  case "$arg" in
    --clean)      CLEAN="--clean" ;;
    --no-install) DO_INSTALL=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

# Expo SDK 54 requires Node 18+. The system node may be too old (the gradle
# autolinking step also calls `node`, so it must be on PATH too). Bootstrap nvm,
# install the required Node and activate it for the whole script — including the
# gradle process, which inherits this PATH.
NODE_VERSION="20"
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"

if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  echo "==> nvm not found, installing nvm"
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
fi

# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"

if ! nvm use "$NODE_VERSION" >/dev/null 2>&1; then
  echo "==> Installing Node $NODE_VERSION via nvm"
  nvm install "$NODE_VERSION"
  nvm use "$NODE_VERSION" >/dev/null
fi
echo "    Using Node $(node --version) at $(command -v node)"

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "ERROR: Node 18+ required, got $(node --version)." >&2
  exit 1
fi

# Locate the Android SDK so gradle can find it. Honor an existing env var,
# otherwise probe the usual install locations.
if [ -z "${ANDROID_HOME:-}" ] && [ -z "${ANDROID_SDK_ROOT:-}" ]; then
  for d in "$HOME/Android/Sdk" "$HOME/Library/Android/sdk" "/opt/android-sdk" "/usr/lib/android-sdk"; do
    if [ -d "$d/platforms" ]; then
      export ANDROID_HOME="$d"
      break
    fi
  done
fi
ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ -z "$ANDROID_HOME" ] || [ ! -d "$ANDROID_HOME/platforms" ]; then
  echo "ERROR: Android SDK not found. Install it (Android Studio) and set ANDROID_HOME," >&2
  echo "       or create android/local.properties with sdk.dir=/path/to/Android/Sdk" >&2
  exit 1
fi
export ANDROID_HOME
export ANDROID_SDK_ROOT="$ANDROID_HOME"
echo "    Android SDK: $ANDROID_HOME"

echo "==> 0/5 Initializing sms-gateway-app submodule"
( cd "$ROOT" && git submodule update --init --recursive )

cd "$APP_DIR"

echo "==> 1/5 Installing dependencies (npm install)"
npm install

echo "==> 2/5 Prebuild native Android project (expo prebuild ${CLEAN})"
npm run prebuild -- ${CLEAN}

# Pin the SDK location for gradle (after prebuild, which may regenerate android/).
echo "sdk.dir=$ANDROID_HOME" > android/local.properties

echo "==> 3/5 Building release APK (gradle assembleRelease)"
# Stop any gradle daemon that may have started with an old-Node PATH, so the
# autolinking `node` call in settings.gradle uses the nvm Node activated above.
( cd android && ./gradlew --stop >/dev/null 2>&1 || true; ./gradlew assembleRelease )

if [ ! -f "$APK_PATH" ]; then
  echo "ERROR: APK not found at $APP_DIR/$APK_PATH" >&2
  exit 1
fi
cp "$APK_PATH" ./app-release.apk
echo "    APK ready: $APP_DIR/app-release.apk"

if [ "$DO_INSTALL" -eq 0 ]; then
  echo "==> Skipping install (--no-install). Done."
  exit 0
fi

echo "==> 4/5 Installing APK on device (adb install -r)"
if ! adb devices | grep -qw "device"; then
  echo "ERROR: No device connected. Connect a phone via USB (with USB debugging) and retry." >&2
  exit 1
fi
adb install -r ./app-release.apk

echo "==> 5/5 Granting WRITE_SECURE_SETTINGS (for SMS limit feature)"
adb shell pm grant "$PACKAGE" android.permission.WRITE_SECURE_SETTINGS \
  || echo "    WARN: could not grant WRITE_SECURE_SETTINGS (Xiaomi/MIUI may block this). See sms-gateway-app/README.md"

echo "==> Done. App installed on device."
