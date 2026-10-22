#!/usr/bin/env python
"""Pantau Crawler Interactive Launcher."""
import sys
from pathlib import Path

# Tambahkan src ke sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from crawler.interactive import run_interactive

if __name__ == "__main__":
    run_interactive()
