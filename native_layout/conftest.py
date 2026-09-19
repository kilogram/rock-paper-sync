"""Pytest conftest for native_layout tests.

Ensures native_layout and the repo-root packages (rock_paper_sync, tests)
are importable when running ``uv run pytest native_layout/``.
"""

import sys
from pathlib import Path

# Repo root is one level up from native_layout/
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
