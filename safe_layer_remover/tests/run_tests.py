"""Run the test suite without pytest, using QGIS' bundled Python:

    python-qgis.bat safe_layer_remover/tests/run_tests.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import utilities  # noqa: E402,F401  - starts the QgsApplication


def main() -> int:
    suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    code = main()
    utilities.QGISAPP.exitQgis()
    os._exit(code)
