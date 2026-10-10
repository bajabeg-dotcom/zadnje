#!/usr/bin/env sh
# Korg Binary Analyzer - command line entry point (Linux / macOS)
#   ./run.sh decode USER01.STY
#   ./run.sh gui
#   ./run.sh selftest
set -e
cd "$(dirname "$0")"
exec python3 -X utf8 -m korg_binary_analyzer "$@"
