#!/usr/bin/env python3
"""Extract device calibration profile from golden .rm files.

This tool parses golden .rm files recorded from a device and extracts
calibration values for layout engine parameters.

Usage:
    uv run python tools/calibration/extract_profile.py \\
        --device paper_pro_move \\
        --input tests/record_replay/testdata/calibration/paper_pro_move/ \\
        --output tests/record_replay/testdata/calibration/paper_pro_move/profile.json

The tool analyzes highlight positions from the calibration documents to measure:
- Paragraph spacing (Y delta between paragraphs)
- Bullet/list item spacing (Y delta between items)
- List indentation (X offset for lists)
- Heading margins (Y space after headings)
- Word-wrap boundary (maximum line width before wrap)
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Try to import rmscene for parsing .rm files
try:
    import rmscene
    from rmscene import read_blocks
    from rmscene.scene_items import SceneLineItemBlock

    RMSCENE_AVAILABLE = True
except ImportError:
    RMSCENE_AVAILABLE = False


@dataclass
class HighlightRect:
    """A highlight rectangle extracted from an .rm file."""

    x: float
    y: float
    width: float
    height: float
    page: int = 0
    # Text anchor info if available
    anchor_offset: int | None = None
    anchor_text: str | None = None


@dataclass
class StructuralProfile:
    """Structural layout parameters extracted from calibration."""

    paragraph_spacing_px: float = 57.0
    bullet_item_spacing_px: float = 57.0
    list_item_spacing_px: float = 57.0
    checkmark_spacing_px: float = 57.0
    bullet_indent_px: float = 30.0
    list_indent_px: float = 30.0
    checkmark_indent_px: float = 30.0
    heading_margin_after_px: float = 20.0
    subheading_margin_after_px: float = 15.0
    heading_font_scale: float = 1.5
    subheading_font_scale: float = 1.2


@dataclass
class DeviceProfile:
    """Complete device calibration profile."""

    device_name: str
    page_width: int = 1404
    page_height: int = 1872
    text_width: float = 750.0
    layout_text_width: float = 758.0
    font_point_size: float = 29.5
    line_height: float = 57.0
    text_pos_x: float = -375.0
    text_pos_y: float = 94.0
    baseline_offset: float = 25.0
    bottom_margin: float = 100.0
    structural: StructuralProfile = field(default_factory=StructuralProfile)
    calibration_date: str = field(default_factory=lambda: datetime.now().isoformat()[:10])
    golden_files: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Convert to JSON string."""
        data = asdict(self)
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> DeviceProfile:
        """Create from JSON string."""
        data = json.loads(json_str)
        structural_data = data.pop("structural", {})
        data["structural"] = StructuralProfile(**structural_data)
        return cls(**data)


def extract_highlights_from_rm(rm_path: Path) -> list[HighlightRect]:
    """Extract highlight rectangles from an .rm file.

    Args:
        rm_path: Path to the .rm file

    Returns:
        List of HighlightRect objects
    """
    if not RMSCENE_AVAILABLE:
        raise ImportError("rmscene is required. Install with: uv pip install rmscene")

    highlights = []

    with open(rm_path, "rb") as f:
        result = read_blocks(f)

    for block in result.blocks:
        if isinstance(block, SceneLineItemBlock):
            item = block.value
            # Check if this is a highlight (has rectangles)
            if hasattr(item, "rectangles") and item.rectangles:
                for rect in item.rectangles:
                    highlights.append(
                        HighlightRect(
                            x=rect.x,
                            y=rect.y,
                            width=rect.w,
                            height=rect.h,
                        )
                    )

    return highlights


def calculate_spacing_from_highlights(
    highlights: list[HighlightRect],
) -> dict[str, float]:
    """Calculate spacing values from a list of highlights.

    Assumes highlights are sorted by Y position within each category.

    Args:
        highlights: List of highlights to analyze

    Returns:
        Dictionary of calculated spacing values
    """
    if len(highlights) < 2:
        return {}

    # Sort by Y position
    sorted_highlights = sorted(highlights, key=lambda h: h.y)

    # Calculate Y deltas between consecutive highlights
    y_deltas = []
    for i in range(1, len(sorted_highlights)):
        delta = sorted_highlights[i].y - sorted_highlights[i - 1].y
        if delta > 0:  # Ignore negative/zero deltas
            y_deltas.append(delta)

    if not y_deltas:
        return {}

    # Calculate statistics
    avg_delta = sum(y_deltas) / len(y_deltas)
    min_delta = min(y_deltas)
    max_delta = max(y_deltas)

    # Calculate X offset (indentation)
    x_positions = [h.x for h in sorted_highlights]
    min_x = min(x_positions)
    max_x = max(x_positions)

    return {
        "avg_y_delta": avg_delta,
        "min_y_delta": min_delta,
        "max_y_delta": max_delta,
        "min_x": min_x,
        "max_x": max_x,
        "x_range": max_x - min_x,
    }


def analyze_structure_file(rm_path: Path) -> StructuralProfile:
    """Analyze calibration_structure.rm to extract structural parameters.

    Args:
        rm_path: Path to calibration_structure.rm

    Returns:
        StructuralProfile with measured values
    """
    highlights = extract_highlights_from_rm(rm_path)

    if not highlights:
        print(f"Warning: No highlights found in {rm_path}")
        return StructuralProfile()

    # Group highlights by approximate Y position (within line_height tolerance)
    line_height = 57.0  # Default, will be refined

    # Sort by Y
    sorted_highlights = sorted(highlights, key=lambda h: h.y)

    # Calculate line height from consecutive highlights
    if len(sorted_highlights) >= 2:
        y_deltas = []
        for i in range(1, len(sorted_highlights)):
            delta = sorted_highlights[i].y - sorted_highlights[i - 1].y
            if 40 < delta < 200:  # Reasonable range for line/element spacing
                y_deltas.append(delta)

        if y_deltas:
            # The most common delta is likely the line height
            line_height = min(y_deltas, key=lambda d: abs(d - 57))

    # Analyze X positions for indentation
    x_positions = [h.x for h in sorted_highlights]
    if x_positions:
        min_x = min(x_positions)
        # Count X positions to find indentation levels
        x_groups = {}
        for x in x_positions:
            # Round to nearest 10px for grouping
            key = round(x / 10) * 10
            x_groups[key] = x_groups.get(key, 0) + 1

        # Find the base X (most common) and indented X
        sorted_x_groups = sorted(x_groups.items(), key=lambda kv: -kv[1])
        base_x = sorted_x_groups[0][0] if sorted_x_groups else min_x

        # Calculate indent as difference from base
        indent_values = [x for x in x_groups.keys() if x > base_x]
        if indent_values:
            indent = min(indent_values) - base_x
        else:
            indent = 30.0  # Default

    else:
        indent = 30.0

    return StructuralProfile(
        paragraph_spacing_px=line_height,
        bullet_item_spacing_px=line_height,
        list_item_spacing_px=line_height,
        checkmark_spacing_px=line_height,
        bullet_indent_px=indent,
        list_indent_px=indent,
        checkmark_indent_px=indent,
    )


def analyze_wrap_file(rm_path: Path) -> dict[str, float]:
    """Analyze calibration_wrap.rm to find word-wrap boundary.

    Args:
        rm_path: Path to calibration_wrap.rm

    Returns:
        Dictionary with wrap-related measurements
    """
    highlights = extract_highlights_from_rm(rm_path)

    if not highlights:
        return {}

    # Find maximum X position (indicates line width before wrap)
    max_x = max(h.x + h.width for h in highlights)
    min_x = min(h.x for h in highlights)

    # The line width is approximately max_x - min_x
    return {
        "max_line_end_x": max_x,
        "line_start_x": min_x,
        "effective_line_width": max_x - min_x,
    }


def create_profile(
    device_name: str, calibration_dir: Path, output_path: Path | None = None
) -> DeviceProfile:
    """Create a device profile from calibration golden files.

    Args:
        device_name: Name of the device (e.g., "paper_pro_move")
        calibration_dir: Directory containing golden .rm files
        output_path: Optional path to write profile.json

    Returns:
        DeviceProfile with measured values
    """
    profile = DeviceProfile(device_name=device_name)
    golden_files = []

    # Look for calibration files
    structure_path = calibration_dir / "calibration_structure.rm"
    wrap_path = calibration_dir / "calibration_wrap.rm"
    chars_path = calibration_dir / "calibration_chars.rm"

    if structure_path.exists():
        golden_files.append(structure_path.name)
        try:
            profile.structural = analyze_structure_file(structure_path)
            print(f"Analyzed structure file: {structure_path}")
        except Exception as e:
            print(f"Warning: Failed to analyze {structure_path}: {e}")

    if wrap_path.exists():
        golden_files.append(wrap_path.name)
        try:
            wrap_info = analyze_wrap_file(wrap_path)
            if "effective_line_width" in wrap_info:
                profile.layout_text_width = wrap_info["effective_line_width"]
            print(f"Analyzed wrap file: {wrap_path}")
        except Exception as e:
            print(f"Warning: Failed to analyze {wrap_path}: {e}")

    if chars_path.exists():
        golden_files.append(chars_path.name)
        print(f"Found chars file: {chars_path} (no specific analysis needed)")

    profile.golden_files = golden_files

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(profile.to_json())
        print(f"Wrote profile to: {output_path}")

    return profile


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Extract device calibration profile")
    parser.add_argument("--device", required=True, help="Device name (e.g., paper_pro_move)")
    parser.add_argument("--input", required=True, help="Directory with golden .rm files")
    parser.add_argument("--output", help="Output path for profile.json")
    parser.add_argument("--print", action="store_true", help="Print profile to stdout")

    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.exists():
        print(f"Error: Input directory does not exist: {input_dir}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else None

    profile = create_profile(args.device, input_dir, output_path)

    if args.print or not output_path:
        print("\n=== Device Profile ===")
        print(profile.to_json())


if __name__ == "__main__":
    main()
