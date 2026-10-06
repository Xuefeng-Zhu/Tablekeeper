#!/usr/bin/env python3
"""Probe a pinned local OpenCode server without inference or BAND traffic."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from factorykit.opencode_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
