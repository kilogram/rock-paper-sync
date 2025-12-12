"""Font metrics for accurate text layout using Noto Sans.

This module provides per-character width calculations using the actual font
metrics from Noto Sans Regular, which is the font used by reMarkable devices
for text rendering.

The reMarkable device uses proportional fonts (not monospace), so character
widths vary significantly:
- 'I' = 11.9px, 'N' = 26.6px, 'S' = 19.2px, etc.
- "INSERTED " = ~159px vs naive 15px/char = 135px

Using actual font metrics reduces positioning errors from ~24px to <5px.

Typography Model:
  - Font size: 10.0 typographic points (1/72 inch)
  - Document DPI: 226 (reMarkable 2 coordinate system)
  - Pixel size: 10.0 × 226 / 72 ≈ 31.4 pixels

This is the PROPER typographic model discovered through empirical calibration
on 2025-12-12. The previous value (29.5) worked because it was used directly
as pixels, but lacked the proper DPI conversion.
"""

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Device font size (discovered via calibration 2025-12-12)
# This is the TYPOGRAPHIC point size (1/72 inch)
# Measured by highlighting 20 'i' characters and comparing to theoretical widths
# at different point sizes. 10.0pt matched within 1.2% error.
DEVICE_FONT_SIZE_PT = 10.0

# Document coordinate DPI (reMarkable 2 format)
# All reMarkable devices use 226 DPI for document coordinates
DEFAULT_DOCUMENT_PPI = 226

# Common paths where Noto Sans might be installed
FONT_SEARCH_PATHS = [
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",  # Arch Linux
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",  # Debian/Ubuntu
    "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",  # Fedora
    "/System/Library/Fonts/Supplemental/NotoSans-Regular.ttf",  # macOS
    Path.home() / ".local/share/fonts/NotoSans-Regular.ttf",  # User fonts
]


class FontMetricsError(Exception):
    """Raised when font metrics cannot be loaded."""

    pass


def _find_font_path() -> Path:
    """Find the Noto Sans Regular font file."""
    for path in FONT_SEARCH_PATHS:
        p = Path(path)
        if p.exists():
            return p
    raise FontMetricsError(
        f"Noto Sans Regular not found. Searched: {[str(p) for p in FONT_SEARCH_PATHS]}"
    )


@lru_cache(maxsize=1)
def _load_font() -> tuple[dict[int, str], dict, int]:
    """Load font and return (cmap, glyphset, units_per_em).

    Returns:
        Tuple of (character map, glyph set, units per em)
    """
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        raise FontMetricsError("fonttools not installed. Run: pip install fonttools")

    font_path = _find_font_path()
    font = TTFont(str(font_path))

    # Get character map (Unicode BMP - platform 3, encoding 1)
    cmap_table = font["cmap"].getcmap(3, 1)
    if cmap_table is None:
        raise FontMetricsError("Font missing Windows Unicode BMP cmap table")
    cmap = cmap_table.cmap

    glyphset = font.getGlyphSet()
    units_per_em = font["head"].unitsPerEm

    return cmap, glyphset, units_per_em


def char_width(
    char: str,
    font_size_pt: float = DEVICE_FONT_SIZE_PT,
    document_ppi: int = DEFAULT_DOCUMENT_PPI,
) -> float:
    """Get width of a single character in document pixels.

    Args:
        char: Single character to measure
        font_size_pt: Font size in typographic points (1/72 inch)
        document_ppi: Document coordinate DPI (default: 226 for reMarkable)

    Returns:
        Width in document pixels

    Example:
        # Get width of 'A' at device font size (10pt @ 226 DPI)
        width = char_width('A')  # ~18.7 pixels

        # Get width at different font size
        width = char_width('A', font_size_pt=12.0)  # ~22.4 pixels
    """
    if len(char) != 1:
        raise ValueError(f"Expected single character, got {len(char)}")

    # Convert typographic points to document pixels
    pixel_size = font_size_pt * document_ppi / 72.0

    cmap, glyphset, units_per_em = _load_font()
    glyph_name = cmap.get(ord(char))

    if glyph_name and glyph_name in glyphset:
        return glyphset[glyph_name].width * pixel_size / units_per_em

    # Fallback for unknown characters - use space width
    space_glyph = cmap.get(ord(" "))
    if space_glyph and space_glyph in glyphset:
        return glyphset[space_glyph].width * pixel_size / units_per_em

    # Last resort - return average width
    return pixel_size * 0.5


def text_width(
    text: str,
    font_size_pt: float = DEVICE_FONT_SIZE_PT,
    document_ppi: int = DEFAULT_DOCUMENT_PPI,
) -> float:
    """Get total width of text string in document pixels.

    Args:
        text: String to measure
        font_size_pt: Font size in typographic points (1/72 inch)
        document_ppi: Document coordinate DPI (default: 226 for reMarkable)

    Returns:
        Total width in document pixels
    """
    return sum(char_width(c, font_size_pt, document_ppi) for c in text)


def text_width_range(
    text: str,
    start: int,
    end: int,
    font_size_pt: float = DEVICE_FONT_SIZE_PT,
    document_ppi: int = DEFAULT_DOCUMENT_PPI,
) -> float:
    """Get width of a text substring.

    Args:
        text: Full text
        start: Start index (inclusive)
        end: End index (exclusive)
        font_size_pt: Font size in typographic points (1/72 inch)
        document_ppi: Document coordinate DPI (default: 226 for reMarkable)

    Returns:
        Width of text[start:end] in document pixels
    """
    return text_width(text[start:end], font_size_pt, document_ppi)


def get_font_info() -> dict:
    """Get information about the loaded font for debugging.

    Returns:
        Dictionary with font information
    """
    try:
        cmap, glyphset, units_per_em = _load_font()
        pixel_size = DEVICE_FONT_SIZE_PT * DEFAULT_DOCUMENT_PPI / 72.0
        return {
            "font_path": str(_find_font_path()),
            "units_per_em": units_per_em,
            "num_glyphs": len(glyphset),
            "font_size_pt": DEVICE_FONT_SIZE_PT,
            "document_ppi": DEFAULT_DOCUMENT_PPI,
            "pixel_size": pixel_size,
            "sample_widths": {
                "space": char_width(" "),
                "a": char_width("a"),
                "m": char_width("m"),
                "i": char_width("i"),
            },
        }
    except FontMetricsError as e:
        return {"error": str(e)}
