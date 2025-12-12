"""Tests comparing our layout engine with Qt6 reference implementation.

These tests require PyQt6 which is an optional calibration dependency.
Skip if not available.
"""

import pytest
from pathlib import Path
import sys

# Check if PyQt6 is available
try:
    from PyQt6.QtGui import QGuiApplication
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False

# Add tools to path for imports
TOOLS_PATH = Path(__file__).parent.parent.parent / "tools"
if str(TOOLS_PATH) not in sys.path:
    sys.path.insert(0, str(TOOLS_PATH))


pytestmark = pytest.mark.skipif(
    not PYQT6_AVAILABLE,
    reason="PyQt6 not installed. Install with: uv pip install 'rock-paper-sync[calibration]'"
)


# Test strings for comparison
CALIBRATION_STRINGS = [
    "This paragraph contains the word \"",
    "The quick brown fox jumps over the lazy dog",
    "INSERTED ",
    "More content that will test word wrapping behavior at the end of lines",
    "a b c d e f g h i j k l m n o p q r s t u v w x y z",
    "iiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii",  # Narrow characters
    "mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmm",  # Wide characters
]


@pytest.fixture
def qt_ref():
    """Create Qt layout reference for testing."""
    if not PYQT6_AVAILABLE:
        pytest.skip("PyQt6 not available")

    from calibration.qt_layout_reference import QtLayoutReference
    return QtLayoutReference(font_family="Noto Sans", font_size=29.5, text_width=758.0)


@pytest.fixture
def layout_engine():
    """Create our layout engine for comparison."""
    from rock_paper_sync.layout.device import PAPER_PRO_MOVE
    from rock_paper_sync.layout.engine import WordWrapLayoutEngine
    return WordWrapLayoutEngine.from_geometry(PAPER_PRO_MOVE, use_font_metrics=True)


class TestCharacterWidths:
    """Test character width measurements.

    Note: Qt and fonttools interpret point sizes differently (~32% scaling).
    These tests verify consistent RELATIVE proportions, not absolute values.
    """

    def test_char_width_ratios_match(self, qt_ref, layout_engine):
        """Character width ratios should be consistent between Qt and fonttools."""
        # Narrow vs wide character ratio should be similar
        qt_i = qt_ref.char_width("i")
        qt_m = qt_ref.char_width("m")
        qt_ratio = qt_m / qt_i

        our_i = layout_engine._get_text_width("i")
        our_m = layout_engine._get_text_width("m")
        our_ratio = our_m / our_i

        # Ratios should be within 10% of each other
        ratio_diff = abs(qt_ratio - our_ratio) / qt_ratio
        assert ratio_diff < 0.10, (
            f"Width ratio mismatch: Qt m/i={qt_ratio:.2f}, ours={our_ratio:.2f}"
        )

    def test_string_width_scaling_consistent(self, qt_ref, layout_engine):
        """String widths should scale consistently (Qt is ~32% larger at same pt)."""
        for text in CALIBRATION_STRINGS[:3]:  # Test first few strings
            qt_width = qt_ref.measure_text(text)
            our_width = layout_engine._get_text_width(text)

            # Qt gives ~32% larger widths at same point size
            # Check scaling is consistent (1.25-1.40x range)
            if our_width > 0:
                ratio = qt_width / our_width
                assert 1.25 < ratio < 1.40, (
                    f"Scaling ratio for '{text[:30]}...': {ratio:.2f} "
                    f"(expected ~1.32, Qt={qt_width:.1f}, ours={our_width:.1f})"
                )


class TestWordWrap:
    """Test word-wrap behavior.

    Note: Qt with larger character widths will wrap earlier at the same line width.
    These tests verify the wrap algorithm behaves consistently, not that breaks match exactly.
    """

    def test_word_wrap_algorithm_produces_breaks(self, qt_ref):
        """Qt word wrap should produce valid line breaks."""
        text = "The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs."

        qt_breaks = qt_ref.calculate_line_breaks(text, 758.0)

        # Should have at least the first line break at 0
        assert len(qt_breaks) >= 1
        assert qt_breaks[0] == 0

    def test_explicit_newlines_preserved_in_our_engine(self, layout_engine):
        """Our engine should handle explicit newlines."""
        text = "Line one.\nLine two.\nLine three."

        our_breaks = layout_engine.calculate_line_breaks(text, 758.0)

        # Should have at least 3 lines (one per explicit line)
        assert len(our_breaks) >= 3

    def test_our_wrap_produces_breaks(self, layout_engine):
        """Our word wrap should produce valid line breaks."""
        text = "The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs."

        our_breaks = layout_engine.calculate_line_breaks(text, 758.0)

        assert len(our_breaks) >= 1
        assert our_breaks[0] == 0

    def test_narrow_words_fit_more_per_line(self, layout_engine):
        """Narrow character words should fit more per line than wide character words."""
        # Use words with spaces so wrapping can occur
        narrow = " ".join(["ii"] * 50)  # 50 narrow words
        wide = " ".join(["mm"] * 50)  # 50 wide words

        narrow_breaks = layout_engine.calculate_line_breaks(narrow, 758.0)
        wide_breaks = layout_engine.calculate_line_breaks(wide, 758.0)

        # Wide characters should need more lines (have more breaks)
        assert len(wide_breaks) >= len(narrow_breaks), (
            f"Wide should need at least as many lines: narrow={len(narrow_breaks)}, wide={len(wide_breaks)}"
        )


class TestFontInfo:
    """Test font information is accessible."""

    def test_font_info_returns_data(self, qt_ref):
        """Font info should return sensible values."""
        info = qt_ref.get_font_info()

        assert info["font_family"] == "Noto Sans"
        assert info["font_size_pt"] == pytest.approx(29.5, rel=0.01)
        assert info["average_char_width"] > 0
        assert info["line_spacing"] > 0

    def test_sample_widths_are_positive(self, qt_ref):
        """Sample character widths should be positive."""
        info = qt_ref.get_font_info()
        sample_widths = info["sample_widths"]

        for char, width in sample_widths.items():
            assert width > 0, f"Character '{char}' has non-positive width: {width}"

        # 'i' should be narrower than 'm'
        assert sample_widths["i"] < sample_widths["m"]
