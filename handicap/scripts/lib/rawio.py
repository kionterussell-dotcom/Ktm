"""Raw pulls land in data/raw/YYYY-MM-DD-HHMM-<source>.json and are never edited."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"


def write_raw(source: str, payload: Any) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = RAW_DIR / f"{stamp}-{source}.json"
    n = 2
    while path.exists():                     # never overwrite a raw file
        path = RAW_DIR / f"{stamp}-{source}.{n}.json"
        n += 1
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
