"""``python3 -m nullbar <verb|paths...>`` — the command line.

A module entry point rather than ``-m nullbar.cli``, which warns because
importing the package has already loaded that submodule.
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
