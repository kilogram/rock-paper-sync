"""Word-wrapping layout engine for reMarkable documents.

This module provides the WordWrapLayoutEngine class which handles:
- Line break calculation with word wrapping
- Character offset to pixel position conversion
- Highlight rectangle calculation

The engine supports both fixed-width (monospace) and proportional font modes.
When font metrics are available (Noto Sans), it uses actual character widths
for accurate positioning. This is critical for highlight anchoring since the
reMarkable device uses proportional fonts.
"""

from collections.abc import Callable

from .constants import CHAR_WIDTH, LINE_HEIGHT, TEXT_WIDTH


class WordWrapLayoutEngine:
    """Word-wrapping layout engine with optional proportional font metrics.

    By default uses a fixed average character width (15px). When `use_font_metrics=True`,
    uses actual Noto Sans font metrics for accurate proportional width calculations.

    The proportional mode is critical for highlight anchoring - the reMarkable device
    uses Noto Sans (proportional font), and using fixed-width calculations causes
    ~24.5px positioning errors when text shifts.

    This class is the canonical implementation for text layout calculations.
    All annotation handlers should use this (via LayoutContext) rather than
    implementing their own layout logic.

    Example:
        # Basic usage with fixed-width fallback
        engine = WordWrapLayoutEngine()
        line_breaks = engine.calculate_line_breaks(text, 750.0)

        # With proportional font metrics for accuracy
        engine = WordWrapLayoutEngine(use_font_metrics=True)
        x, y = engine.offset_to_position(100, text, (0, 0), 750.0)
    """

    def __init__(
        self,
        text_width: float = TEXT_WIDTH,
        avg_char_width: float = CHAR_WIDTH,
        line_height: float = LINE_HEIGHT,
        use_font_metrics: bool = False,
    ):
        """Initialize word wrap layout engine.

        Args:
            text_width: Text width in pixels (from RootTextBlock.width)
            avg_char_width: Average character width (fallback: 15.0px)
            line_height: Line height in pixels (calibrated: 57.0px from device highlight analysis)
            use_font_metrics: If True, use Noto Sans font metrics for accurate width calculations
        """
        self.text_width = text_width
        self.avg_char_width = avg_char_width
        self.line_height = line_height
        self.use_font_metrics = use_font_metrics
        self._text_width_fn: Callable[[str], float] | None = None

        if use_font_metrics:
            try:
                from rock_paper_sync.font_metrics import text_width as font_text_width

                self._text_width_fn = font_text_width
            except Exception:
                # Fall back to fixed width if font metrics unavailable
                self._text_width_fn = None

    def _get_text_width(self, text: str) -> float:
        """Get width of text string in pixels."""
        if self._text_width_fn is not None:
            return self._text_width_fn(text)
        return len(text) * self.avg_char_width

    def calculate_line_breaks(self, text: str, width: float) -> list[int]:
        """Calculate line breaks using word-wrapping algorithm.

        Algorithm:
        1. Split text into words
        2. Fill each line greedily until next word would overflow
        3. Break at word boundaries (like Qt's QTextLayout)
        4. Handle explicit newlines

        When font metrics are enabled, uses actual character widths from Noto Sans
        instead of fixed average character width.

        Args:
            text: Text content to lay out
            width: Available width in pixels

        Returns:
            List of character offsets where lines start (first is always 0)
        """
        if not text:
            return [0]

        line_breaks = [0]  # First line starts at 0

        # Track current position in text
        pos = 0
        line_start = 0  # Start of current line
        current_line_width = 0.0  # Width of current line in pixels

        while pos < len(text):
            # Check for explicit newline
            if text[pos] == "\n":
                # Start new line after the newline
                pos += 1
                if pos < len(text):
                    line_breaks.append(pos)
                line_start = pos
                current_line_width = 0.0
                continue

            # Find next word boundary (space or newline or end of text)
            word_start = pos
            word_end = pos
            while word_end < len(text) and text[word_end] not in (" ", "\n"):
                word_end += 1

            # Calculate word width (including leading space if not at line start)
            word_text = text[word_start:word_end]
            word_width = self._get_text_width(word_text)

            # Check if word fits on current line
            if current_line_width + word_width > width and pos > line_start:
                # Word doesn't fit, start new line
                line_breaks.append(pos)
                line_start = pos
                current_line_width = 0.0

            # Add word to current line
            current_line_width += word_width
            pos = word_end

            # If we're at a space, consume it and add to line width
            if pos < len(text) and text[pos] == " ":
                current_line_width += self._get_text_width(" ")
                pos += 1

        return line_breaks

    def offset_to_position(
        self, offset: int, text: str, origin: tuple[float, float], width: float
    ) -> tuple[float, float]:
        """Convert character offset to (x, y) coordinates.

        When font metrics are enabled, uses actual character widths from Noto Sans
        for accurate X position calculation. This is critical for highlight anchoring
        since the device uses proportional fonts.

        Args:
            offset: Character offset in the text (0-based)
            text: Full text content
            origin: (x, y) origin point for text rendering
            width: Available width for text

        Returns:
            (x, y) coordinates for the character at the given offset
        """
        # Clamp offset to valid range
        offset = max(0, min(offset, len(text)))

        line_breaks = self.calculate_line_breaks(text, width)

        # Find which line this offset is on
        # We want the LAST line break that is <= offset
        line_num = 0
        line_start = 0
        for i in range(len(line_breaks) - 1, -1, -1):
            if offset >= line_breaks[i]:
                line_num = i
                line_start = line_breaks[i]
                break

        # Calculate X position using text width from line start to offset
        # This uses font metrics when available for accurate proportional positioning
        text_before_offset = text[line_start:offset]
        x_offset = self._get_text_width(text_before_offset)

        # Calculate position
        x = origin[0] + x_offset
        y = origin[1] + (line_num * self.line_height)

        return (x, y)

    def position_to_offset(
        self, x: float, y: float, text: str, origin: tuple[float, float], width: float
    ) -> int:
        """Convert (x, y) coordinates to approximate character offset.

        This is the inverse of offset_to_position, useful for mapping
        stroke positions back to text offsets.

        Args:
            x: X coordinate in pixels
            y: Y coordinate in pixels
            text: Full text content
            origin: (x, y) origin point for text rendering
            width: Available width for text

        Returns:
            Approximate character offset (0-based)
        """
        if not text:
            return 0

        line_breaks = self.calculate_line_breaks(text, width)

        # Find which line based on Y coordinate
        line_num = int((y - origin[1]) / self.line_height)
        line_num = max(0, min(line_num, len(line_breaks) - 1))

        line_start = line_breaks[line_num]
        line_end = line_breaks[line_num + 1] if line_num + 1 < len(line_breaks) else len(text)

        # Find character based on X coordinate within the line
        x_relative = x - origin[0]
        if x_relative <= 0:
            return line_start

        # Binary search for the character position
        # We find the offset where text width just exceeds x_relative
        for offset in range(line_start, line_end + 1):
            text_width = self._get_text_width(text[line_start:offset])
            if text_width >= x_relative:
                return offset

        return line_end

    def get_line_for_y(self, y: float, origin_y: float) -> int:
        """Get line number for a Y coordinate.

        Args:
            y: Y coordinate in pixels
            origin_y: Y origin for text rendering

        Returns:
            Line number (0-based)
        """
        if y < origin_y:
            return 0
        return int((y - origin_y) / self.line_height)

    def get_line_height(self) -> float:
        """Return line height in pixels."""
        return self.line_height

    def get_avg_char_width(self) -> float:
        """Return average character width in pixels."""
        return self.avg_char_width

    def calculate_highlight_rectangles(
        self,
        start_offset: int,
        end_offset: int,
        text: str,
        origin: tuple[float, float],
        width: float,
        rect_height: float | None = None,
    ) -> list[tuple[float, float, float, float]]:
        """Calculate rectangles for a text highlight span.

        Used for re-rendering highlight positions when text shifts. Returns
        rectangles that can be used to update highlight positions after
        markdown modifications.

        When font metrics are enabled, uses actual character widths for accurate
        rectangle positioning and sizing.

        Args:
            start_offset: Character offset where highlight starts
            end_offset: Character offset where highlight ends (exclusive)
            text: Full document text
            origin: (x, y) origin of text rendering
            width: Available text width in pixels
            rect_height: Height for rectangles (default: line_height)

        Returns:
            List of (x, y, w, h) tuples, one per line of highlight
        """
        rect_height = rect_height or self.line_height
        line_breaks = self.calculate_line_breaks(text, width)
        rectangles: list[tuple[float, float, float, float]] = []

        for line_idx in range(len(line_breaks)):
            line_start = line_breaks[line_idx]
            line_end = line_breaks[line_idx + 1] if line_idx + 1 < len(line_breaks) else len(text)

            # Intersection of highlight range with this line
            hl_start = max(start_offset, line_start)
            hl_end = min(end_offset, line_end)

            if hl_start < hl_end:
                # Use font metrics for accurate positioning
                text_before_highlight = text[line_start:hl_start]
                highlight_text = text[hl_start:hl_end]

                rect_x = origin[0] + self._get_text_width(text_before_highlight)
                rect_y = origin[1] + line_idx * self.line_height
                rect_w = self._get_text_width(highlight_text)
                rect_h = rect_height

                rectangles.append((rect_x, rect_y, rect_w, rect_h))

        return rectangles
