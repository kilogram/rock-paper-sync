"""Comparison utilities for device-native ground truth testing.

Provides tools to compare re-anchored highlights against device-native
ground truth, enabling regression testing of highlight positioning accuracy.

The comparison works at the RECTANGLE level, not the highlight level:
- Flattens all highlights to their component rectangles
- Matches rectangles by spatial proximity (nearest neighbor)
- This handles cases where our code creates 1 multi-rect highlight
  but the device creates multiple single-rect highlights

Example:
    # Compare re-anchored output against device-native ground truth
    assert_highlights_match(
        reanchored_rm_files,
        golden_rm_files,
        tolerance_px=5.0
    )
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

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


def compare_highlights(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
) -> ComparisonResult:
    """Compare highlight rectangles between re-anchored and golden documents.

    Extracts all rectangles from all highlights and matches by proximity.
    This handles cases where highlight structure differs (e.g., 1 multi-rect
    highlight vs multiple single-rect highlights).

    Args:
        reanchored_rm_files: page_uuid -> .rm bytes from re-anchored document
        golden_rm_files: page_uuid -> .rm bytes from device-native document

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

    return match_rectangles_by_proximity(reanchored_rects, golden_rects)


def assert_highlights_match(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    tolerance_px: float = 5.0,
) -> None:
    """Assert that highlight rectangle positions match within tolerance.

    Compares re-anchored rectangles against device-native ground truth
    using spatial proximity matching. Fails if any matched rectangle
    exceeds the tolerance.

    Args:
        reanchored_rm_files: page_uuid -> .rm bytes from re-anchored document
        golden_rm_files: page_uuid -> .rm bytes from device-native document
        tolerance_px: Maximum allowed position difference in pixels

    Raises:
        AssertionError: If any rectangle position exceeds tolerance
    """
    result = compare_highlights(reanchored_rm_files, golden_rm_files)

    if not result.matches:
        ra_count = sum(len(extract_rectangles_from_rm(rm)) for rm in reanchored_rm_files.values())
        g_count = sum(len(extract_rectangles_from_rm(rm)) for rm in golden_rm_files.values())
        raise AssertionError(
            f"No matching rectangles found to compare.\n"
            f"Re-anchored has {ra_count} rectangle(s), golden has {g_count} rectangle(s)."
        )

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
