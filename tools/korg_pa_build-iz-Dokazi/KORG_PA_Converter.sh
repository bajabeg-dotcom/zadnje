#!/bin/sh
# KORG PA Konverter - Unix launcher
cd "$(dirname "$0")" || exit 1
exec python3 korg_pa_converter_gui.py "$@"
