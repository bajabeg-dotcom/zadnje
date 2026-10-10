#!/usr/bin/env python3
"""Run the pa_tool test suite with the stdlib runner.

The bundled interpreter is isolated, so this script puts the project root on
sys.path before discovering tests.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(
        os.path.join(HERE, "tests"), pattern="test_*.py", top_level_dir=HERE
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
