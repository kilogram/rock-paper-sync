"""Test to calibrate and verify font metrics against device data.

These tests validate that our Noto Sans font metrics produce accurate
text width calculations that match device rendering. The reMarkable
device uses Noto Sans (proportional font), so using fixed character
widths causes positioning errors.

Calibration data comes from actual device measurements:
- "INSERTED " causes a 159.5px shift (9 chars, proportional)
- Our old model: 9 × 15px = 135px (24.5px error)
- With font metrics: ~159.7px (accurate!)
"""

from rock_paper_sync.font_metrics import (
    FONT_POINT_SIZE,
    char_width,
    get_font_info,
    text_width,
)


class TestFontMetricsBasics:
    """Basic font metrics functionality tests."""

    def test_font_loads(self):
        """Verify font file can be loaded."""
        info = get_font_info()
        assert "error" not in info, f"Font loading failed: {info.get('error')}"
        assert info["units_per_em"] == 1000  # Noto Sans standard

    def test_char_width_space(self):
        """Space character should have width ~8.4px at default size."""
        width = char_width(" ")
        assert 7 < width < 10, f"Space width {width}px outside expected range"

    def test_char_width_varies(self):
        """Different characters should have different widths (proportional)."""
        width_i = char_width("i")  # Narrow
        width_m = char_width("m")  # Wide
        assert (
            width_m > width_i * 2
        ), f"Proportional check failed: 'm'={width_m}px should be > 2x 'i'={width_i}px"

    def test_text_width_empty(self):
        """Empty string should have zero width."""
        assert text_width("") == 0


class TestFontCalibration:
    """Tests to verify font metrics match device measurements."""

    def test_inserted_width_matches_device(self):
        """Verify that 'INSERTED ' width matches device observation.

        Device showed 159.5px shift for "INSERTED " text insertion.
        This is the primary calibration point.
        """
        expected_shift = 159.5
        calculated = text_width("INSERTED ")

        # Allow 2% tolerance for minor font version differences
        tolerance = expected_shift * 0.02
        assert abs(calculated - expected_shift) < tolerance, (
            f"Font calibration error: {calculated:.1f}px vs {expected_shift}px expected. "
            f"Point size may need adjustment from {FONT_POINT_SIZE}"
        )

    def test_individual_char_widths(self):
        """Verify individual character widths are reasonable.

        These values were measured from Noto Sans at 32.4pt point size.
        """
        expected = {
            "I": 11.0,  # Narrow capital
            "N": 24.6,  # Medium capital
            "S": 17.8,  # Medium capital
            "E": 18.0,  # Medium capital
            "R": 20.1,  # Medium capital
            "T": 18.0,  # Medium capital
            " ": 8.4,  # Space
            "t": 11.7,  # Narrow lowercase
            "a": 18.2,  # Medium lowercase
            "r": 13.4,  # Narrow lowercase
            "g": 19.9,  # Medium lowercase
            "e": 18.3,  # Medium lowercase
        }

        for char, expected_width in expected.items():
            actual = char_width(char)
            tolerance = expected_width * 0.1  # 10% tolerance
            assert (
                abs(actual - expected_width) < tolerance
            ), f"Char '{char}' width {actual:.1f}px far from expected {expected_width}px"


class TestLayoutEngineWithFontMetrics:
    """Test layout engine using font metrics."""

    def test_x_shift_accuracy(self):
        """Verify X shift calculation matches device behavior."""
        from rock_paper_sync.layout import WordWrapLayoutEngine

        engine = WordWrapLayoutEngine(
            text_width=750.0,
            avg_char_width=15.0,  # Fallback
            line_height=57.0,
            use_font_metrics=True,
        )

        old_text = "The target word is here."
        new_text = "The INSERTED target word is here."
        origin = (-375.0, 94.0)

        # Find "target" position in both texts
        old_offset = old_text.index("target")
        new_offset = new_text.index("target")

        old_pos = engine.offset_to_position(old_offset, old_text, origin, 750.0)
        new_pos = engine.offset_to_position(new_offset, new_text, origin, 750.0)

        x_delta = new_pos[0] - old_pos[0]

        # Should be ~159.5px (the INSERTED text width)
        expected_delta = 159.5
        tolerance = 5.0  # 5px tolerance
        assert abs(x_delta - expected_delta) < tolerance, (
            f"X shift {x_delta:.1f}px far from expected {expected_delta}px. "
            f"Font metrics may not be working correctly."
        )

    def test_fallback_to_fixed_width(self):
        """Verify graceful fallback when font metrics unavailable."""
        from rock_paper_sync.layout import WordWrapLayoutEngine

        # This should work even if we can't load fonts
        engine = WordWrapLayoutEngine(
            text_width=750.0,
            avg_char_width=15.0,
            line_height=57.0,
            use_font_metrics=False,  # Explicitly disable
        )

        pos = engine.offset_to_position(10, "Hello world", (0, 0), 750.0)
        expected_x = 10 * 15.0  # 10 chars × 15px
        assert pos[0] == expected_x


class TestDeltaCalculation:
    """Test that delta-based approach produces accurate results."""

    def test_delta_cancels_systematic_errors(self):
        """Verify delta approach gives accurate relative positioning.

        The key insight: even if absolute positions have small errors,
        the DELTA between old and new positions should be accurate
        because we use the same model for both calculations.
        """
        from rock_paper_sync.layout import WordWrapLayoutEngine

        engine = WordWrapLayoutEngine(
            text_width=750.0,
            use_font_metrics=True,
        )

        origin = (-375.0, 94.0)

        # Simulate text modification
        old_text = "Hello world"
        new_text = "Hello INSERTED world"

        # Find "world" in both
        old_world_offset = old_text.index("world")
        new_world_offset = new_text.index("world")

        old_pos = engine.offset_to_position(old_world_offset, old_text, origin, 750.0)
        new_pos = engine.offset_to_position(new_world_offset, new_text, origin, 750.0)

        # Delta should equal width of "INSERTED "
        expected_delta = text_width("INSERTED ")
        actual_delta = new_pos[0] - old_pos[0]

        # Very tight tolerance since it's the same model
        assert (
            abs(actual_delta - expected_delta) < 0.1
        ), f"Delta calculation error: {actual_delta:.2f}px vs {expected_delta:.2f}px"
