"""Coordinate system for reMarkable documents.

This module provides type-safe coordinate transformations between the various
coordinate spaces used in reMarkable .rm files and document generation.

Coordinate Spaces (see plan for full taxonomy):
- Document Space (Continuous): The "lingua franca" - coordinates across all pages
- Page-Local Space: What .rm files actually store (per-page coordinates)
- Text-Relative Space: Relative to RootTextBlock origin (center-relative X)
- Anchor-Relative Space: Relative to TreeNodeBlock anchors (for strokes)

Usage:
    # Convert stroke coordinates to document space
    resolver = AnchorResolver.from_rm_file(rm_path)
    anchor = resolver.get_anchor(parent_id)
    if anchor:
        doc_point = AnchorRelativePoint(x, y).to_document(anchor)

    # Convert between document and page-local
    page_local = doc_point.to_page_local()  # Uses DEFAULT_LAYOUT
    doc_point = page_local.to_document()

    # Custom page layouts
    layout = PageLayout(page_heights=(1872.0, 1404.0, 1872.0))  # Mixed pages
    page_local = doc_point.to_page_local(layout)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rmscene.tagged_block_common import CrdtId

    from .layout import LayoutContext
    from .rm_file_extractor import RmFileExtractor

# =============================================================================
# Page Constants (from DeviceGeometry - single source of truth)
# =============================================================================

PAGE_WIDTH = 1404
PAGE_HEIGHT = 1872
PAGE_CENTER_X = 702.0
DEFAULT_TEXT_POS_X = -375.0
DEFAULT_TEXT_POS_Y = 234.0
NEGATIVE_Y_OFFSET = 82.0  # line_height + baseline_offset for dual-anchor Y

# Special markers
END_OF_DOC_MARKER = 281474976710655  # 0xFFFFFFFFFFFF - anchor at end of document
ROOT_LAYER_ID = (0, 11)  # CrdtId for absolute coordinate space (no transformation)


# =============================================================================
# Pagination Flexibility
# =============================================================================


class PaginationPolicy(Enum):
    """How content flows across pages.

    CONTINUOUS: Content flows across pages (Y can exceed bounds)
    STRICT_PAGE: Content stays on its original page (no cross-page migration)
    """

    CONTINUOUS = "continuous"
    STRICT_PAGE = "strict"


@dataclass(frozen=True)
class PageLayout:
    """Defines page boundaries for a document.

    Supports non-uniform page heights (templates, landscape pages, etc.).

    Attributes:
        page_heights: Height of each page (tuple for immutability)
        default_height: Height for pages beyond the explicit list
    """

    page_heights: tuple[float, ...]
    default_height: float = PAGE_HEIGHT

    @classmethod
    def uniform(cls, height: float = PAGE_HEIGHT) -> PageLayout:
        """Create uniform page layout (default case)."""
        return cls(page_heights=(), default_height=height)

    def height_of(self, page: int) -> float:
        """Get height of a specific page."""
        if page < len(self.page_heights):
            return self.page_heights[page]
        return self.default_height

    def y_start_of(self, page: int) -> float:
        """Get continuous Y where page starts."""
        if page <= 0:
            return 0.0
        # Sum heights of all previous pages
        total = sum(self.page_heights[i] for i in range(min(page, len(self.page_heights))))
        # Add default height for pages beyond the explicit list
        if page > len(self.page_heights):
            total += (page - len(self.page_heights)) * self.default_height
        return total

    def page_for_y(self, y: float) -> int:
        """Find which page a continuous Y falls on."""
        if y < 0:
            return 0
        cumulative = 0.0
        for page in range(len(self.page_heights)):
            height = self.page_heights[page]
            if y < cumulative + height:
                return page
            cumulative += height
        # Beyond explicit pages, use default height
        remaining = y - cumulative
        return len(self.page_heights) + int(remaining / self.default_height)


# Default layout for simple uniform pages
DEFAULT_LAYOUT = PageLayout.uniform()


# =============================================================================
# Core Coordinate Types
# =============================================================================


@dataclass(frozen=True)
class TextOrigin:
    """Text area origin (from RootTextBlock).

    Attributes:
        x: X position (typically -375, center-relative)
        y: Y position (typically 234, from page top)
        width: Text width (typically 750)
    """

    x: float = DEFAULT_TEXT_POS_X
    y: float = DEFAULT_TEXT_POS_Y
    width: float = 750.0


@dataclass(frozen=True)
class DocumentPoint:
    """Continuous document coordinates across all pages.

    This is the "lingua franca" - all other point types convert to/from this.
    - X: 0 to PAGE_WIDTH (1404)
    - Y: 0 to infinity (continuous across pages)
    """

    x: float
    y: float

    @classmethod
    def create(cls, x: float, y: float) -> DocumentPoint:
        """Create with validation - use for external inputs.

        Raises:
            ValueError: If x is outside page bounds or y is negative
        """
        if not (0 <= x <= PAGE_WIDTH):
            raise ValueError(f"x={x} outside page bounds [0, {PAGE_WIDTH}]")
        if y < 0:
            raise ValueError(f"y={y} cannot be negative")
        return cls(x, y)

    @classmethod
    def unsafe(cls, x: float, y: float) -> DocumentPoint:
        """Create without validation - for internal transformations.

        Use when you know coordinates are valid (e.g., from rmscene).
        """
        return cls(x, y)

    def page_index(self, layout: PageLayout = DEFAULT_LAYOUT) -> int:
        """Which page this point is on (0-indexed)."""
        return layout.page_for_y(self.y)

    def to_page_local(self, layout: PageLayout = DEFAULT_LAYOUT) -> PageLocalPoint:
        """Convert to page-local coordinates for .rm file output."""
        page = layout.page_for_y(self.y)
        local_y = self.y - layout.y_start_of(page)
        return PageLocalPoint(page=page, x=self.x, y=local_y)

    def to_text_relative(self, origin: TextOrigin) -> TextRelativePoint:
        """Convert to text-relative coordinates."""
        return TextRelativePoint(
            x=self.x - PAGE_CENTER_X,
            y=self.y - origin.y,
        )


@dataclass(frozen=True)
class PageLocalPoint:
    """Page-local coordinates for .rm file I/O.

    This is what .rm files actually store - coordinates relative to a specific page.

    Attributes:
        page: 0-indexed page number
        x: X coordinate (0 to PAGE_WIDTH)
        y: Y coordinate (0 to page height, can exceed for scrolled content)
    """

    page: int
    x: float
    y: float

    def to_document(self, layout: PageLayout = DEFAULT_LAYOUT) -> DocumentPoint:
        """Convert to continuous document coordinates."""
        continuous_y = layout.y_start_of(self.page) + self.y
        return DocumentPoint.unsafe(x=self.x, y=continuous_y)


@dataclass(frozen=True)
class TextRelativePoint:
    """Center-relative X, text_pos_y-relative Y (as in RootTextBlock).

    Attributes:
        x: Center-relative X (-375 to +375 for standard text area)
        y: Y relative to text_pos_y
    """

    x: float
    y: float

    def to_document(self, origin: TextOrigin) -> DocumentPoint:
        """Convert to absolute document coordinates."""
        return DocumentPoint.unsafe(
            x=PAGE_CENTER_X + self.x,
            y=origin.y + self.y,
        )


@dataclass(frozen=True)
class AnchorPoint:
    """Resolved anchor position for stroke groups.

    Created by AnchorResolver - hides CRDT/TreeNodeBlock details.

    Attributes:
        x: anchor_origin_x from TreeNodeBlock
        y: Y position resolved from character offset
        char_offset: Original character offset (for debugging)
    """

    x: float
    y: float
    char_offset: int | None = None


@dataclass(frozen=True)
class AnchorRelativePoint:
    """Stroke coordinates relative to their parent anchor.

    Strokes in .rm files store coordinates relative to their parent TreeNodeBlock's
    anchor position. Use to_document() to convert to absolute coordinates.

    The dual-anchor Y offset (82px for negative Y) is applied automatically.
    """

    x: float
    y: float

    def to_document(self, anchor: AnchorPoint) -> DocumentPoint:
        """Convert to absolute document coordinates.

        Automatically applies dual-anchor Y offset for negative Y values.
        This exists because the device anchors strokes differently depending
        on whether they're drawn above or below the text line.
        """
        y_offset = NEGATIVE_Y_OFFSET if self.y < 0 else 0.0
        return DocumentPoint.unsafe(
            x=anchor.x + self.x,
            y=anchor.y + y_offset + self.y,
        )


# =============================================================================
# Utility Functions
# =============================================================================


def is_root_layer(parent_id: CrdtId | None) -> bool:
    """Check if parent uses absolute coordinates (no transformation needed).

    Items parented to the root layer CrdtId(0, 11) use absolute document
    coordinates. Items with no parent (None) also use absolute coordinates.
    All other parent_ids use anchor-relative coordinates.

    Args:
        parent_id: Parent layer CrdtId or None

    Returns:
        True if this uses absolute coordinates (None or root layer)
    """
    if parent_id is None:
        return True  # No parent means absolute coordinates
    return (parent_id.part1, parent_id.part2) == ROOT_LAYER_ID


# =============================================================================
# AnchorResolver - Handles CRDT Complexity
# =============================================================================


class AnchorResolver:
    """Resolves per-parent anchor positions from TreeNodeBlocks.

    Handles all CRDT complexity:
    - Building CrdtId -> char_offset mapping from RootTextBlock
    - Resolving char_offset -> Y position via LayoutContext
    - End-of-document marker handling
    - Caching for efficiency

    Usage:
        resolver = AnchorResolver.from_rm_file(rm_path)
        anchor = resolver.get_anchor(parent_id)
        if anchor:
            doc_point = AnchorRelativePoint(x, y).to_document(anchor)
    """

    def __init__(
        self,
        parent_to_anchor_x: dict[CrdtId, float],
        parent_to_char_offset: dict[CrdtId, int],
        layout_ctx: LayoutContext,
        default_text_origin: TextOrigin,
    ) -> None:
        """Initialize resolver with extracted anchor data.

        Use factory methods from_rm_file() or from_blocks() instead of
        calling this directly.

        Args:
            parent_to_anchor_x: Map from parent_id to X anchor offset
            parent_to_char_offset: Map from parent_id to character offset
            layout_ctx: Layout context for character offset -> Y resolution
            default_text_origin: Default text origin for unknown parent_ids
        """
        self._parent_to_anchor_x = parent_to_anchor_x
        self._parent_to_char_offset = parent_to_char_offset
        self._layout_ctx = layout_ctx
        self._default_text_origin = default_text_origin
        self._cache: dict[CrdtId, AnchorPoint | None] = {}

    @property
    def layout_context(self) -> LayoutContext:
        """Get the layout context used for Y position resolution."""
        return self._layout_ctx

    @property
    def text_content(self) -> str:
        """Get the text content from the layout context."""
        return self._layout_ctx.text_content

    @classmethod
    def from_rm_file(cls, rm_path: Path) -> AnchorResolver:
        """Create resolver from .rm file.

        Reads the file, extracts TreeNodeBlocks for anchor mappings,
        and creates a LayoutContext from the RootTextBlock.
        Delegates file reading to RmFileExtractor.

        Args:
            rm_path: Path to .rm file

        Returns:
            AnchorResolver ready for anchor lookups
        """
        from rock_paper_sync.rm_file_extractor import RmFileExtractor

        extractor = RmFileExtractor.from_path(rm_path)
        return cls.from_extractor(extractor)

    @classmethod
    def from_blocks(cls, blocks: list) -> AnchorResolver:
        """Create resolver from pre-read rmscene blocks.

        Use this when you already have blocks from rmscene.read_blocks()
        to avoid re-reading the file.

        Args:
            blocks: List of rmscene blocks from read_blocks()

        Returns:
            AnchorResolver ready for anchor lookups
        """
        from rock_paper_sync.rm_file_extractor import RmFileExtractor

        extractor = RmFileExtractor.from_blocks(blocks)
        return cls.from_extractor(extractor)

    @classmethod
    def from_extractor(cls, extractor: RmFileExtractor) -> AnchorResolver:
        """Create resolver from RmFileExtractor.

        The primary factory method that uses the consolidated extractor.

        Args:
            extractor: RmFileExtractor with pre-extracted data

        Returns:
            AnchorResolver ready for anchor lookups
        """
        from .layout import LayoutContext, TextAreaConfig

        # Get text content and origin from extractor
        text_pos_x = extractor.text_origin.pos_x
        text_pos_y = extractor.text_origin.pos_y
        crdt_to_char = extractor.crdt_to_char

        # Create layout context for Y position resolution
        layout_ctx = LayoutContext.from_text(
            extractor.text_content,
            use_font_metrics=True,
            config=TextAreaConfig(text_pos_x=text_pos_x, text_pos_y=text_pos_y),
        )

        default_text_origin = TextOrigin(x=text_pos_x, y=text_pos_y)

        # Extract per-parent anchor mappings from TreeNodeBlocks
        parent_to_anchor_x: dict[CrdtId, float] = {}
        parent_to_char_offset: dict[CrdtId, int] = {}

        for block in extractor.blocks:
            if type(block).__name__ == "TreeNodeBlock":
                if hasattr(block, "group") and block.group:
                    g = block.group
                    node_id = g.node_id

                    # Get anchor_origin_x
                    if (
                        hasattr(g, "anchor_origin_x")
                        and g.anchor_origin_x
                        and g.anchor_origin_x.value is not None
                    ):
                        parent_to_anchor_x[node_id] = g.anchor_origin_x.value

                    # Get anchor_id and resolve CRDT ID to character offset
                    if hasattr(g, "anchor_id") and g.anchor_id and g.anchor_id.value:
                        anchor_crdt = g.anchor_id.value
                        if hasattr(anchor_crdt, "part2"):
                            # Check for end-of-document sentinel
                            if anchor_crdt.part2 == END_OF_DOC_MARKER:
                                parent_to_char_offset[node_id] = END_OF_DOC_MARKER
                            elif anchor_crdt in crdt_to_char:
                                # Look up actual character offset from CRDT ID
                                parent_to_char_offset[node_id] = crdt_to_char[anchor_crdt]

        return cls(parent_to_anchor_x, parent_to_char_offset, layout_ctx, default_text_origin)

    def get_anchor(self, parent_id: CrdtId | None) -> AnchorPoint | None:
        """Get anchor position for a parent_id.

        Returns None if parent_id is unknown or invalid (caller should skip annotation).
        Uses caching for efficiency.

        Args:
            parent_id: Parent layer CrdtId

        Returns:
            AnchorPoint with resolved anchor_x and anchor_y, or None if invalid
        """
        # Handle None or root layer
        if parent_id is None:
            return None
        if is_root_layer(parent_id):
            # Root layer uses absolute coordinates - no anchor transformation
            return AnchorPoint(x=0.0, y=0.0, char_offset=None)

        # Check cache
        if parent_id in self._cache:
            return self._cache[parent_id]

        # Resolve anchor_x
        anchor_x = self._parent_to_anchor_x.get(parent_id, self._default_text_origin.x)

        # Resolve anchor_y from character offset
        char_offset = self._parent_to_char_offset.get(parent_id)

        if char_offset is None:
            # No anchor_id for this parent - mark as invalid
            self._cache[parent_id] = None
            return None

        if char_offset == END_OF_DOC_MARKER:
            # End of document marker - position after last character
            text_len = len(self._layout_ctx.text_content)
            if text_len > 0:
                _, last_y = self._layout_ctx.offset_to_position(text_len - 1)
                anchor_y = last_y + self._layout_ctx.line_height
            else:
                anchor_y = self._default_text_origin.y
            anchor = AnchorPoint(x=anchor_x, y=anchor_y, char_offset=END_OF_DOC_MARKER)
        elif char_offset < len(self._layout_ctx.text_content):
            # Normal case - resolve character offset to Y position
            _, anchor_y = self._layout_ctx.offset_to_position(char_offset)
            anchor = AnchorPoint(x=anchor_x, y=anchor_y, char_offset=char_offset)
        else:
            # Character offset out of bounds - invalid
            self._cache[parent_id] = None
            return None

        self._cache[parent_id] = anchor
        return anchor

    def get_text_end_y(self) -> float:
        """Get Y position after the last line of text.

        Useful for detecting implicit paragraphs (strokes below all text).

        Returns:
            Y coordinate for end of text
        """
        text_len = len(self._layout_ctx.text_content)
        if text_len > 0:
            _, last_y = self._layout_ctx.offset_to_position(text_len - 1)
            return last_y + self._layout_ctx.line_height
        return self._default_text_origin.y

    def to_absolute(
        self,
        native_x: float,
        native_y: float,
        parent_id: CrdtId | None,
    ) -> tuple[float, float]:
        """Convert native coordinates to absolute document coordinates.

        Convenience method for backward compatibility. Uses get_anchor() and
        AnchorRelativePoint internally.

        Args:
            native_x: X coordinate in native space
            native_y: Y coordinate in native space
            parent_id: Parent layer CrdtId

        Returns:
            Tuple of (absolute_x, absolute_y)
        """
        if is_root_layer(parent_id):
            # Root layer or None: already absolute
            return (native_x, native_y)

        anchor = self.get_anchor(parent_id)
        if anchor is None:
            # Unknown parent - return as-is
            return (native_x, native_y)

        doc_point = AnchorRelativePoint(native_x, native_y).to_document(anchor)
        return (doc_point.x, doc_point.y)
