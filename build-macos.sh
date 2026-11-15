#!/usr/bin/env bash
# Build a macOS Universal 2 (arm64 + x86_64) .app bundle in one package.
# Prerequisite: Use the python.org macOS Universal2 Python installer so the
# Python runtime and binary wheels include both arm64 and x86_64 slices.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${1:-${SCRIPT_DIR}/.venv/bin/python}"
OUTPUT_DIR="${2:-${SCRIPT_DIR}/dist}"
TARGET_ARCH="${TARGET_ARCH:-universal2}"

if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

cd "$SCRIPT_DIR"

# Check whether the Python binary itself is Universal 2; fall back cleanly if single-arch.
if [ "$TARGET_ARCH" = "universal2" ] && command -v lipo >/dev/null 2>&1; then
    ARCHS="$(lipo -archs "$PYTHON" 2>/dev/null || true)"
    if [[ "$ARCHS" != *"x86_64"* || "$ARCHS" != *"arm64"* ]]; then
        echo "Note: $PYTHON only contains [$ARCHS] (not universal2)."
        echo "To build a single Universal 2 .app for both Intel and Apple Silicon,"
        echo "use the official python.org macOS universal2 installer. Falling back to native arch."
        TARGET_ARCH="$(uname -m)"
    fi
fi

# Generate macOS .icns from studio.png if sips & iconutil are available
ICON_ARGS=()
if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1 && [ -f "src/nicoview/assets/studio.png" ]; then
    ICONSET_DIR="${SCRIPT_DIR}/build/studio.iconset"
    ICNS_PATH="${SCRIPT_DIR}/build/studio.icns"
    rm -rf "$ICONSET_DIR" "$ICNS_PATH"
    mkdir -p "$ICONSET_DIR"
    for size in 16 32 128 256 512; do
        sips -z $size $size "src/nicoview/assets/studio.png" --out "${ICONSET_DIR}/icon_${size}x${size}.png" >/dev/null
        sips -z $((size*2)) $((size*2)) "src/nicoview/assets/studio.png" --out "${ICONSET_DIR}/icon_${size}x${size}@2x.png" >/dev/null
    done
    if iconutil -c icns "$ICONSET_DIR" -o "$ICNS_PATH"; then
        ICON_ARGS=(--icon "$ICNS_PATH")
    fi
fi

"$PYTHON" -m PyInstaller --noconfirm --clean --onedir --windowed \
    --name NicoLiveViewer \
    --target-arch "$TARGET_ARCH" \
    --osx-bundle-identifier "jp.nicoliveviewer.studio" \
    "${ICON_ARGS[@]}" \
    --paths src \
    --collect-all streamlink \
    --collect-all streamlink_cli \
    --collect-data certifi \
    --add-data "src/nicoview/assets:nicoview/assets" \
    --distpath "$OUTPUT_DIR" \
    --workpath build \
    --specpath . \
    src/nicoview/__main__.py

APP_BUNDLE="${OUTPUT_DIR}/NicoLiveViewer.app"
PACKAGE="${OUTPUT_DIR}/NicoLiveViewer"
cp README.md USAGE.md THIRD_PARTY.md FEATURE_STATUS.md LICENSE "$PACKAGE/"
if [ -d "$APP_BUNDLE/Contents/Resources" ]; then
    cp README.md USAGE.md THIRD_PARTY.md FEATURE_STATUS.md LICENSE "$APP_BUNDLE/Contents/Resources/"
fi

"$APP_BUNDLE/Contents/MacOS/NicoLiveViewer" --self-test
echo "Built macOS app bundle ($TARGET_ARCH) at: $APP_BUNDLE"
echo "Before redistributing, include dependency notices and corresponding sources described in THIRD_PARTY.md."
