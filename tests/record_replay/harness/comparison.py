"""Comparison utilities for device-native ground truth testing.

Provides tools to compare re-anchored annotations (highlights AND strokes)
against device-native ground truth, enabling regression testing of
positioning accuracy.

The comparison works at the RECTANGLE/BOUNDING BOX level:
- Highlights: Flattens to component rectangles, matches by proximity
- Strokes: Uses bounding boxes, matches by proximity

Example:
    # Compare re-anchored output against device-native ground truth
    assert_highlights_match(reanchored_rm_files, golden_rm_files, tolerance_px=5.0)
    assert_strokes_match(reanchored_rm_files, golden_rm_files, tolerance_px=20.0)
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

from rock_paper_sync.annotations import AnnotationType, read_annotations
from rock_paper_sync.annotations.core_types import Rectangle


@dataclass
class RectangleMatch:
    """A matched pair of rectangles with their distance."""

    reanchored: Rectangle
    golden: Rectangle
    reanchored_text: str  # Text from the highlight this rect came from
    golden_text: str

    @property
    def x_delta(self) -> float:
        """X position difference (reanchored - golden)."""
        return self.reanchored.x - self.golden.x

    @property
    def y_delta(self) -> float:
        """Y position difference (reanchored - golden)."""
        return self.reanchored.y - self.golden.y

    @property
    def distance(self) -> float:
        """Euclidean distance between rectangle positions."""
        return math.sqrt(self.x_delta**2 + self.y_delta**2)

    def within_tolerance(self, tolerance_px: float) -> bool:
        """Check if position difference is within tolerance."""
        return self.distance <= tolerance_px

    def format_diff(self) -> str:
        """Format a human-readable diff for this match."""
        lines = [
            f"  Δx={self.x_delta:+.1f}, Δy={self.y_delta:+.1f} "
            f"(distance={self.distance:.1f}px)",
            f"    reanchored: x={self.reanchored.x:.1f}, y={self.reanchored.y:.1f} "
            f"('{self.reanchored_text[:20]}...')",
            f"    golden:     x={self.golden.x:.1f}, y={self.golden.y:.1f} "
            f"('{self.golden_text[:20]}...')",
        ]
        return "\n".join(lines)


@dataclass
class ComparisonResult:
    """Result of comparing rectangles between re-anchored and golden."""

    matches: list[RectangleMatch]
    unmatched_reanchored: list[tuple[Rectangle, str]]  # (rect, highlight_text)
    unmatched_golden: list[tuple[Rectangle, str]]

    @property
    def max_delta_px(self) -> float:
        """Maximum position difference across all matched rectangles."""
        if not self.matches:
            return 0.0
        return max(m.distance for m in self.matches)

    @property
    def rect_count_matches(self) -> bool:
        """Whether total rectangle counts match."""
        total_reanchored = len(self.matches) + len(self.unmatched_reanchored)
        total_golden = len(self.matches) + len(self.unmatched_golden)
        return total_reanchored == total_golden

    def within_tolerance(self, tolerance_px: float) -> bool:
        """Check if all matched rectangles are within tolerance."""
        if not self.matches:
            return True
        return all(m.within_tolerance(tolerance_px) for m in self.matches)


def extract_rectangles_from_rm(rm_data: bytes) -> list[tuple[Rectangle, str]]:
    """Extract all rectangles from highlights in .rm file bytes.

    Args:
        rm_data: Raw bytes of .rm file

    Returns:
        List of (Rectangle, highlight_text) tuples
    """
    rectangles = []
    for annotation in read_annotations(io.BytesIO(rm_data)):
        if annotation.type == AnnotationType.HIGHLIGHT and annotation.highlight:
            hl = annotation.highlight
            for rect in hl.rectangles:
                rectangles.append((rect, hl.text))
    return rectangles


def rect_distance(r1: Rectangle, r2: Rectangle) -> float:
    """Calculate Euclidean distance between two rectangle positions."""
    return math.sqrt((r1.x - r2.x) ** 2 + (r1.y - r2.y) ** 2)


def match_rectangles_by_proximity(
    reanchored: list[tuple[Rectangle, str]],
    golden: list[tuple[Rectangle, str]],
    max_match_distance: float = 100.0,
) -> ComparisonResult:
    """Match rectangles by spatial proximity (nearest neighbor).

    Each reanchored rectangle is matched to its nearest unmatched golden rectangle,
    if within max_match_distance.

    Args:
        reanchored: List of (rect, text) from re-anchored document
        golden: List of (rect, text) from golden document
        max_match_distance: Maximum distance to consider a match

    Returns:
        ComparisonResult with matches and unmatched rectangles
    """
    matches = []
    used_golden = set()
    unmatched_reanchored = []

    for ra_rect, ra_text in reanchored:
        best_match = None
        best_distance = float("inf")
        best_idx = -1

        for i, (g_rect, g_text) in enumerate(golden):
            if i in used_golden:
                continue

            dist = rect_distance(ra_rect, g_rect)
            if dist < best_distance:
                best_distance = dist
                best_match = (g_rect, g_text)
                best_idx = i

        if best_match and best_distance <= max_match_distance:
            matches.append(
                RectangleMatch(
                    reanchored=ra_rect,
                    golden=best_match[0],
                    reanchored_text=ra_text,
                    golden_text=best_match[1],
                )
            )
            used_golden.add(best_idx)
        else:
            unmatched_reanchored.append((ra_rect, ra_text))

    # Collect unmatched golden rectangles
    unmatched_golden = [
        (rect, text) for i, (rect, text) in enumerate(golden) if i not in used_golden
    ]

    return ComparisonResult(
        matches=matches,
        unmatched_reanchored=unmatched_reanchored,
        unmatched_golden=unmatched_golden,
    )


def match_rectangles_by_text(
    reanchored: list[tuple[Rectangle, str]],
    golden: list[tuple[Rectangle, str]],
) -> ComparisonResult:
    """Match rectangles by highlight text (not position).

    This is useful when pagination differs between re-anchored and golden,
    but we want to verify highlights are on the same text.

    Args:
        reanchored: List of (rect, text) from re-anchored document
        golden: List of (rect, text) from golden document

    Returns:
        ComparisonResult with matches based on text content
    """
    matches = []
    used_golden = set()
    unmatched_reanchored = []

    for ra_rect, ra_text in reanchored:
        # Find golden rectangle with same text
        best_match = None
        best_idx = -1

        for i, (g_rect, g_text) in enumerate(golden):
            if i in used_golden:
                continue

            # Match by text content (case-insensitive, strip whitespace)
            if ra_text.strip().lower() == g_text.strip().lower():
                best_match = (g_rect, g_text)
                best_idx = i
                break

        if best_match:
            matches.append(
                RectangleMatch(
                    reanchored=ra_rect,
                    golden=best_match[0],
                    reanchored_text=ra_text,
                    golden_text=best_match[1],
                )
            )
            used_golden.add(best_idx)
        else:
            unmatched_reanchored.append((ra_rect, ra_text))

    # Collect unmatched golden rectangles
    unmatched_golden = [
        (rect, text) for i, (rect, text) in enumerate(golden) if i not in used_golden
    ]

    return ComparisonResult(
        matches=matches,
        unmatched_reanchored=unmatched_reanchored,
        unmatched_golden=unmatched_golden,
    )


def compare_highlights(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    match_by_text: bool = False,
) -> ComparisonResult:
    """Compare highlight rectangles between re-anchored and golden documents.

    Extracts all rectangles from all highlights and matches by proximity
    or by text content.

    Args:
        reanchored_rm_files: page_uuid -> .rm bytes from re-anchored document
        golden_rm_files: page_uuid -> .rm bytes from device-native document
        match_by_text: If True, match by highlight text instead of position.
                       Useful when pagination differs between sources.

    Returns:
        ComparisonResult with matched and unmatched rectangles
    """
    # Extract all rectangles from both sets of .rm files
    reanchored_rects = []
    for rm_data in reanchored_rm_files.values():
        reanchored_rects.extend(extract_rectangles_from_rm(rm_data))

    golden_rects = []
    for rm_data in golden_rm_files.values():
        golden_rects.extend(extract_rectangles_from_rm(rm_data))

    if match_by_text:
        return match_rectangles_by_text(reanchored_rects, golden_rects)
    return match_rectangles_by_proximity(reanchored_rects, golden_rects)


def assert_highlights_match(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    tolerance_px: float = 5.0,
    match_by_text: bool = False,
) -> None:
    """Assert that highlight rectangle positions match within tolerance.

    Compares re-anchored rectangles against device-native ground truth
    using spatial proximity matching or text matching. Fails if any
    matched rectangle exceeds the tolerance.

    Args:
        reanchored_rm_files: page_uuid -> .rm bytes from re-anchored document
        golden_rm_files: page_uuid -> .rm bytes from device-native document
        tolerance_px: Maximum allowed position difference in pixels
        match_by_text: If True, match highlights by text content instead of
                       position. Useful when pagination differs.

    Raises:
        AssertionError: If any rectangle position exceeds tolerance
    """
    result = compare_highlights(reanchored_rm_files, golden_rm_files, match_by_text=match_by_text)

    if not result.matches:
        ra_count = sum(len(extract_rectangles_from_rm(rm)) for rm in reanchored_rm_files.values())
        g_count = sum(len(extract_rectangles_from_rm(rm)) for rm in golden_rm_files.values())
        raise AssertionError(
            f"No matching rectangles found to compare.\n"
            f"Re-anchored has {ra_count} rectangle(s), golden has {g_count} rectangle(s)."
        )

    # When matching by text, we only verify text matches (position tolerance is informational)
    if match_by_text:
        print(f"Matched {len(result.matches)} highlight(s) by text content")
        for m in result.matches:
            print(f"  '{m.reanchored_text[:30]}...' → position delta: {m.distance:.1f}px")
        return

    failures = [m for m in result.matches if not m.within_tolerance(tolerance_px)]

    if failures:
        lines = [
            f"Rectangle position mismatch (tolerance={tolerance_px}px):",
            "",
        ]
        for f in failures:
            lines.append(f.format_diff())
            lines.append("")

        raise AssertionError("\n".join(lines))


def print_highlight_comparison(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
) -> None:
    """Print a detailed comparison of highlight rectangles for debugging.

    Args:
        reanchored_rm_files: page_uuid -> .rm bytes from re-anchored document
        golden_rm_files: page_uuid -> .rm bytes from device-native document
    """
    result = compare_highlights(reanchored_rm_files, golden_rm_files)

    print("\n" + "=" * 60)
    print("RECTANGLE COMPARISON: Re-anchored vs Device-Native")
    print("=" * 60)

    if not result.matches:
        print("No matching rectangles found!")
    else:
        print(f"\nMatched {len(result.matches)} rectangle(s):")
        for i, m in enumerate(result.matches):
            print(f"\nRect {i}:")
            print(m.format_diff())

    if result.unmatched_reanchored:
        print(f"\nUnmatched re-anchored: {len(result.unmatched_reanchored)}")
        for rect, text in result.unmatched_reanchored:
            print(f"  x={rect.x:.1f}, y={rect.y:.1f} ('{text[:20]}...')")

    if result.unmatched_golden:
        print(f"\nUnmatched golden: {len(result.unmatched_golden)}")
        for rect, text in result.unmatched_golden:
            print(f"  x={rect.x:.1f}, y={rect.y:.1f} ('{text[:20]}...')")

    print(f"\nMax delta: {result.max_delta_px:.1f}px")
    print("=" * 60)


# =============================================================================
# STROKE COMPARISON
# =============================================================================


@dataclass
class StrokeMatch:
    """A matched pair of stroke bounding boxes with their distance."""

    reanchored_bbox: Rectangle
    golden_bbox: Rectangle

    @property
    def x_delta(self) -> float:
        """X center difference (reanchored - golden)."""
        ra_cx = self.reanchored_bbox.x + self.reanchored_bbox.w / 2
        g_cx = self.golden_bbox.x + self.golden_bbox.w / 2
        return ra_cx - g_cx

    @property
    def y_delta(self) -> float:
        """Y center difference (reanchored - golden)."""
        ra_cy = self.reanchored_bbox.y + self.reanchored_bbox.h / 2
        g_cy = self.golden_bbox.y + self.golden_bbox.h / 2
        return ra_cy - g_cy

    @property
    def distance(self) -> float:
        """Euclidean distance between bounding box centers."""
        return math.sqrt(self.x_delta**2 + self.y_delta**2)

    def within_tolerance(self, tolerance_px: float) -> bool:
        """Check if position difference is within tolerance."""
        return self.distance <= tolerance_px

    def format_diff(self) -> str:
        """Format a human-readable diff for this match."""
        ra = self.reanchored_bbox
        g = self.golden_bbox
        lines = [
            f"  Δx={self.x_delta:+.1f}, Δy={self.y_delta:+.1f} "
            f"(distance={self.distance:.1f}px)",
            f"    reanchored: bbox=({ra.x:.1f}, {ra.y:.1f}, {ra.w:.1f}, {ra.h:.1f})",
            f"    golden:     bbox=({g.x:.1f}, {g.y:.1f}, {g.w:.1f}, {g.h:.1f})",
        ]
        return "\n".join(lines)


@dataclass
class StrokeComparisonResult:
    """Result of comparing strokes between re-anchored and golden."""

    matches: list[StrokeMatch]
    unmatched_reanchored: list[Rectangle]  # Bounding boxes
    unmatched_golden: list[Rectangle]

    @property
    def max_delta_px(self) -> float:
        """Maximum position difference across all matched strokes."""
        if not self.matches:
            return 0.0
        return max(m.distance for m in self.matches)

    @property
    def stroke_count_matches(self) -> bool:
        """Whether total stroke counts match."""
        total_reanchored = len(self.matches) + len(self.unmatched_reanchored)
        total_golden = len(self.matches) + len(self.unmatched_golden)
        return total_reanchored == total_golden

    def within_tolerance(self, tolerance_px: float) -> bool:
        """Check if all matched strokes are within tolerance."""
        if not self.matches:
            return True
        return all(m.within_tolerance(tolerance_px) for m in self.matches)


def extract_stroke_bboxes_from_rm(rm_data: bytes) -> list[Rectangle]:
    """Extract all stroke bounding boxes from .rm file bytes.

    Args:
        rm_data: Raw bytes of .rm file

    Returns:
        List of bounding box Rectangles
    """
    bboxes = []
    for annotation in read_annotations(io.BytesIO(rm_data)):
        if annotation.type == AnnotationType.STROKE and annotation.stroke:
            bbox = annotation.stroke.bounding_box
            bboxes.append(bbox)
    return bboxes


def bbox_center_distance(b1: Rectangle, b2: Rectangle) -> float:
    """Calculate Euclidean distance between two bounding box centers."""
    c1x = b1.x + b1.w / 2
    c1y = b1.y + b1.h / 2
    c2x = b2.x + b2.w / 2
    c2y = b2.y + b2.h / 2
    return math.sqrt((c1x - c2x) ** 2 + (c1y - c2y) ** 2)


def match_strokes_by_proximity(
    reanchored: list[Rectangle],
    golden: list[Rectangle],
    max_match_distance: float = 200.0,
) -> StrokeComparisonResult:
    """Match stroke bounding boxes by spatial proximity (nearest neighbor).

    Args:
        reanchored: List of bounding boxes from re-anchored document
        golden: List of bounding boxes from golden document
        max_match_distance: Maximum distance to consider a match

    Returns:
        StrokeComparisonResult with matches and unmatched strokes
    """
    matches = []
    used_golden = set()
    unmatched_reanchored = []

    for ra_bbox in reanchored:
        best_match = None
        best_distance = float("inf")
        best_idx = -1

        for i, g_bbox in enumerate(golden):
            if i in used_golden:
                continue

            dist = bbox_center_distance(ra_bbox, g_bbox)
            if dist < best_distance:
                best_distance = dist
                best_match = g_bbox
                best_idx = i

        if best_match and best_distance <= max_match_distance:
            matches.append(
                StrokeMatch(
                    reanchored_bbox=ra_bbox,
                    golden_bbox=best_match,
                )
            )
            used_golden.add(best_idx)
        else:
            unmatched_reanchored.append(ra_bbox)

    # Collect unmatched golden bboxes
    unmatched_golden = [bbox for i, bbox in enumerate(golden) if i not in used_golden]

    return StrokeComparisonResult(
        matches=matches,
        unmatched_reanchored=unmatched_reanchored,
        unmatched_golden=unmatched_golden,
    )


def compare_strokes(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
) -> StrokeComparisonResult:
    """Compare stroke bounding boxes between re-anchored and golden documents.

    Args:
        reanchored_rm_files: page_uuid -> .rm bytes from re-anchored document
        golden_rm_files: page_uuid -> .rm bytes from device-native document

    Returns:
        StrokeComparisonResult with matched and unmatched strokes
    """
    # Extract all bboxes from both sets of .rm files
    reanchored_bboxes = []
    for rm_data in reanchored_rm_files.values():
        reanchored_bboxes.extend(extract_stroke_bboxes_from_rm(rm_data))

    golden_bboxes = []
    for rm_data in golden_rm_files.values():
        golden_bboxes.extend(extract_stroke_bboxes_from_rm(rm_data))

    return match_strokes_by_proximity(reanchored_bboxes, golden_bboxes)


def assert_strokes_match(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    tolerance_px: float = 20.0,
) -> None:
    """Assert that stroke bounding box positions match within tolerance.

    Compares re-anchored strokes against device-native ground truth
    using spatial proximity matching. Fails if any matched stroke
    exceeds the tolerance.

    Args:
        reanchored_rm_files: page_uuid -> .rm bytes from re-anchored document
        golden_rm_files: page_uuid -> .rm bytes from device-native document
        tolerance_px: Maximum allowed position difference in pixels

    Raises:
        AssertionError: If any stroke position exceeds tolerance
    """
    result = compare_strokes(reanchored_rm_files, golden_rm_files)

    if not result.matches:
        ra_count = sum(
            len(extract_stroke_bboxes_from_rm(rm)) for rm in reanchored_rm_files.values()
        )
        g_count = sum(len(extract_stroke_bboxes_from_rm(rm)) for rm in golden_rm_files.values())
        raise AssertionError(
            f"No matching strokes found to compare.\n"
            f"Re-anchored has {ra_count} stroke(s), golden has {g_count} stroke(s)."
        )

    failures = [m for m in result.matches if not m.within_tolerance(tolerance_px)]

    if failures:
        lines = [
            f"Stroke position mismatch (tolerance={tolerance_px}px):",
            "",
        ]
        for f in failures:
            lines.append(f.format_diff())
            lines.append("")

        raise AssertionError("\n".join(lines))


def print_stroke_comparison(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
) -> None:
    """Print a detailed comparison of stroke bounding boxes for debugging.

    Args:
        reanchored_rm_files: page_uuid -> .rm bytes from re-anchored document
        golden_rm_files: page_uuid -> .rm bytes from device-native document
    """
    result = compare_strokes(reanchored_rm_files, golden_rm_files)

    print("\n" + "=" * 60)
    print("STROKE COMPARISON: Re-anchored vs Device-Native")
    print("=" * 60)

    if not result.matches:
        print("No matching strokes found!")
    else:
        print(f"\nMatched {len(result.matches)} stroke(s):")
        for i, m in enumerate(result.matches):
            status = "✅" if m.distance < 20 else "❌"
            print(f"\n{status} Stroke {i}:")
            print(m.format_diff())

    if result.unmatched_reanchored:
        print(f"\nUnmatched re-anchored: {len(result.unmatched_reanchored)}")
        for bbox in result.unmatched_reanchored:
            print(f"  bbox=({bbox.x:.1f}, {bbox.y:.1f}, {bbox.w:.1f}, {bbox.h:.1f})")

    if result.unmatched_golden:
        print(f"\nUnmatched golden: {len(result.unmatched_golden)}")
        for bbox in result.unmatched_golden:
            print(f"  bbox=({bbox.x:.1f}, {bbox.y:.1f}, {bbox.w:.1f}, {bbox.h:.1f})")

    print(f"\nMax delta: {result.max_delta_px:.1f}px")
    print("=" * 60)


# =============================================================================
# DEBUG IMAGE GENERATION
# =============================================================================


def save_comparison_images(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    output_dir: Path,
    prefix: str = "",
) -> list[Path]:
    """Render and save .rm files as PNG images for visual debugging.

    Saves rendered PNGs for both re-anchored and golden .rm files,
    making it easy to visually compare the annotation positions.

    Args:
        reanchored_rm_files: page_uuid -> .rm bytes from re-anchored document
        golden_rm_files: page_uuid -> .rm bytes from device-native document
        output_dir: Directory to save the images
        prefix: Optional prefix for filenames (e.g., test name)

    Returns:
        List of paths to saved images
    """
    from tools.rmlib import RmRenderer

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = []

    renderer = RmRenderer()
    prefix_str = f"{prefix}_" if prefix else ""

    # Render re-anchored files
    for i, (page_uuid, rm_data) in enumerate(sorted(reanchored_rm_files.items())):
        try:
            img = renderer.render_bytes(rm_data)
            out_path = output_dir / f"{prefix_str}reanchored_page{i}_{page_uuid[:8]}.png"
            img.save(out_path)
            saved_paths.append(out_path)
        except Exception as e:
            print(f"Failed to render reanchored page {page_uuid}: {e}")

    # Render golden files
    for i, (page_uuid, rm_data) in enumerate(sorted(golden_rm_files.items())):
        try:
            img = renderer.render_bytes(rm_data)
            out_path = output_dir / f"{prefix_str}golden_page{i}_{page_uuid[:8]}.png"
            img.save(out_path)
            saved_paths.append(out_path)
        except Exception as e:
            print(f"Failed to render golden page {page_uuid}: {e}")

    return saved_paths
