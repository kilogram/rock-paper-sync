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

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .device import DEFAULT_DEVICE

if TYPE_CHECKING:
    from .device import DeviceGeometry


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
        text_width: float | None = None,
        avg_char_width: float | None = None,
        line_height: float | None = None,
        use_font_metrics: bool = False,
        geometry: DeviceGeometry | None = None,
    ):
        """Initialize word wrap layout engine.

        Args:
            text_width: Text width in pixels (from RootTextBlock.width)
            avg_char_width: Average character width (fallback: 15.0px)
            line_height: Line height in pixels (calibrated: 57.0px from device highlight analysis)
            use_font_metrics: If True, use Noto Sans font metrics for accurate width calculations
            geometry: Device geometry to derive defaults from (uses DEFAULT_DEVICE if not provided)
        """
        # Use provided geometry or default
        effective_geometry = geometry or DEFAULT_DEVICE

        # Use explicit parameters if provided, otherwise derive from geometry
        self.text_width = text_width if text_width is not None else effective_geometry.text_width
        self.avg_char_width = (
            avg_char_width if avg_char_width is not None else effective_geometry.char_width
        )
        self.line_height = (
            line_height if line_height is not None else effective_geometry.line_height
        )
        self.use_font_metrics = use_font_metrics
        self._geometry = effective_geometry
        self._text_width_fn: Callable[[str], float] | None = None

        if use_font_metrics:
            try:
                from rock_paper_sync.font_metrics import text_width as font_text_width

                # Create wrapper that passes font size and DPI from geometry
                def _width_with_dpi(text: str) -> float:
                    return font_text_width(
                        text,
                        font_size_pt=self._geometry.font_point_size,
                        document_ppi=self._geometry.document_ppi,
                    )

                self._text_width_fn = _width_with_dpi
            except Exception:
                # Fall back to fixed width if font metrics unavailable
                self._text_width_fn = None

    @classmethod
    def from_geometry(
        cls,
        geometry: DeviceGeometry,
        use_font_metrics: bool = False,
    ) -> WordWrapLayoutEngine:
        """Create layout engine from device geometry.

        This is the preferred way to create an engine with device-specific
        parameters.

        Args:
            geometry: Device geometry to use
            use_font_metrics: If True, use Noto Sans font metrics

        Returns:
            WordWrapLayoutEngine configured for the device
        """
        # Use layout_text_width for word wrapping - this is the calibrated width
        # that matches the device's actual text wrapping behavior (slightly wider
        # than text_width used in RootTextBlock positioning)
        return cls(
            text_width=geometry.layout_text_width,
            avg_char_width=geometry.char_width,
            line_height=geometry.line_height,
            use_font_metrics=use_font_metrics,
            geometry=geometry,
        )

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

    def split_for_pages(
        self, text: str, lines_per_page: int, first_chunk_lines: int | None = None
    ) -> list[str]:
        """Split text into page-sized chunks.

        Uses word-wrap line breaks to find natural split points at page
        boundaries. Each chunk will fit within the specified line limits.

        Args:
            text: Text content to split
            lines_per_page: Maximum lines per page (used for all chunks after first)
            first_chunk_lines: Maximum lines for first chunk (to fill remaining
                               space on current page). If None, uses lines_per_page.

        Returns:
            List of text chunks, each fitting on one page
        """
        if not text:
            return []

        line_breaks = self.calculate_line_breaks(text, self.text_width)
        num_lines = len(line_breaks)

        # First chunk size (remaining space on current page, or full page)
        first_size = first_chunk_lines if first_chunk_lines is not None else lines_per_page

        if num_lines <= first_size:
            # Fits in first chunk
            return [text]

        chunks = []
        line_idx = 0
        is_first = True

        while line_idx < num_lines:
            # Start of this chunk
            chunk_start = line_breaks[line_idx]

            # Determine chunk size
            chunk_size = first_size if is_first else lines_per_page
            is_first = False

            # End of this chunk
            end_line_idx = min(line_idx + chunk_size, num_lines)

            if end_line_idx < num_lines:
                chunk_end = line_breaks[end_line_idx]
            else:
                chunk_end = len(text)

            chunk = text[chunk_start:chunk_end].strip()
            if chunk:
                chunks.append(chunk)

            line_idx = end_line_idx

        return chunks

    def offset_to_position(
        self, offset: int, text: str, origin: tuple[float, float], width: float, debug: bool = False
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
            debug: If True, print debug info

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

        if debug:
            print(f"[DEBUG-LAYOUT] offset={offset}, line_num={line_num}, line_start={line_start}")
            print(
                f"[DEBUG-LAYOUT] Total line_breaks: {len(line_breaks)}, breaks={line_breaks[:15]}..."
            )
            context = text[max(0, offset - 10) : offset + 10]
            print(f"[DEBUG-LAYOUT] Text at offset: '...{context}...'")
            # Show what's around line breaks 5-8
            for i in range(5, min(9, len(line_breaks))):
                lb = line_breaks[i]
                lb_context = text[max(0, lb - 5) : lb + 20].replace("\n", "\\n")
                print(f"[DEBUG-LAYOUT] Line {i} (offset {lb}): '{lb_context}'")

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

                text_width_before = self._get_text_width(text_before_highlight)
                rect_x = origin[0] + text_width_before
                rect_y = origin[1] + line_idx * self.line_height
                rect_w = self._get_text_width(highlight_text)
                rect_h = rect_height

                rectangles.append((rect_x, rect_y, rect_w, rect_h))

        return rectangles
