#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
mkdir -p logs
python3 -c "import tkinter, mido, korg_pa_manager"
python3 -m korg_pa_manager --self-test > logs/last-self-test.json
exec python3 run_korg_pa_manager.py "$@"
