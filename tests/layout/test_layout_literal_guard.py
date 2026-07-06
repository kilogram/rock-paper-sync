"""D4 guard: layout literals may exist only under src/rock_paper_sync/layout/.

LAYOUT_VERIFICATION_PLAN.md Phase 4b. Every device-layout magic number has one
home — the layout package, where it carries provenance from the calibration
profile (see test_geometry_profile_consistency). A literal appearing anywhere
else in production code is a regression waiting to drift, so this test bans
them by grep, forever. 57 stays on the list permanently: it is the known-wrong
line-pitch attractor that cost the Phase 4a refit.

Allowlist entries must state why the occurrence is not layout math.
"""

import re
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
SRC = REPO / "src" / "rock_paper_sync"
# tools/rmlib renders; tools/calibration measures. Both must take layout
# numbers from the engine/profile, never carry their own (the renderer's
# private BODY_LINE_HEIGHT=57 survived the Phase 4a refit precisely because
# this guard originally scanned src/ only).
TOOL_DIRS = [REPO / "tools" / "rmlib", REPO / "tools" / "calibration"]

# Layout literals banned outside src/rock_paper_sync/layout/.
# Word-boundary-ish regexes: not preceded/followed by digits or a dot, so
# e.g. 1757 or 45.559 do not match.
BANNED = [
    "57.0",  # pre-refit line pitch — the known-wrong attractor; banned forever
    "45.55",  # measured body line pitch (lives in DeviceGeometry.line_height)
    "82.0",  # pre-refit negative_y_offset (57 + 25)
    "70.55",  # negative_y_offset (45.55 + 25) — must come from geometry
    "11.45",  # refit delta; appearing in code means someone is compensating
    "758.0",  # layout_text_width
    "758",  # layout_text_width (int form)
    "750.0",  # text_width
    "234.0",  # text_pos_y
    "-375.0",  # text_pos_x
    "44.4",  # device highlight box height
]

# (relative path, literal) pairs that are provably not layout math.
ALLOWLIST: set[tuple[str, str]] = set()


def _matches(text: str, literal: str) -> list[int]:
    pattern = re.compile(rf"(?<![\d.\w]){re.escape(literal)}(?![\d.\w])")
    return [text[: m.start()].count("\n") + 1 for m in pattern.finditer(text)]


def _scan(root: Path, label: str) -> list[str]:
    violations = []
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(root).as_posix()
        if label == "src/rock_paper_sync" and rel.startswith("layout/"):
            continue
        text = py.read_text()
        for literal in BANNED:
            for line_no in _matches(text, literal):
                if (f"{label}/{rel}", literal) in ALLOWLIST or (rel, literal) in ALLOWLIST:
                    continue
                violations.append(f"{label}/{rel}:{line_no}: {literal}")
    return violations


def test_layout_literals_only_in_layout_package():
    violations = _scan(SRC, "src/rock_paper_sync")
    for tool_dir in TOOL_DIRS:
        if tool_dir.exists():
            violations += _scan(tool_dir, f"tools/{tool_dir.name}")
    assert not violations, (
        "Layout literals outside src/rock_paper_sync/layout/ — import from "
        "DeviceGeometry instead (or add a justified ALLOWLIST entry):\n" + "\n".join(violations)
    )
