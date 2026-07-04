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
    import rmscene  # noqa: F401  (availability probe)
    from rmscene import read_blocks
    from rmscene.scene_stream import SceneLineItemBlock

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


# ===========================================================================
# Corpus mode (Phase 2): sentinel-based extraction
#
# The corpus layout (see docs/LAYOUT_TESTBENCH_PLAN.md Phase 2) is:
#   <input>/src/*.md                 source markdown with ZEBRAnn sentinels
#   <input>/rm_files/<doc>/*.rm      device .rm files pulled after highlighting
#   <input>/profile.json             recording metadata + derived measurements
#
# On-device highlights are stored as SceneGlyphItemBlock -> GlyphRange, whose
# `.text` is the highlighted string itself. That lets us map a highlight back
# to its sentinel by exact text match — no positional guessing (principle P2).
# ===========================================================================


@dataclass
class GlyphHighlight:
    """A device-recorded highlight: the highlighted text and its rectangles."""

    text: str
    rects: list[HighlightRect]
    doc: str

    @property
    def top(self) -> float:
        return min(r.y for r in self.rects)

    @property
    def height(self) -> float:
        return max(r.height for r in self.rects)

    @property
    def left(self) -> float:
        return min(r.x for r in self.rects)

    @property
    def right(self) -> float:
        return max(r.x + r.width for r in self.rects)


@dataclass
class SentinelSource:
    """Where a sentinel lives in the source markdown."""

    sentinel: str
    doc: str
    block_type: str  # body | heading | list | code
    heading_level: int | None = None
    list_level: int | None = None
    char_offset: int | None = None  # offset within the plain-text line
    plain_line: str = ""


def read_glyph_highlights(rm_path: Path, doc: str) -> list[GlyphHighlight]:
    """Read GlyphRange highlights from an .rm file (SceneGlyphItemBlock)."""
    if not RMSCENE_AVAILABLE:
        raise ImportError("rmscene is required. Install with: uv pip install rmscene")

    from rmscene.scene_stream import SceneGlyphItemBlock

    out: list[GlyphHighlight] = []
    with open(rm_path, "rb") as f:
        for block in read_blocks(f):
            if isinstance(block, SceneGlyphItemBlock) and block.item.value is not None:
                gr = block.item.value
                rects = [
                    HighlightRect(x=r.x, y=r.y, width=r.w, height=r.h)
                    for r in gr.rectangles
                ]
                if rects:
                    out.append(GlyphHighlight(text=gr.text, rects=rects, doc=doc))
    return out


def read_stroke_bounds(rm_path: Path) -> dict[str, float] | None:
    """Read the Y-bounds of handwritten strokes in an .rm file (for T5 probe).

    Returns min/max Y of all stroke points in the file's native coordinate
    space, or None if there are no strokes. Note (C4): stroke points are
    anchor-relative; the T5 analysis compares these against the highlight
    rectangle to estimate the baseline offset and flags the result for review.
    """
    if not RMSCENE_AVAILABLE:
        raise ImportError("rmscene is required.")

    from rmscene.scene_stream import SceneLineItemBlock

    ys: list[float] = []
    with open(rm_path, "rb") as f:
        for block in read_blocks(f):
            if isinstance(block, SceneLineItemBlock) and block.item.value is not None:
                item = block.item.value
                for p in getattr(item, "points", []):
                    ys.append(p.y)
    if not ys:
        return None
    return {"stroke_min_y": min(ys), "stroke_max_y": max(ys), "n_points": len(ys)}


def parse_sentinel_sources(src_dir: Path) -> dict[str, SentinelSource]:
    """Scan source markdown for ZEBRAnn / T5BASE sentinels and classify them."""
    import re

    token_re = re.compile(r"(ZEBRA\d{2}|T5BASE)")
    sources: dict[str, SentinelSource] = {}

    for md in sorted(src_dir.glob("*.md")):
        if md.name == "README.md":
            continue
        doc = md.stem
        in_code = False
        for raw in md.read_text().splitlines():
            stripped = raw.strip()
            if stripped.startswith("```"):
                in_code = not in_code
                continue

            if in_code:
                block_type, heading_level, list_level = "code", None, None
                plain = raw
            elif stripped.startswith("#"):
                block_type = "heading"
                heading_level = len(raw) - len(raw.lstrip("#"))
                list_level = None
                plain = raw.lstrip("#").strip()
            elif re.match(r"^\s*([-*]|\d+\.)\s", raw):
                block_type = "list"
                heading_level = None
                indent = len(raw) - len(raw.lstrip(" "))
                list_level = indent // 4
                plain = re.sub(r"^\s*([-*]|\d+\.)\s+", "", raw)
            else:
                block_type, heading_level, list_level = "body", None, None
                plain = raw

            plain_stored = plain.strip()
            for m in token_re.finditer(raw):
                token = m.group(1)
                offset = plain_stored.find(token)
                sources[token] = SentinelSource(
                    sentinel=token,
                    doc=doc,
                    block_type=block_type,
                    heading_level=heading_level,
                    list_level=list_level,
                    char_offset=offset if offset >= 0 else None,
                    plain_line=plain_stored,
                )
    return sources


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def build_sentinel_records(
    sources: dict[str, SentinelSource],
    highlights: list[GlyphHighlight],
) -> tuple[list[dict], list[str]]:
    """Correlate device highlights to source sentinels by exact text match.

    Returns (records, warnings). Each record joins the source location, char
    range, and the device rectangle(s) for one sentinel.
    """
    by_text: dict[str, list[GlyphHighlight]] = {}
    for h in highlights:
        by_text.setdefault(h.text.strip(), []).append(h)

    records: list[dict] = []
    warnings: list[str] = []

    for sentinel, src in sorted(sources.items()):
        matches = by_text.get(sentinel, [])
        if not matches:
            warnings.append(f"MISSING highlight for sentinel {sentinel} "
                            f"(source doc {src.doc}) — was it highlighted on device?")
            continue
        if len(matches) > 1:
            warnings.append(f"AMBIGUOUS: {len(matches)} highlights match {sentinel}; "
                            "using the first in reading order.")
            matches.sort(key=lambda h: (h.top, h.left))
        h = matches[0]
        records.append({
            "sentinel": sentinel,
            "doc": src.doc,
            "block_type": src.block_type,
            "heading_level": src.heading_level,
            "list_level": src.list_level,
            "char_offset": src.char_offset,
            "char_length": len(sentinel),
            "plain_line": src.plain_line,
            "n_rects": len(h.rects),
            "rect": {
                "x": round(h.left, 2),
                "y": round(h.top, 2),
                "width": round(h.right - h.left, 2),
                "height": round(h.height, 2),
            },
            "rects": [
                {"x": round(r.x, 2), "y": round(r.y, 2),
                 "width": round(r.width, 2), "height": round(r.height, 2)}
                for r in h.rects
            ],
        })
    return records, warnings


def derive_measurements(records: list[dict], input_dir: Path) -> dict:
    """Derive layout measurements from correlated sentinel records.

    Every value is either a measured number or an explicit null with a reason,
    so the corpus can report exactly which OPEN/ASSERTED spec items it settled.
    """
    m: dict = {}

    # Line height by block type (rect height == line height for that block; E5).
    heights: dict[str, list[float]] = {}
    for r in records:
        heights.setdefault(r["block_type"], []).append(r["rect"]["height"])
    m["line_height_px"] = {bt: _median(v) for bt, v in heights.items()}

    # Per-heading-level line height (T3): doc 02 has one sentinel per H1..H6.
    heading_levels: dict[int, list[float]] = {}
    for r in records:
        if r["block_type"] == "heading" and r["heading_level"]:
            heading_levels.setdefault(r["heading_level"], []).append(r["rect"]["height"])
    m["heading_line_height_px"] = {
        f"h{lvl}": _median(v) for lvl, v in sorted(heading_levels.items())
    }

    # Word-wrap width (W1): rightmost body glyph edge; left frame edge is x_min.
    body = [r for r in records if r["block_type"] == "body"]
    if body:
        left = min(r["rect"]["x"] for r in body)
        right = max(r["rect"]["x"] + r["rect"]["width"] for r in body)
        m["wrap"] = {
            "body_left_x": round(left, 2),
            "body_right_edge_x": round(right, 2),
            "implied_layout_width": round(right - left, 2),
            "note": "implied width is a lower bound (nearest sentinel to margin)",
        }
    else:
        m["wrap"] = None

    # Paragraph gap (B1): spacing-ladder doc deltas between consecutive sentinels.
    ladder = sorted(
        (r for r in records if r["doc"].endswith("spacing_ladder")),
        key=lambda r: r["rect"]["y"],
    )
    if len(ladder) >= 2:
        deltas = [
            round(ladder[i]["rect"]["y"] - ladder[i - 1]["rect"]["y"], 2)
            for i in range(1, len(ladder))
        ]
        m["paragraph_gap_px"] = {
            "deltas": deltas,
            "min": min(deltas),
            "note": "each delta is the Y gap between successive ladder paragraphs; "
                    "constant deltas ⇒ extra blank lines collapse (B1)",
        }
    else:
        m["paragraph_gap_px"] = None

    # List indentation (B3): rect.x per list level from the lists doc.
    list_levels: dict[int, list[float]] = {}
    for r in records:
        if r["block_type"] == "list" and r["list_level"] is not None:
            list_levels.setdefault(r["list_level"], []).append(r["rect"]["x"])
    m["list_indent_x_by_level"] = {
        f"level{lvl}": _median(v) for lvl, v in sorted(list_levels.items())
    }

    # Heading spacing (B2): first body sentinel Y minus heading sentinel Y, per doc.
    headings_doc = [r for r in records if r["doc"].endswith("headings")]
    heading_gaps = []
    hs = sorted(headings_doc, key=lambda r: r["rect"]["y"])
    for i in range(1, len(hs)):
        if hs[i - 1]["block_type"] == "heading" and hs[i]["block_type"] == "body":
            heading_gaps.append({
                "after_level": hs[i - 1]["heading_level"],
                "gap_px": round(hs[i]["rect"]["y"] - hs[i - 1]["rect"]["y"], 2),
            })
    m["heading_gap_px"] = heading_gaps or None

    # T5 baseline probe: compare T5BASE highlight top against stroke bounds.
    t5 = _derive_t5(records, input_dir)
    m["t5_baseline_offset"] = t5

    return m


def _derive_t5(records: list[dict], input_dir: Path) -> dict | None:
    """Estimate baseline offset from the T5 probe (highlight vs descender)."""
    t5_rec = next((r for r in records if r["sentinel"] == "T5BASE"), None)
    if t5_rec is None:
        return {"value": None, "reason": "T5BASE highlight not found in corpus"}

    probe_doc = t5_rec["doc"]
    stroke_bounds = None
    doc_dir = input_dir / "rm_files" / probe_doc
    if doc_dir.exists():
        for rm in doc_dir.glob("*.rm"):
            b = read_stroke_bounds(rm)
            if b:
                stroke_bounds = b
                break

    if stroke_bounds is None:
        return {
            "value": None,
            "reason": "no handwritten stroke found in T5 probe document",
            "highlight_top_y": t5_rec["rect"]["y"],
        }

    return {
        "value": None,
        "reason": "raw values recorded; resolve 20-vs-25 in Phase 3 (strokes are "
                  "anchor-relative per C4, so this needs the anchor to be absolute)",
        "highlight_top_y": t5_rec["rect"]["y"],
        "highlight_height": t5_rec["rect"]["height"],
        "stroke_min_y_native": round(stroke_bounds["stroke_min_y"], 2),
        "stroke_max_y_native": round(stroke_bounds["stroke_max_y"], 2),
    }


def create_corpus_profile(device: str, input_dir: Path, output_path: Path | None) -> dict:
    """Build the full corpus profile from src/ + rm_files/<doc>/ layout."""
    src = input_dir / "src"
    rm_root = input_dir / "rm_files"
    if not src.exists():
        raise FileNotFoundError(f"No src/ directory under {input_dir}; not a corpus.")

    sources = parse_sentinel_sources(src)

    highlights: list[GlyphHighlight] = []
    if rm_root.exists():
        for doc_dir in sorted(p for p in rm_root.iterdir() if p.is_dir()):
            for rm in sorted(doc_dir.glob("*.rm")):
                highlights.extend(read_glyph_highlights(rm, doc=doc_dir.name))

    records, warnings = build_sentinel_records(sources, highlights)
    measurements = derive_measurements(records, input_dir)

    # Merge with any existing profile.json (recording metadata from record_corpus).
    profile: dict = {}
    default_out = input_dir / "profile.json"
    existing = output_path if (output_path and output_path.exists()) else default_out
    if existing.exists():
        try:
            profile = json.loads(existing.read_text())
        except Exception:  # noqa: BLE001
            profile = {}

    profile["device_name"] = device
    profile["sentinel_count_source"] = len(sources)
    profile["sentinel_count_matched"] = len(records)
    profile["sentinels"] = records
    profile["measurements"] = measurements
    profile["warnings"] = warnings

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(profile, indent=2) + "\n")
        print(f"Wrote corpus profile to: {output_path}")

    print(f"Sentinels: {len(records)}/{len(sources)} matched to device highlights")
    for w in warnings:
        print(f"  WARN: {w}")
    return profile


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Extract device calibration profile")
    parser.add_argument("--device", required=True, help="Device name (e.g., paper_pro_move)")
    parser.add_argument("--input", required=True, help="Corpus dir (with src/ + rm_files/) "
                                                       "or legacy dir of golden .rm files")
    parser.add_argument("--output", help="Output path for profile.json")
    parser.add_argument("--print", action="store_true", help="Print profile to stdout")
    parser.add_argument("--legacy", action="store_true",
                        help="Force legacy flat calibration_*.rm mode")

    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.exists():
        print(f"Error: Input directory does not exist: {input_dir}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else None

    # Prefer corpus mode when a src/ directory is present.
    if not args.legacy and (input_dir / "src").exists():
        profile = create_corpus_profile(args.device, input_dir, output_path)
        if args.print or not output_path:
            print("\n=== Corpus Profile ===")
            print(json.dumps(profile, indent=2))
        return

    profile = create_profile(args.device, input_dir, output_path)

    if args.print or not output_path:
        print("\n=== Device Profile ===")
        print(profile.to_json())


if __name__ == "__main__":
    main()
