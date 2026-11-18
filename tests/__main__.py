"""
Run the whole test suite: python -m tests [-v] [-k PATTERN]

The process ends with os._exit once the results are printed, as the app does: left to the
interpreter's exit, PySide destroys the widgets the tests made in no particular order, which can
crash after every test passed (SIGILL on macOS, heap corruption on Windows) and turn a passing
run into a failing exit code.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

program = unittest.main(
    module=None,
    argv=["python -m tests", "discover", "-s", os.path.join(ROOT, "tests"), "-t", ROOT, *sys.argv[1:]],
    exit=False,
)
sys.stdout.flush()
sys.stderr.flush()
os._exit(0 if program.result.wasSuccessful() else 1)
