#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${1:-${SCRIPT_DIR}/.venv/bin/python}"
OUTPUT_DIR="${2:-${SCRIPT_DIR}/dist}"

if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

cd "$SCRIPT_DIR"
"$PYTHON" -m PyInstaller --noconfirm --clean --onedir --console \
    --name NicoLiveViewer \
    --paths src \
    --collect-all streamlink \
    --collect-all streamlink_cli \
    --collect-data certifi \
    --add-data "src/nicoview/assets:nicoview/assets" \
    --distpath "$OUTPUT_DIR" \
    --workpath build \
    --specpath . \
    src/nicoview/__main__.py

PACKAGE="${OUTPUT_DIR}/NicoLiveViewer"
cp README.md USAGE.md THIRD_PARTY.md FEATURE_STATUS.md LICENSE "$PACKAGE/"
"$PACKAGE/NicoLiveViewer" --self-test
echo "Before redistributing, include dependency notices and corresponding sources described in THIRD_PARTY.md."
