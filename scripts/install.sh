#!/usr/bin/env bash
set -euo pipefail

# Install native-multimodal Antigravity plugin for macOS / Linux.
# Source repo: https://github.com/creep-crow/antigravity-native-multimodal

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="${AGY_PLUGIN_INSTALL_DIR:-${HOME}/.gemini/config/plugins/native-multimodal}"

echo "Installing native-multimodal plugin..."
echo "  Source: ${REPO_ROOT}"
echo "  Target: ${TARGET_DIR}"

mkdir -p "${TARGET_DIR}"

for f in plugin.json hooks.json README.md README.zh-CN.md LICENSE CHANGELOG.md; do
  if [ -f "${REPO_ROOT}/${f}" ]; then
    cp -f "${REPO_ROOT}/${f}" "${TARGET_DIR}/${f}"
  fi
done

for d in assets hooks rules skills; do
  if [ -d "${REPO_ROOT}/${d}" ]; then
    rm -rf "${TARGET_DIR}/${d}"
    cp -R "${REPO_ROOT}/${d}" "${TARGET_DIR}/${d}"
  fi
done

find "${TARGET_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} +

echo ""
echo "Environment checks:"
if command -v python3 >/dev/null 2>&1; then
  echo "  [OK] python3: $(command -v python3) ($(python3 --version 2>&1))"
elif command -v python >/dev/null 2>&1; then
  echo "  [OK] python: $(command -v python) ($(python --version 2>&1))"
else
  echo "  [WARN] python3/python not found on PATH. Install Python 3.10+ for runtime hooks and media helper."
fi

if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  echo "  [OK] ffmpeg:  $(command -v ffmpeg)"
  echo "  [OK] ffprobe: $(command -v ffprobe)"
else
  echo "  [WARN] ffmpeg/ffprobe not found on PATH. Auto-bridging exotic containers and slicing require FFmpeg."
fi

echo ""
echo "Installation complete! Enable or verify in Antigravity Customizations UI (native-multimodal)."
