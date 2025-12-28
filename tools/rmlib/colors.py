"""Color constants for reMarkable rendering.

Maps rmscene PenColor values to RGB tuples.
"""

from rmscene import si

# RGB color mappings for pen colors
PEN_COLORS: dict[si.PenColor, tuple[int, int, int]] = {
    si.PenColor.BLACK: (0, 0, 0),
    si.PenColor.GRAY: (128, 128, 128),
    si.PenColor.WHITE: (255, 255, 255),
    si.PenColor.YELLOW: (255, 237, 0),
    si.PenColor.GREEN: (0, 172, 0),
    si.PenColor.PINK: (255, 98, 187),
    si.PenColor.BLUE: (0, 98, 204),
    si.PenColor.RED: (214, 43, 61),
    si.PenColor.GRAY_OVERLAP: (160, 160, 160),
    si.PenColor.HIGHLIGHT: (255, 237, 0),  # Same as yellow
    si.PenColor.GREEN_2: (0, 200, 0),
    si.PenColor.CYAN: (0, 180, 200),
    si.PenColor.MAGENTA: (200, 0, 180),
    si.PenColor.YELLOW_2: (255, 220, 0),
}

# Default colors for missing values
DEFAULT_STROKE_COLOR = (0, 0, 0)  # Black
HIGHLIGHT_COLOR = (255, 237, 0)  # Yellow
HIGHLIGHT_ALPHA = 80  # Semi-transparent

# Background color
BACKGROUND_COLOR = (255, 255, 255)  # White


def get_pen_color(color: si.PenColor) -> tuple[int, int, int]:
    """Get RGB tuple for a pen color."""
    return PEN_COLORS.get(color, DEFAULT_STROKE_COLOR)


def get_highlight_rgba() -> tuple[int, int, int, int]:
    """Get RGBA tuple for highlight rendering."""
    return (*HIGHLIGHT_COLOR, HIGHLIGHT_ALPHA)
