"""Layout context for annotation processing.

This module provides the LayoutContext class, which is the primary abstraction
for accessing layout information during annotation processing. It provides:

- Unified access to layout constants and configuration
- Character offset to position conversion
- Position to offset conversion (inverse mapping)
- Pre-computed line breaks for efficiency
- Integration with WordWrapLayoutEngine

All annotation handlers should use LayoutContext rather than accessing
layout constants or the engine directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constants import (
    CHAR_WIDTH,
    LINE_HEIGHT,
    TEXT_POS_X,
    TEXT_POS_Y,
    TEXT_WIDTH,
)
from .engine import WordWrapLayoutEngine


@dataclass(frozen=True)
class LayoutConfig:
    """Immutable layout configuration.

    This is a lightweight configuration object that can be passed around
    without the full text content or engine state.
    """

    text_width: float = TEXT_WIDTH
    text_pos_x: float = TEXT_POS_X
    text_pos_y: float = TEXT_POS_Y
    line_height: float = LINE_HEIGHT
    char_width: float = CHAR_WIDTH

    @property
    def origin(self) -> tuple[float, float]:
        """Get origin as (x, y) tuple."""
        return (self.text_pos_x, self.text_pos_y)


class LayoutContext:
    """Shared layout context for annotation processing.

    This is the primary abstraction for layout information. It encapsulates:
    - Layout configuration (dimensions, positions)
    - Layout engine (word-wrap calculations)
    - Text content for the document/page
    - Pre-computed line breaks

    Annotation handlers receive a LayoutContext and use it for:
    - Converting character offsets to pixel positions
    - Converting pixel positions back to character offsets
    - Finding line numbers for Y coordinates
    - Calculating highlight rectangles

    Example:
        # Create context for a page
        context = LayoutContext.from_text(page_text, use_font_metrics=True)

        # Convert offset to position
        x, y = context.offset_to_position(100)

        # Convert position back to offset
        offset = context.position_to_offset(x, y)

        # Get line number for a Y coordinate
        line = context.get_line_for_y(500.0)
    """

    def __init__(
        self,
        config: LayoutConfig,
        engine: WordWrapLayoutEngine,
        text_content: str,
        line_breaks: list[int] | None = None,
    ):
        """Initialize layout context.

        Args:
            config: Layout configuration
            engine: Word-wrap layout engine
            text_content: Full text content for this context
            line_breaks: Pre-computed line breaks (computed if not provided)
        """
        self._config = config
        self._engine = engine
        self._text_content = text_content
        self._line_breaks = line_breaks or engine.calculate_line_breaks(
            text_content, config.text_width
        )

    @property
    def config(self) -> LayoutConfig:
        """Get layout configuration."""
        return self._config

    @property
    def engine(self) -> WordWrapLayoutEngine:
        """Get layout engine."""
        return self._engine

    @property
    def text_content(self) -> str:
        """Get text content."""
        return self._text_content

    @property
    def line_breaks(self) -> list[int]:
        """Get pre-computed line breaks."""
        return self._line_breaks

    @property
    def origin(self) -> tuple[float, float]:
        """Get text origin as (x, y) tuple."""
        return self._config.origin

    @property
    def text_width(self) -> float:
        """Get text width."""
        return self._config.text_width

    @property
    def line_height(self) -> float:
        """Get line height."""
        return self._config.line_height

    def offset_to_position(self, char_offset: int) -> tuple[float, float]:
        """Convert character offset to (x, y) position.

        Args:
            char_offset: Character offset in the text (0-based)

        Returns:
            (x, y) pixel coordinates for the character
        """
        return self._engine.offset_to_position(
            char_offset, self._text_content, self._config.origin, self._config.text_width
        )

    def position_to_offset(self, x: float, y: float) -> int:
        """Convert (x, y) position to approximate character offset.

        This is useful for mapping stroke positions back to text offsets
        for content anchoring.

        Args:
            x: X coordinate in pixels
            y: Y coordinate in pixels

        Returns:
            Approximate character offset (0-based)
        """
        return self._engine.position_to_offset(
            x, y, self._text_content, self._config.origin, self._config.text_width
        )

    def get_line_for_y(self, y: float) -> int:
        """Get line number for a Y coordinate.

        Args:
            y: Y coordinate in pixels

        Returns:
            Line number (0-based)
        """
        return self._engine.get_line_for_y(y, self._config.text_pos_y)

    def get_line_count(self) -> int:
        """Get total number of lines in the text."""
        return len(self._line_breaks)

    def get_line_start(self, line_num: int) -> int:
        """Get character offset for start of a line.

        Args:
            line_num: Line number (0-based)

        Returns:
            Character offset for start of line
        """
        if line_num < 0 or line_num >= len(self._line_breaks):
            return 0
        return self._line_breaks[line_num]

    def get_line_y(self, line_num: int) -> float:
        """Get Y coordinate for a line.

        Args:
            line_num: Line number (0-based)

        Returns:
            Y coordinate in pixels
        """
        return self._config.text_pos_y + line_num * self._config.line_height

    def calculate_highlight_rectangles(
        self,
        start_offset: int,
        end_offset: int,
        rect_height: float | None = None,
    ) -> list[tuple[float, float, float, float]]:
        """Calculate rectangles for a text highlight span.

        Args:
            start_offset: Character offset where highlight starts
            end_offset: Character offset where highlight ends (exclusive)
            rect_height: Height for rectangles (default: line_height)

        Returns:
            List of (x, y, w, h) tuples, one per line of highlight
        """
        return self._engine.calculate_highlight_rectangles(
            start_offset,
            end_offset,
            self._text_content,
            self._config.origin,
            self._config.text_width,
            rect_height,
        )

    @classmethod
    def from_text(
        cls,
        text_content: str,
        use_font_metrics: bool = True,
        config: LayoutConfig | None = None,
    ) -> LayoutContext:
        """Create layout context from text content.

        This is the primary factory method for creating contexts.

        Args:
            text_content: Full text content
            use_font_metrics: Whether to use Noto Sans font metrics
            config: Optional custom layout configuration

        Returns:
            LayoutContext ready for use
        """
        config = config or LayoutConfig()

        engine = WordWrapLayoutEngine(
            text_width=config.text_width,
            avg_char_width=config.char_width,
            line_height=config.line_height,
            use_font_metrics=use_font_metrics,
        )

        line_breaks = engine.calculate_line_breaks(text_content, config.text_width)

        return cls(config, engine, text_content, line_breaks)

    @classmethod
    def from_rm_file(
        cls,
        rm_file: Path,
        use_font_metrics: bool = True,
    ) -> LayoutContext:
        """Create layout context from .rm file.

        Extracts text content and origin from the RootTextBlock in the file.

        Args:
            rm_file: Path to .rm file
            use_font_metrics: Whether to use Noto Sans font metrics

        Returns:
            LayoutContext with text content and layout from the file
        """
        import rmscene

        text_content = ""
        text_pos_x = TEXT_POS_X
        text_pos_y = TEXT_POS_Y
        text_width = TEXT_WIDTH

        try:
            with rm_file.open("rb") as f:
                blocks = list(rmscene.read_blocks(f))

            for block in blocks:
                if "RootText" in type(block).__name__:
                    text_data = block.value
                    text_pos_x = text_data.pos_x
                    text_pos_y = text_data.pos_y
                    text_width = text_data.width

                    # Extract text from CrdtSequence
                    text_parts = []
                    for item in text_data.items.sequence_items():
                        if hasattr(item, "value") and isinstance(item.value, str):
                            text_parts.append(item.value)
                    text_content = "".join(text_parts)
                    break

        except Exception:
            # Return empty context on error
            pass

        config = LayoutConfig(
            text_width=text_width,
            text_pos_x=text_pos_x,
            text_pos_y=text_pos_y,
        )

        return cls.from_text(text_content, use_font_metrics, config)

    def with_origin(self, origin: tuple[float, float]) -> LayoutContext:
        """Create new context with different origin.

        Useful when processing multiple pages with the same text but
        different origins.

        Args:
            origin: New (x, y) origin

        Returns:
            New LayoutContext with updated origin
        """
        new_config = LayoutConfig(
            text_width=self._config.text_width,
            text_pos_x=origin[0],
            text_pos_y=origin[1],
            line_height=self._config.line_height,
            char_width=self._config.char_width,
        )

        return LayoutContext(new_config, self._engine, self._text_content, self._line_breaks)

    def with_text(self, text_content: str) -> LayoutContext:
        """Create new context with different text content.

        Useful when comparing old and new document layouts.

        Args:
            text_content: New text content

        Returns:
            New LayoutContext with updated text
        """
        return LayoutContext.from_text(text_content, self._engine.use_font_metrics, self._config)
