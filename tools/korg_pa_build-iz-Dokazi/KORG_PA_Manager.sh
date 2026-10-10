#!/bin/sh
# KORG PA Manager - Unix launcher
cd "$(dirname "$0")" || exit 1
exec python3 korg_pa_manager_gui.py "$@"
