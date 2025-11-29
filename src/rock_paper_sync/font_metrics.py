"""Font metrics for accurate text layout using Noto Sans.

This module provides per-character width calculations using the actual font
metrics from Noto Sans Regular, which is the font used by reMarkable devices
for text rendering.

The reMarkable device uses proportional fonts (not monospace), so character
widths vary significantly:
- 'I' = 11.9px, 'N' = 26.6px, 'S' = 19.2px, etc.
- "INSERTED " = ~159px vs naive 15px/char = 135px

Using actual font metrics reduces positioning errors from ~24px to <5px.
"""

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Calibrated point size from device measurements
# Derived from: 159.5px shift / 4928 font units * 1000 = 32.4pt
FONT_POINT_SIZE = 32.4

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


def char_width(char: str, point_size: float = FONT_POINT_SIZE) -> float:
    """Get width of a single character in pixels at given point size.

    Args:
        char: Single character to measure
        point_size: Font size in points/pixels

    Returns:
        Width in pixels
    """
    if len(char) != 1:
        raise ValueError(f"Expected single character, got {len(char)}")

    cmap, glyphset, units_per_em = _load_font()
    glyph_name = cmap.get(ord(char))

    if glyph_name and glyph_name in glyphset:
        return glyphset[glyph_name].width * point_size / units_per_em

    # Fallback for unknown characters - use space width
    space_glyph = cmap.get(ord(" "))
    if space_glyph and space_glyph in glyphset:
        return glyphset[space_glyph].width * point_size / units_per_em

    # Last resort - return average width
    return 15.0


def text_width(text: str, point_size: float = FONT_POINT_SIZE) -> float:
    """Get total width of text string in pixels.

    Args:
        text: String to measure
        point_size: Font size in points/pixels

    Returns:
        Total width in pixels
    """
    return sum(char_width(c, point_size) for c in text)


def text_width_range(text: str, start: int, end: int, point_size: float = FONT_POINT_SIZE) -> float:
    """Get width of a text substring.

    Args:
        text: Full text
        start: Start index (inclusive)
        end: End index (exclusive)
        point_size: Font size in points/pixels

    Returns:
        Width of text[start:end] in pixels
    """
    return text_width(text[start:end], point_size)


def get_font_info() -> dict:
    """Get information about the loaded font for debugging.

    Returns:
        Dictionary with font information
    """
    try:
        cmap, glyphset, units_per_em = _load_font()
        return {
            "font_path": str(_find_font_path()),
            "units_per_em": units_per_em,
            "num_glyphs": len(glyphset),
            "point_size": FONT_POINT_SIZE,
            "sample_widths": {
                "space": char_width(" "),
                "a": char_width("a"),
                "m": char_width("m"),
                "i": char_width("i"),
            },
        }
    except FontMetricsError as e:
        return {"error": str(e)}
