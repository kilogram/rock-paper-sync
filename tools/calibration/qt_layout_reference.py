#!/usr/bin/env python3
"""PyQt6-based reference implementation for layout engine calibration.

This tool provides access to Qt6's text layout functionality to:
1. Measure character widths using QFontMetricsF
2. Calculate word-wrap line breaks using QTextLayout
3. Compare Qt results with our fonttools-based engine

Usage:
    # Install calibration dependencies first:
    uv pip install "rock-paper-sync[calibration]"

    # Run the tool:
    uv run python tools/calibration/qt_layout_reference.py

    # Compare with fonttools:
    uv run python tools/calibration/qt_layout_reference.py --compare-fonttools

    # Test word-wrap at specific width:
    uv run python tools/calibration/qt_layout_reference.py --width 758 --text "Your text here"

This tool requires PyQt6 which is only available as an optional dependency
for calibration purposes. It is NOT a runtime dependency.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Check if PyQt6 is available
try:
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QFont, QFontMetricsF, QGuiApplication, QTextLayout, QTextOption

    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False


@dataclass
class QtLayoutReference:
    """Qt6 reference implementation for text layout.

    This class provides the same interface as our WordWrapLayoutEngine but uses
    Qt6's native text layout facilities for comparison and calibration.

    Note on font sizes:
    - fonttools at 29.5pt matches device measurements
    - Qt at 29.5pt gives ~32% larger widths due to DPI/scaling differences
    - To match device behavior in Qt, use ~22pt (or set use_pixel_size=True with ~30px)

    For calibration comparisons, use the same point size in both engines and
    compare relative differences rather than absolute values.
    """

    font_family: str = "Noto Sans"
    font_size: float = 29.5  # Point size (note: Qt interprets this differently than fonttools)
    text_width: float = 758.0  # Layout width for word-wrap
    use_pixel_size: bool = False  # If True, interpret font_size as pixels not points

    _app: QGuiApplication | None = None
    _font: QFont | None = None
    _metrics: QFontMetricsF | None = None

    def __post_init__(self):
        """Initialize Qt application and font."""
        if not PYQT6_AVAILABLE:
            raise ImportError(
                "PyQt6 is required for calibration. Install with: "
                "uv pip install 'rock-paper-sync[calibration]'"
            )

        # Create QGuiApplication if not already running
        if QGuiApplication.instance() is None:
            self._app = QGuiApplication(sys.argv)

        # Create font
        self._font = QFont(self.font_family)
        if self.use_pixel_size:
            self._font.setPixelSize(int(self.font_size))
        else:
            self._font.setPointSizeF(self.font_size)
        # Use same style strategy as device (anti-aliased)
        self._font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)

        # Create metrics
        self._metrics = QFontMetricsF(self._font)

    def char_width(self, char: str) -> float:
        """Get width of a single character in pixels.

        Args:
            char: Single character to measure

        Returns:
            Width in pixels
        """
        if len(char) != 1:
            raise ValueError(f"Expected single character, got {len(char)}")
        return self._metrics.horizontalAdvance(char)

    def measure_text(self, text: str) -> float:
        """Get total width of text string in pixels.

        Args:
            text: String to measure

        Returns:
            Total width in pixels
        """
        return self._metrics.horizontalAdvance(text)

    def calculate_line_breaks(self, text: str, width: float | None = None) -> list[int]:
        """Calculate line breaks using Qt's QTextLayout.

        This uses the same word-wrap algorithm that the reMarkable device uses,
        since it's Qt6-based.

        Args:
            text: Text content to lay out
            width: Available width in pixels (default: self.text_width)

        Returns:
            List of character offsets where lines start (first is always 0)
        """
        if width is None:
            width = self.text_width

        layout = QTextLayout(text, self._font)

        # Configure text options
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        layout.setTextOption(option)

        # Perform layout
        layout.beginLayout()
        line_breaks = [0]
        y = 0.0

        while True:
            line = layout.createLine()
            if not line.isValid():
                break

            line.setLineWidth(width)
            line.setPosition(QPointF(0, y))
            y += line.height()

            # Record start of next line (if not the first line)
            next_start = line.textStart() + line.textLength()
            if next_start < len(text):
                line_breaks.append(next_start)

        layout.endLayout()
        return line_breaks

    def get_font_info(self) -> dict:
        """Get information about the loaded font for debugging.

        Returns:
            Dictionary with font information
        """
        return {
            "font_family": self._font.family(),
            "font_size_pt": self._font.pointSizeF(),
            "font_size_px": self._metrics.height(),
            "ascent": self._metrics.ascent(),
            "descent": self._metrics.descent(),
            "leading": self._metrics.leading(),
            "line_spacing": self._metrics.lineSpacing(),
            "average_char_width": self._metrics.averageCharWidth(),
            "sample_widths": {
                "space": self.char_width(" "),
                "a": self.char_width("a"),
                "m": self.char_width("m"),
                "i": self.char_width("i"),
                "x": self.char_width("x"),
            },
        }


def compare_with_fonttools(qt_ref: QtLayoutReference, test_strings: list[str]) -> None:
    """Compare Qt measurements with fonttools measurements.

    Args:
        qt_ref: Qt reference implementation
        test_strings: List of strings to test
    """
    try:
        from rock_paper_sync.font_metrics import char_width as ft_char_width
        from rock_paper_sync.font_metrics import text_width as ft_text_width
    except ImportError:
        print("Error: Could not import rock_paper_sync.font_metrics")
        return

    print("\n=== Character Width Comparison ===")
    print(f"Font: {qt_ref.font_family}, Size: {qt_ref.font_size}pt\n")

    # Compare single characters
    test_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    total_diff = 0.0
    max_diff = 0.0
    max_diff_char = ""

    print("Character widths (Qt vs fonttools):")
    for char in test_chars:
        qt_w = qt_ref.char_width(char)
        ft_w = ft_char_width(char, qt_ref.font_size)
        diff = qt_w - ft_w
        total_diff += abs(diff)
        if abs(diff) > abs(max_diff):
            max_diff = diff
            max_diff_char = char
        if abs(diff) > 0.5:
            print(f"  '{char}': Qt={qt_w:.2f}px, ft={ft_w:.2f}px, diff={diff:+.2f}px")

    print(f"\nMax char diff: '{max_diff_char}' = {max_diff:+.2f}px")
    print(f"Average char diff: {total_diff / len(test_chars):.3f}px")

    print("\n=== String Width Comparison ===")
    for text in test_strings:
        qt_w = qt_ref.measure_text(text)
        ft_w = ft_text_width(text, qt_ref.font_size)
        diff = qt_w - ft_w
        display_text = text[:50] + "..." if len(text) > 50 else text
        print(f"'{display_text}':")
        print(f"  Qt: {qt_w:.1f}px, fonttools: {ft_w:.1f}px, diff: {diff:+.1f}px")


def compare_word_wrap(qt_ref: QtLayoutReference, text: str, width: float) -> None:
    """Compare Qt word-wrap with our implementation.

    Args:
        qt_ref: Qt reference implementation
        text: Text to lay out
        width: Width for word-wrap
    """
    try:
        from rock_paper_sync.layout.device import PAPER_PRO_MOVE
        from rock_paper_sync.layout.engine import WordWrapLayoutEngine
    except ImportError:
        print("Error: Could not import rock_paper_sync layout modules")
        return

    engine = WordWrapLayoutEngine.from_geometry(PAPER_PRO_MOVE, use_font_metrics=True)

    qt_breaks = qt_ref.calculate_line_breaks(text, width)
    our_breaks = engine.calculate_line_breaks(text, width)

    print(f"\n=== Word Wrap Comparison (width={width}px) ===")
    print(f"Qt line breaks: {len(qt_breaks)} lines")
    print(f"Our line breaks: {len(our_breaks)} lines")

    if qt_breaks == our_breaks:
        print("Line breaks MATCH!")
    else:
        print("\nLine breaks DIFFER:")
        max_lines = max(len(qt_breaks), len(our_breaks))
        for i in range(max_lines):
            qt_b = qt_breaks[i] if i < len(qt_breaks) else None
            our_b = our_breaks[i] if i < len(our_breaks) else None

            if qt_b != our_b:
                qt_text = text[qt_b : qt_b + 20] if qt_b is not None else "N/A"
                our_text = text[our_b : our_b + 20] if our_b is not None else "N/A"
                print(f"  Line {i}: Qt={qt_b} '{qt_text}...', Ours={our_b} '{our_text}...'")
            elif i < 5 or i == max_lines - 1:  # Show first few and last line
                qt_text = text[qt_b : qt_b + 30].replace("\n", "\\n") if qt_b is not None else "N/A"
                print(f"  Line {i}: {qt_b} '{qt_text}...'")


def main():
    """Main entry point for calibration tool."""
    import argparse

    parser = argparse.ArgumentParser(description="Qt6 layout reference for calibration")
    parser.add_argument(
        "--font", default="Noto Sans", help="Font family to use (default: Noto Sans)"
    )
    parser.add_argument(
        "--size", type=float, default=29.5, help="Font size in points (default: 29.5)"
    )
    parser.add_argument(
        "--width", type=float, default=758.0, help="Text width for word-wrap (default: 758.0)"
    )
    parser.add_argument("--text", help="Text to measure")
    parser.add_argument(
        "--compare-fonttools", action="store_true", help="Compare with fonttools measurements"
    )
    parser.add_argument(
        "--compare-wrap", action="store_true", help="Compare word-wrap with our implementation"
    )
    parser.add_argument("--info", action="store_true", help="Show font information")

    args = parser.parse_args()

    if not PYQT6_AVAILABLE:
        print("Error: PyQt6 is required for calibration.")
        print("Install with: uv pip install 'rock-paper-sync[calibration]'")
        sys.exit(1)

    qt_ref = QtLayoutReference(
        font_family=args.font, font_size=args.size, text_width=args.width
    )

    if args.info:
        print("=== Font Information ===")
        info = qt_ref.get_font_info()
        for key, value in info.items():
            if isinstance(value, dict):
                print(f"{key}:")
                for k, v in value.items():
                    print(f"  {k}: {v:.2f}px")
            elif isinstance(value, float):
                print(f"{key}: {value:.2f}")
            else:
                print(f"{key}: {value}")

    if args.text:
        width = qt_ref.measure_text(args.text)
        print(f"\nText: '{args.text}'")
        print(f"Width: {width:.2f}px")

        if args.compare_wrap:
            compare_word_wrap(qt_ref, args.text, args.width)

    if args.compare_fonttools:
        test_strings = [
            "This paragraph contains the word \"",
            "The quick brown fox jumps over the lazy dog",
            "INSERTED ",
            "More content that will test word wrapping behavior at the end of lines",
        ]
        if args.text:
            test_strings.append(args.text)
        compare_with_fonttools(qt_ref, test_strings)

    # If no specific action, show basic info and comparison
    if not any([args.info, args.text, args.compare_fonttools, args.compare_wrap]):
        print("=== Qt6 Layout Reference Tool ===")
        print(f"Font: {args.font} @ {args.size}pt")
        print(f"Width: {args.width}px")
        print()
        print("Use --help for available options")
        print("Use --compare-fonttools to compare with our font metrics")
        print("Use --compare-wrap with --text to test word-wrap")


if __name__ == "__main__":
    main()
