"""Thin launcher entry point — catches KeyboardInterrupt during slow imports."""

from __future__ import annotations

import sys


def cli() -> int:
    try:
        from investdaytip.main import main

        return main()
    except KeyboardInterrupt:
        print("\nInterrupted. Exiting.", file=sys.stderr)
        return 130
