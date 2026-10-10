#!/bin/sh
# KORG PA Manager v5 - Unix launcher
cd "$(dirname "$0")" || exit 1
exec python3 korg_pa_manager_v5_gui.py "$@"
