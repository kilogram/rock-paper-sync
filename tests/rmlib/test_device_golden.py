"""Device golden tests for the RmRenderer.

These tests compare renderer output against actual device thumbnails,
ensuring our rendering matches what the device produces.

The test corpus is located at tests/fixtures/renderer_corpus/ with each
test case containing:
  - page.rm: The source .rm file
  - device_thumbnail.png: Ground truth from the device

Tests use structural similarity (SSIM) or visual comparison to account
for minor rendering differences that don't affect correctness.
"""

from pathlib import Path

import pytest
from PIL import Image

from tools.rmlib import RmRenderer

# Test corpus location
CORPUS_DIR = Path(__file__).parent.parent / "fixtures" / "renderer_corpus"

# Device physical dimensions (Paper Pro Move in landscape)
PHYSICAL_WIDTH = 1696  # pixels at 264 PPI
PHYSICAL_HEIGHT = 954  # pixels at 264 PPI
DEVICE_PPI = 264

# Document dimensions
DOC_WIDTH = 1404
DOC_HEIGHT = 1872

# Scale factor from document to physical coords
SCALE = DEVICE_PPI / 226.0  # ~1.168


def get_test_cases() -> list[Path]:
    """Discover all test cases in corpus."""
    if not CORPUS_DIR.exists():
        return []
    return sorted(CORPUS_DIR.glob("*/page.rm"))


def extract_viewport(rendered: Image.Image) -> Image.Image:
    """Extract the viewport region that matches the device thumbnail.

    The device thumbnail shows a specific viewport of the document,
    not the entire page. This function extracts that region.

    Returns:
        Cropped image matching the device viewport.
    """
    # Viewport dimensions in document coordinates
    viewport_width = int(PHYSICAL_HEIGHT / SCALE)  # 817 (landscape: height becomes width)
    viewport_height = int(PHYSICAL_WIDTH / SCALE)  # 1452 (landscape: width becomes height)

    # Viewport is horizontally centered
    viewport_x = (DOC_WIDTH - viewport_width) // 2  # 293
    viewport_y = 0

    # Clamp to document bounds
    viewport_height = min(viewport_height, DOC_HEIGHT)

    return rendered.crop(
        (
            viewport_x,
            viewport_y,
            viewport_x + viewport_width,
            viewport_y + viewport_height,
        )
    )


def calculate_similarity(img1: Image.Image, img2: Image.Image) -> float:
    """Calculate similarity between two images.

    Returns a score from 0.0 (completely different) to 1.0 (identical).
    Uses a simple pixel-based comparison after converting to grayscale.
    """
    # Ensure same size
    if img1.size != img2.size:
        img1 = img1.resize(img2.size, Image.Resampling.LANCZOS)

    # Convert to grayscale
    gray1 = img1.convert("L")
    gray2 = img2.convert("L")

    # Count matching pixels (within threshold)
    threshold = 30  # Allow small differences
    matching = 0
    total = gray1.width * gray1.height

    for y in range(gray1.height):
        for x in range(gray1.width):
            p1 = gray1.getpixel((x, y))
            p2 = gray2.getpixel((x, y))
            if abs(p1 - p2) <= threshold:
                matching += 1

    return matching / total


def save_diagnostic(
    test_name: str,
    rendered: Image.Image,
    expected: Image.Image,
    output_dir: Path | None = None,
) -> Path:
    """Save diagnostic images when a test fails.

    Creates:
    - rendered.png: Our rendered output (viewport)
    - expected.png: Device thumbnail
    - overlay.png: Difference visualization (red=device, cyan=ours)

    Returns:
        Path to the output directory.
    """
    if output_dir is None:
        output_dir = Path("/tmp/renderer_test_failures") / test_name

    output_dir.mkdir(parents=True, exist_ok=True)

    # Scale rendered to match expected
    rendered_scaled = rendered.resize(expected.size, Image.Resampling.LANCZOS)

    # Save images
    rendered_scaled.save(output_dir / "rendered.png")
    expected.save(output_dir / "expected.png")

    # Create overlay
    overlay = Image.new("RGB", expected.size, (255, 255, 255))
    expected_gray = expected.convert("L")
    rendered_gray = rendered_scaled.convert("L")

    for y in range(expected.height):
        for x in range(expected.width):
            device_val = expected_gray.getpixel((x, y))
            ours_val = rendered_gray.getpixel((x, y))

            if device_val < 200 and ours_val < 200:
                overlay.putpixel((x, y), (0, 0, 0))  # Both
            elif device_val < 200:
                overlay.putpixel((x, y), (255, 0, 0))  # Device only
            elif ours_val < 200:
                overlay.putpixel((x, y), (0, 255, 255))  # Ours only

    overlay.save(output_dir / "overlay.png")

    return output_dir


class TestDeviceGolden:
    """Tests comparing renderer output to device thumbnails."""

    @pytest.fixture
    def renderer(self) -> RmRenderer:
        """Create a renderer instance."""
        return RmRenderer()

    @pytest.mark.parametrize(
        "rm_path",
        get_test_cases(),
        ids=lambda p: p.parent.name,
    )
    def test_renderer_matches_device(self, renderer: RmRenderer, rm_path: Path) -> None:
        """Renderer output matches device thumbnail.

        Uses structural similarity for comparison, not exact pixel match,
        to account for minor rendering differences.
        """
        # Get paths
        test_name = rm_path.parent.name
        thumbnail_path = rm_path.parent / "device_thumbnail.png"

        if not thumbnail_path.exists():
            pytest.skip(f"No device thumbnail for {test_name}")

        # Render
        rendered = renderer.render(rm_path)

        # Extract viewport
        viewport = extract_viewport(rendered)

        # Load expected
        expected = Image.open(thumbnail_path)

        # Scale viewport to thumbnail size
        viewport_scaled = viewport.resize(expected.size, Image.Resampling.LANCZOS)

        # Calculate similarity
        similarity = calculate_similarity(viewport_scaled, expected)

        # Threshold for passing (90% similarity)
        threshold = 0.90

        if similarity < threshold:
            # Save diagnostic images
            diag_dir = save_diagnostic(test_name, viewport, expected)
            pytest.fail(
                f"Similarity {similarity:.2%} below threshold {threshold:.0%}. "
                f"Diagnostics saved to {diag_dir}"
            )

    def test_corpus_exists(self) -> None:
        """Test corpus directory exists and contains test cases."""
        if not CORPUS_DIR.exists():
            pytest.skip(f"Corpus directory not found: {CORPUS_DIR}")

        test_cases = get_test_cases()
        assert len(test_cases) > 0, "No test cases found in corpus"


class TestDeviceNativeRef:
    """Specific tests for the device-native reference document."""

    @pytest.fixture
    def device_native_rm(self) -> Path:
        """Path to device-native reference .rm file."""
        path = CORPUS_DIR / "010_device_native_ref" / "page.rm"
        if not path.exists():
            pytest.skip(f"Device-native reference not found: {path}")
        return path

    @pytest.fixture
    def device_thumbnail(self) -> Image.Image:
        """Load device thumbnail."""
        path = CORPUS_DIR / "010_device_native_ref" / "device_thumbnail.png"
        if not path.exists():
            pytest.skip(f"Device thumbnail not found: {path}")
        return Image.open(path)

    def test_renders_correctly(
        self,
        device_native_rm: Path,
        device_thumbnail: Image.Image,
    ) -> None:
        """Device-native reference renders to match device thumbnail."""
        renderer = RmRenderer()
        rendered = renderer.render(device_native_rm)

        # Extract and scale viewport
        viewport = extract_viewport(rendered)
        viewport_scaled = viewport.resize(device_thumbnail.size, Image.Resampling.LANCZOS)

        # Calculate similarity
        similarity = calculate_similarity(viewport_scaled, device_thumbnail)

        # This is our primary reference document - should be very similar
        assert (
            similarity > 0.85
        ), f"Device-native reference similarity {similarity:.2%} below 85% threshold"

    def test_text_is_visible(self, device_native_rm: Path) -> None:
        """Rendered document contains visible text (non-white pixels)."""
        renderer = RmRenderer()
        rendered = renderer.render(device_native_rm)

        # Count non-white pixels in text region
        viewport = extract_viewport(rendered)
        gray = viewport.convert("L")

        non_white = sum(1 for p in gray.getdata() if p < 250)

        # Should have significant text content
        assert non_white > 1000, f"Only {non_white} non-white pixels, expected >1000"

    def test_strokes_are_visible(self, device_native_rm: Path) -> None:
        """Rendered document contains visible strokes."""
        renderer = RmRenderer()
        rendered = renderer.render(device_native_rm)

        # Check for black pixels (strokes)
        pixels = list(rendered.getdata())
        black_pixels = sum(1 for r, g, b in pixels if r < 50 and g < 50 and b < 50)

        # Should have stroke content
        assert black_pixels > 100, f"Only {black_pixels} black pixels, expected >100"

    def test_highlights_are_visible(self, device_native_rm: Path) -> None:
        """Rendered document contains visible highlights (yellow-ish)."""
        renderer = RmRenderer()
        rendered = renderer.render(device_native_rm)

        # Look for yellow/highlight colored pixels
        pixels = list(rendered.getdata())
        yellow_ish = sum(
            1
            for r, g, b in pixels
            if r > 200 and g > 200 and b < 200 and (r - b > 50 or g - b > 50)
        )

        assert yellow_ish > 50, f"Only {yellow_ish} yellow-ish pixels, expected >50"
