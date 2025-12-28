"""Unit tests for the RmRenderer.

Tests basic rendering functionality without requiring device golden files.
"""

import io
import tempfile
import uuid
from pathlib import Path

import pytest
import rmscene
from PIL import Image
from rmscene import si

from tools.rmlib import RmRenderer
from tools.rmlib.colors import BACKGROUND_COLOR, get_pen_color


class TestRmRendererBasics:
    """Basic renderer functionality tests."""

    def test_default_dimensions(self):
        """Renderer creates images with correct default dimensions."""
        renderer = RmRenderer()
        assert renderer.width == 1404
        assert renderer.height == 1872

    def test_custom_dimensions(self):
        """Renderer accepts custom dimensions."""
        renderer = RmRenderer(width=800, height=600)
        assert renderer.width == 800
        assert renderer.height == 600

    def test_background_color(self):
        """Renderer uses correct background color."""
        renderer = RmRenderer()
        assert renderer.background_color == BACKGROUND_COLOR


class TestRmRendererOutput:
    """Tests for rendered output."""

    @pytest.fixture
    def empty_rm_bytes(self) -> bytes:
        """Create minimal valid .rm file bytes with no strokes."""
        output = io.BytesIO()
        # Write minimal .rm file with just header blocks
        test_uuid = uuid.UUID("00000000-0000-0000-0000-000000000001")
        rmscene.write_blocks(
            output,
            [
                rmscene.AuthorIdsBlock(author_uuids={1: test_uuid}),
                rmscene.MigrationInfoBlock(migration_id=rmscene.CrdtId(0, 1), is_device=False),
                rmscene.PageInfoBlock(
                    loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0
                ),
            ],
        )
        return output.getvalue()

    def test_empty_page_renders_white(self, empty_rm_bytes):
        """Empty .rm file renders as white image."""
        renderer = RmRenderer()
        image = renderer.render_bytes(empty_rm_bytes)

        # Check it's the right size
        assert image.size == (1404, 1872)

        # Sample some pixels - should all be white (or close to it)
        # Check corners and center
        for x, y in [(0, 0), (703, 936), (1403, 1871)]:
            pixel = image.getpixel((x, y))
            assert pixel == (255, 255, 255), f"Pixel at ({x}, {y}) should be white, got {pixel}"

    def test_render_returns_rgb_image(self, empty_rm_bytes):
        """Rendered image is in RGB mode."""
        renderer = RmRenderer()
        image = renderer.render_bytes(empty_rm_bytes)
        assert image.mode == "RGB"

    def test_save_png(self, empty_rm_bytes):
        """save_png creates valid PNG file."""
        renderer = RmRenderer()

        with tempfile.NamedTemporaryFile(suffix=".rm", delete=False) as f:
            f.write(empty_rm_bytes)
            rm_path = Path(f.name)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            output_path = Path(f.name)

        try:
            renderer.save_png(rm_path, output_path)

            # Verify PNG was created
            assert output_path.exists()

            # Verify it's a valid PNG
            image = Image.open(output_path)
            assert image.format == "PNG"
            assert image.size == (1404, 1872)
        finally:
            rm_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)


class TestColorMapping:
    """Tests for color mapping."""

    def test_black_color(self):
        """Black pen maps to black RGB."""
        color = get_pen_color(si.PenColor.BLACK)
        assert color == (0, 0, 0)

    def test_gray_color(self):
        """Gray pen maps to gray RGB."""
        color = get_pen_color(si.PenColor.GRAY)
        assert color == (128, 128, 128)

    def test_yellow_color(self):
        """Yellow pen maps to yellow RGB."""
        color = get_pen_color(si.PenColor.YELLOW)
        assert color == (255, 237, 0)

    def test_unknown_color_fallback(self):
        """Unknown color falls back to black."""
        # Create a mock color value that doesn't exist
        from unittest.mock import MagicMock

        fake_color = MagicMock()
        color = get_pen_color(fake_color)
        assert color == (0, 0, 0)


class TestDeviceNativeReference:
    """Tests using the device-native reference file."""

    @pytest.fixture
    def device_native_rm(self) -> Path:
        """Path to device-native reference .rm file."""
        path = (
            Path(__file__).parent.parent
            / "fixtures"
            / "renderer_corpus"
            / "010_device_native_ref"
            / "page.rm"
        )
        if not path.exists():
            pytest.skip(f"Device-native reference not found: {path}")
        return path

    def test_renders_without_error(self, device_native_rm):
        """Device-native reference renders without exceptions."""
        renderer = RmRenderer()
        image = renderer.render(device_native_rm)
        assert image is not None
        assert image.size == (1404, 1872)

    def test_contains_non_white_pixels(self, device_native_rm):
        """Rendered image contains strokes (non-white pixels)."""
        renderer = RmRenderer()
        image = renderer.render(device_native_rm)

        # Count non-white pixels
        pixels = list(image.getdata())
        non_white = sum(1 for p in pixels if p != (255, 255, 255))

        # Should have some strokes rendered
        assert non_white > 100, f"Expected strokes, but only {non_white} non-white pixels"

    def test_contains_highlight_color(self, device_native_rm):
        """Rendered image contains highlight color (yellow-ish pixels)."""
        renderer = RmRenderer()
        image = renderer.render(device_native_rm)

        # Look for yellow/highlight colored pixels
        # Highlight color (255, 237, 0) at alpha=80 blended over white gives ~(255, 249, 175)
        # Detection criteria: high R and G, lower B, with significant R-B or G-B difference
        pixels = list(image.getdata())
        yellow_ish = sum(
            1
            for r, g, b in pixels
            if r > 200 and g > 200 and b < 200 and (r - b > 50 or g - b > 50)
        )

        assert yellow_ish > 50, f"Expected highlight, but only {yellow_ish} yellow-ish pixels"
