#!/usr/bin/env python3
"""Launch the active 84-unit N5 local-player v3 catalog."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "N5_MULTI_CONTRACT_CATALOG_PATH",
    str(ROOT / "product/n5/course/local-player-v3/active-catalog.json"),
)

from run_n5_multi_contract_learning_app import main  # noqa: E402


if __name__ == "__main__":
    main()
