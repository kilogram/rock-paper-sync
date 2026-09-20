"""I7 guard: screen-pixel literals must only appear in device_profile.py.

Greps src/ (excluding device_profile.py and the layout/ package which
moves in Phase 3a) for the known device-pixel constants.  Fails on a hit.
"""

import subprocess
from pathlib import Path

import pytest

# Known screen-pixel and canvas-pixel literals that must be confined to
# device_profile.py.
# Paper Pro Move screen: 954x1696.
# PDF canvas (measured from SceneInfo.paper_size): 1404x1872.
_PIXEL_LITERALS = ["954", "1696", "1404", "1872"]

# Directories excluded from the scan (they're allowed to know pixel values)
_EXCLUDED_DIRS = {"layout", "fonts", "annotations"}

# Files excluded from the scan
_EXCLUDED_FILES = {"device_profile.py", "coordinates.py"}


def _src_root() -> Path:
    return Path(__file__).parent.parent / "src" / "rock_paper_sync"


def _should_scan(path: Path) -> bool:
    """Return True if this file should be scanned for pixel literals."""
    if path.name in _EXCLUDED_FILES:
        return False
    # Exclude subdirectories like layout/, fonts/
    parts = path.relative_to(_src_root()).parts
    if any(part in _EXCLUDED_DIRS for part in parts):
        return False
    return path.suffix == ".py"


@pytest.mark.parametrize("literal", _PIXEL_LITERALS)
def test_pixel_literal_not_in_source(literal: str) -> None:
    """No source file outside device_profile.py contains a pixel literal."""
    src_root = _src_root()
    violations: list[str] = []

    for py_file in src_root.rglob("*.py"):
        if not _should_scan(py_file):
            continue
        # Use grep for speed
        result = subprocess.run(
            ["grep", "-n", f"\\b{literal}\\b", str(py_file)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # Filter out comments and string-only hits
            for line in result.stdout.strip().splitlines():
                # Skip lines that are just comments
                stripped = line.split(":", 2)[-1].strip()
                if stripped.startswith("#"):
                    continue
                violations.append(f"{py_file.relative_to(src_root)}:{line}")

    assert not violations, (
        f"Pixel literal {literal} found outside device_profile.py (I7 violation):\n"
        + "\n".join(violations)
    )
