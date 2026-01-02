"""Layout context for annotation processing.

This module provides the LayoutContext class, which is the primary abstraction
for accessing layout information during annotation processing. It provides:

- Unified access to layout configuration and device geometry
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
from typing import TYPE_CHECKING

from .device import DEFAULT_DEVICE, DeviceGeometry
from .engine import WordWrapLayoutEngine

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class TextAreaConfig:
    """Immutable text area configuration.

    This is a lightweight configuration object that can be passed around
    without the full text content or engine state. It describes the text
    area dimensions and positioning on a reMarkable page.

    Note: This is distinct from config.LayoutConfig which is user-facing
    configuration for pagination (lines_per_page, margins, etc.).

    For new code, prefer creating from DeviceGeometry:

        config = TextAreaConfig.from_geometry(DEFAULT_DEVICE)

    All values default to those from DEFAULT_DEVICE for backward compatibility.
    """

    # All fields have defaults from DEFAULT_DEVICE for backward compatibility
    text_width: float = DEFAULT_DEVICE.text_width
    text_pos_x: float = DEFAULT_DEVICE.text_pos_x
    text_pos_y: float = DEFAULT_DEVICE.text_pos_y
    line_height: float = DEFAULT_DEVICE.line_height
    char_width: float = DEFAULT_DEVICE.char_width

    @classmethod
    def from_geometry(cls, geometry: DeviceGeometry) -> TextAreaConfig:
        """Create text area config from device geometry.

        This is the preferred way to create a TextAreaConfig.

        Args:
            geometry: Device geometry to derive config from

        Returns:
            TextAreaConfig with values from the geometry
        """
        return cls(
            text_width=geometry.text_width,
            text_pos_x=geometry.text_pos_x,
            text_pos_y=geometry.text_pos_y,
            line_height=geometry.line_height,
            char_width=geometry.char_width,
        )

    @classmethod
    def default(cls) -> TextAreaConfig:
        """Create default text area config (Paper Pro geometry).

        This is a convenience method for backward compatibility.
        """
        return cls.from_geometry(DEFAULT_DEVICE)

    @property
    def origin(self) -> tuple[float, float]:
        """Get origin as (x, y) tuple."""
        return (self.text_pos_x, self.text_pos_y)


class LayoutContext:
    """Shared layout context for annotation processing.

    This is the primary abstraction for layout information. It encapsulates:
    - Device geometry (dimensions, positions)
    - Layout configuration (derived from geometry)
    - Layout engine (word-wrap calculations)
    - Text content for the document/page
    - Pre-computed line breaks

    Annotation handlers receive a LayoutContext and use it for:
    - Converting character offsets to pixel positions
    - Converting pixel positions back to character offsets
    - Finding line numbers for Y coordinates
    - Calculating highlight rectangles

    Example:
        # Create context with default device
        context = LayoutContext.from_text(page_text, use_font_metrics=True)

        # Create context with specific device geometry
        context = LayoutContext.from_text(
            page_text,
            geometry=DEFAULT_DEVICE,
            use_font_metrics=True
        )

        # Convert offset to position
        x, y = context.offset_to_position(100)

        # Convert position back to offset
        offset = context.position_to_offset(x, y)

        # Get line number for a Y coordinate
        line = context.get_line_for_y(500.0)
    """

    def __init__(
        self,
        config: TextAreaConfig,
        engine: WordWrapLayoutEngine,
        text_content: str,
        line_breaks: list[int] | None = None,
        geometry: DeviceGeometry | None = None,
    ):
        """Initialize layout context.

        Args:
            config: Layout configuration
            engine: Word-wrap layout engine
            text_content: Full text content for this context
            line_breaks: Pre-computed line breaks (computed if not provided)
            geometry: Device geometry (stored for reference)
        """
        self._config = config
        self._engine = engine
        self._text_content = text_content
        self._line_breaks = line_breaks or engine.calculate_line_breaks(
            text_content, config.text_width
        )
        self._geometry = geometry or DEFAULT_DEVICE

    @property
    def config(self) -> TextAreaConfig:
        """Get text area configuration."""
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

    @property
    def geometry(self) -> DeviceGeometry:
        """Get device geometry."""
        return self._geometry

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
        config: TextAreaConfig | None = None,
        geometry: DeviceGeometry | None = None,
    ) -> LayoutContext:
        """Create layout context from text content.

        This is the primary factory method for creating contexts.

        Args:
            text_content: Full text content
            use_font_metrics: Whether to use Noto Sans font metrics
            config: Optional custom text area configuration
            geometry: Device geometry (config will be derived from this if provided)

        Returns:
            LayoutContext ready for use
        """
        # Determine geometry and config
        if geometry is not None:
            effective_geometry = geometry
            effective_config = config or TextAreaConfig.from_geometry(geometry)
        elif config is not None:
            effective_geometry = DEFAULT_DEVICE
            effective_config = config
        else:
            effective_geometry = DEFAULT_DEVICE
            effective_config = TextAreaConfig.from_geometry(effective_geometry)

        engine = WordWrapLayoutEngine(
            text_width=effective_config.text_width,
            avg_char_width=effective_config.char_width,
            line_height=effective_config.line_height,
            use_font_metrics=use_font_metrics,
        )

        line_breaks = engine.calculate_line_breaks(text_content, effective_config.text_width)

        return cls(effective_config, engine, text_content, line_breaks, effective_geometry)

    @classmethod
    def from_geometry(
        cls,
        text_content: str,
        geometry: DeviceGeometry,
        use_font_metrics: bool = True,
    ) -> LayoutContext:
        """Create layout context from device geometry.

        This is a convenience method for creating contexts with a specific
        device geometry.

        Args:
            text_content: Full text content
            geometry: Device geometry to use
            use_font_metrics: Whether to use Noto Sans font metrics

        Returns:
            LayoutContext ready for use
        """
        return cls.from_text(
            text_content,
            use_font_metrics=use_font_metrics,
            geometry=geometry,
        )

    @classmethod
    def from_rm_file(
        cls,
        rm_file: Path,
        use_font_metrics: bool = True,
        geometry: DeviceGeometry | None = None,
    ) -> LayoutContext:
        """Create layout context from .rm file.

        Extracts text content and origin from the RootTextBlock in the file.
        Delegates to RmFileExtractor for consolidated .rm reading.

        Args:
            rm_file: Path to .rm file
            use_font_metrics: Whether to use Noto Sans font metrics
            geometry: Device geometry (uses DEFAULT_DEVICE if not provided)

        Returns:
            LayoutContext with text content and layout from the file
        """
        from rock_paper_sync.rm_file_extractor import RmFileExtractor

        effective_geometry = geometry or DEFAULT_DEVICE

        try:
            extractor = RmFileExtractor.from_path(rm_file)
            return extractor.get_layout_context(effective_geometry, use_font_metrics)
        except (OSError, ValueError, AttributeError):
            # Return empty context on error - caller will use defaults
            config = TextAreaConfig(
                text_width=effective_geometry.text_width,
                text_pos_x=effective_geometry.text_pos_x,
                text_pos_y=effective_geometry.text_pos_y,
                line_height=effective_geometry.line_height,
                char_width=effective_geometry.char_width,
            )
            return cls.from_text("", use_font_metrics, config, effective_geometry)

    def with_origin(self, origin: tuple[float, float]) -> LayoutContext:
        """Create new context with different origin.

        Useful when processing multiple pages with the same text but
        different origins.

        Args:
            origin: New (x, y) origin

        Returns:
            New LayoutContext with updated origin
        """
        new_config = TextAreaConfig(
            text_width=self._config.text_width,
            text_pos_x=origin[0],
            text_pos_y=origin[1],
            line_height=self._config.line_height,
            char_width=self._config.char_width,
        )

        return LayoutContext(
            new_config, self._engine, self._text_content, self._line_breaks, self._geometry
        )

    def with_text(self, text_content: str) -> LayoutContext:
        """Create new context with different text content.

        Useful when comparing old and new document layouts.

        Args:
            text_content: New text content

        Returns:
            New LayoutContext with updated text
        """
        return LayoutContext.from_text(
            text_content,
            self._engine.use_font_metrics,
            self._config,
            self._geometry,
        )
