"""pytest configuration (optional: the tests also run with tests/run_tests.py and unittest)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities  # noqa: E402,F401  - starts the QgsApplication
