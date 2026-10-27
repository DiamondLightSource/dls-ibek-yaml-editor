#!/usr/bin/env bash
# Run from the outer container, not from an agent's sandbox.
# Installs the native libraries needed by Qt's offscreen test platform.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    apt-get update
    apt-get install -y libgl1 libegl1 libopengl0 libxkbcommon0 libfontconfig1 libfreetype6 libdbus-1-3
else
    sudo apt-get update
    sudo apt-get install -y libgl1 libegl1 libopengl0 libxkbcommon0 libfontconfig1 libfreetype6 libdbus-1-3
fi
