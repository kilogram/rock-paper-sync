#!/usr/bin/env python3
"""Analyze calibration highlight data to determine device font size.

This tool:
1. Extracts highlight rectangle widths from calibration files
2. Accounts for highlight padding
3. Compares to theoretical widths at different point sizes
4. Determines the device's actual font size
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import rmscene
from rmscene.scene_stream import SceneGlyphItemBlock
from fontTools.ttLib import TTFont


def find_noto_sans() -> Path:
    """Find Noto Sans Regular font."""
    search_paths = [
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
        Path.home() / ".local/share/fonts/NotoSans-Regular.ttf",
    ]

    for path in search_paths:
        p = Path(path)
        if p.exists():
            return p

    raise FileNotFoundError("Noto Sans Regular not found")


def get_char_width_at_point_size(char: str, point_size: float, dpi: int = 226) -> float:
    """Calculate theoretical character width at given point size.

    Args:
        char: Character to measure
        point_size: Font size in typographic points
        dpi: Document DPI (default: 226 for reMarkable)

    Returns:
        Width in document pixels
    """
    font_path = find_noto_sans()
    font = TTFont(str(font_path))

    # Get character map and glyph set
    cmap_table = font["cmap"].getcmap(3, 1)
    cmap = cmap_table.cmap
    glyphset = font.getGlyphSet()
    units_per_em = font["head"].unitsPerEm

    # Convert point size to pixel size
    pixel_size = point_size * dpi / 72.0

    # Get glyph width
    glyph_name = cmap.get(ord(char))
    if glyph_name and glyph_name in glyphset:
        return glyphset[glyph_name].width * pixel_size / units_per_em

    return 0.0


def extract_highlights(rm_file: Path) -> list[tuple[str, float]]:
    """Extract highlight text and widths from .rm file.

    Returns:
        List of (text, total_width_px) tuples
    """
    with open(rm_file, "rb") as f:
        blocks = list(rmscene.read_blocks(f))

    highlights = []
    for block in blocks:
        if isinstance(block, SceneGlyphItemBlock):
            item = block.item.value
            if item and hasattr(item, "text") and item.rectangles:
                text = item.text
                total_width = sum(r.w for r in item.rectangles)
                highlights.append((text, total_width))

    return highlights


def main():
    cal_dir = Path("tests/record_replay/testdata/calibration/paper_pro_move")

    print("=" * 70)
    print("FONT SIZE CALIBRATION ANALYSIS")
    print("=" * 70)
    print()

    # Extract data from calibration files
    print("### Extracting Highlight Data ###\n")

    # Get multi-character highlights (more accurate due to shared padding)
    font_sizes_file = cal_dir / "calibration_font_sizes.rm"
    chars_file = cal_dir / "calibration_chars.rm"

    if not font_sizes_file.exists():
        print(f"ERROR: {font_sizes_file} not found")
        return

    # Multi-char highlights
    highlights = extract_highlights(font_sizes_file)

    for text, width in highlights:
        char_count = len(text)
        print(f"Highlight: {repr(text[:30] + '...' if len(text) > 30 else text)}")
        print(f"  Total width: {width:.1f}px")
        print(f"  Character count: {char_count}")
        print(f"  Width/char (incl. padding): {width / char_count:.2f}px")
        print()

    # Analyze the 20 i's measurement (most accurate)
    i_highlights = [h for h in highlights if h[0] == "i" * 20]
    if not i_highlights:
        print("ERROR: No 20-i highlight found")
        return

    text_i, width_i = i_highlights[0]
    char_count_i = len(text_i)

    print("\n### Analysis: 20 i's ###\n")
    print(f"Measured width: {width_i:.1f}px for {char_count_i} characters")
    print()

    # Test different point sizes
    print("Theoretical widths at different point sizes (226 DPI):\n")

    # Estimate padding by comparing single vs multi-char highlights
    if chars_file.exists():
        single_chars = extract_highlights(chars_file)
        single_i = [h for h in single_chars if h[0] == "i"]
        if single_i:
            single_i_width = single_i[0][1]
            multi_i_per_char = width_i / char_count_i
            estimated_padding = single_i_width - multi_i_per_char
            print(f"Estimated highlight padding: ~{estimated_padding:.1f}px per highlight")
            print(f"  (Single 'i': {single_i_width:.1f}px, Multi 'i' avg: {multi_i_per_char:.1f}px)")
            print()

    # Adjust for padding (rough estimate: ~7-10px total for multi-char highlights)
    padding_estimate = 10.0  # Total padding for the entire highlight
    adjusted_width = width_i - padding_estimate

    print(f"Adjusted width (minus ~{padding_estimate:.0f}px padding): {adjusted_width:.1f}px")
    print()

    # Test range of point sizes
    test_sizes = [8.0, 9.0, 9.5, 10.0, 10.5, 11.0, 12.0, 14.0]

    results = []
    for pt_size in test_sizes:
        theoretical_i_width = get_char_width_at_point_size("i", pt_size, dpi=226)
        theoretical_total = theoretical_i_width * char_count_i

        error = abs(theoretical_total - adjusted_width)
        error_pct = (error / adjusted_width) * 100

        results.append((pt_size, theoretical_i_width, theoretical_total, error, error_pct))

        status = "✓" if error_pct < 5.0 else " "
        print(
            f"{status} {pt_size:4.1f}pt: i={theoretical_i_width:.2f}px, "
            f"20i={theoretical_total:.1f}px, error={error:.1f}px ({error_pct:.1f}%)"
        )

    # Find best match
    best = min(results, key=lambda x: x[4])
    print()
    print("=" * 70)
    print(f"BEST MATCH: {best[0]:.1f}pt")
    print(f"  Theoretical: {best[2]:.1f}px")
    print(f"  Measured: {adjusted_width:.1f}px")
    print(f"  Error: {best[3]:.1f}px ({best[4]:.1f}%)")
    print("=" * 70)
    print()

    # Verify with other characters
    print("### Verification with Other Characters ###\n")

    # Check 'm' width
    m_theoretical = get_char_width_at_point_size("m", best[0], dpi=226)
    print(f"Predicted 'm' width at {best[0]:.1f}pt: {m_theoretical:.2f}px")

    if chars_file.exists():
        m_highlights = [h for h in extract_highlights(chars_file) if h[0] == "m"]
        if m_highlights:
            m_measured = m_highlights[0][1]
            # Assume similar padding as 'i'
            m_adjusted = m_measured - estimated_padding
            print(f"Measured 'm' highlight: {m_measured:.1f}px (minus padding: ~{m_adjusted:.1f}px)")
            print(f"Match: {'✓ GOOD' if abs(m_theoretical - m_adjusted) < 3 else '✗ MISMATCH'}")
        print()

    # Recommendation
    print("### Recommendation ###\n")
    print(f"Update DeviceGeometry:")
    print(f"  font_point_size={best[0]:.1f}  # Typographic points (verified via calibration)")
    print()
    print("Note: The current empirical value of 29.5 works because it's treated as")
    print("a pixel scale factor. The proper typographic point size is different.")


if __name__ == "__main__":
    main()
