#!/usr/bin/env python3
"""Launcher for pa_tool.

The bundled interpreter runs in isolated mode (sys.flags.isolated == 1,
safe_path == True), so neither the current directory nor PYTHONPATH ends up on
sys.path. Running this script puts its own directory first on sys.path, which is
what makes `import pa_tool` work.

Usage:
    python run_pa_tool.py doctor
    python run_pa_tool.py detect <path>
    python run_pa_tool.py validate <path> --model Pa5X
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pa_tool.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
