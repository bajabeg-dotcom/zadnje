#!/usr/bin/env bash
# ============================================================
#  VirtualPa800 NATIVE 0.2 -- glavni pokretac (Linux/macOS)
#  Pokrece PA800 Manager Web v0.2 na http://127.0.0.1:8765
# ============================================================
set -e
cd "$(dirname "$0")/app/pa800-manager-v0.2"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[GRESKA] python3 nije pronadjen. Instaliraj Python 3.10+."
  exit 1
fi

echo "============================================================"
echo " VirtualPa800 NATIVE 0.2 -- PA800 Manager Web v0.2"
echo " Otvori u browseru: http://127.0.0.1:8765"
echo " Za gasenje: CTRL+C"
echo "============================================================"
python3 server.py --host 127.0.0.1 --port 8765
