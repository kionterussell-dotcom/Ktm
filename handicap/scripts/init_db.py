#!/usr/bin/env python
"""Create or migrate data/desk.db. Safe to re-run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db

if __name__ == "__main__":
    print(f"desk.db ready at {db.init()}")
