"""``python3 -m nullbar <paths...>`` — the lookahead lint.

A module entry point rather than ``-m nullbar.leaklint``, which warns
because importing the package has already loaded that submodule.
"""
import sys

from .leaklint import main

if __name__ == "__main__":
    sys.exit(main())
