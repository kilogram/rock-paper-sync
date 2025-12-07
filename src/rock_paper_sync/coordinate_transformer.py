"""Coordinate transformation utilities for reMarkable documents.

This module handles coordinate space transformations between:
- Native (text-relative) coordinates used in .rm files
- Absolute page coordinates

reMarkable v6 files use multiple coordinate spaces:
- Absolute: Items parented to root layer (CrdtId(0, 11))
- Text-relative: Items parented to text layers (e.g., CrdtId(2, 1316))

See docs/STROKE_ANCHORING.md for detailed documentation.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rmscene.tagged_block_common import CrdtId

if TYPE_CHECKING:
    from .layout import LayoutContext

# Import from device geometry (single source of truth)
from .layout.device import DEFAULT_DEVICE

# Aliases for backward compatibility - derived from default device geometry
DEFAULT_TEXT_ORIGIN_X = DEFAULT_DEVICE.text_pos_x
DEFAULT_TEXT_ORIGIN_Y = DEFAULT_DEVICE.text_pos_y
DEFAULT_TEXT_WIDTH = DEFAULT_DEVICE.text_width
NEGATIVE_Y_OFFSET = DEFAULT_DEVICE.negative_y_offset
ROOT_LAYER_ID = DEFAULT_DEVICE.root_layer_id

logger = logging.getLogger("rock_paper_sync.coordinate_transformer")


@dataclass
class TextOrigin:
    """Text origin coordinates from RootTextBlock."""

    x: float
    y: float
    width: float = DEFAULT_TEXT_WIDTH


@dataclass
class AnchorOrigin:
    """Anchor origin for a parent layer."""

    x: float
    y: float


@dataclass
class ParentAnchor:
    """Per-parent anchor position for coordinate transformation.

    Each stroke group (parent_id) has its own anchor position determined by:
    - anchor_x: X coordinate offset from TreeNodeBlock.anchor_origin_x
    - anchor_y: Y coordinate from anchor_id character offset resolution

    Attributes:
        anchor_x: X coordinate offset for this parent
        anchor_y: Y coordinate for this parent (from text character position)
        char_offset: Original character offset (for debugging/logging), None if unknown
    """

    anchor_x: float
    anchor_y: float
    char_offset: int | None = None


# End-of-document marker in anchor_id (0xFFFFFFFFFFFF)
# When anchor_id.part2 equals this value, the anchor is at end of document
END_OF_DOC_ANCHOR_MARKER = 281474976710655


def is_root_layer(parent_id: "CrdtId") -> bool:
    """Check if parent_id represents the root layer (absolute coordinates).

    Args:
        parent_id: Parent layer CrdtId

    Returns:
        True if this is the root layer
    """
    from rmscene.tagged_block_common import CrdtId

    return parent_id == CrdtId(ROOT_LAYER_ID[0], ROOT_LAYER_ID[1])


def is_text_relative(parent_id: "CrdtId | None") -> bool:
    """Check if parent_id uses text-relative coordinates.

    Args:
        parent_id: Parent layer CrdtId or None

    Returns:
        True if coordinates are text-relative
    """
    if parent_id is None:
        return False
    return not is_root_layer(parent_id)


class CoordinateTransformer:
    """Transforms coordinates between native and absolute space.

    Usage:
        transformer = CoordinateTransformer(text_origin_x=-375, text_origin_y=94)

        # Transform a single point
        abs_x, abs_y = transformer.to_absolute(
            native_x=10,
            native_y=-50,
            parent_id=CrdtId(2, 530),
            anchor_x=100,
        )

        # Transform annotation bounding box
        abs_bbox = transformer.transform_bbox(bbox, parent_id, anchor_x)
    """

    def __init__(
        self,
        text_origin_x: float = DEFAULT_TEXT_ORIGIN_X,
        text_origin_y: float = DEFAULT_TEXT_ORIGIN_Y,
    ) -> None:
        """Initialize transformer with text origin.

        Args:
            text_origin_x: X coordinate of text origin (from RootTextBlock.pos_x)
            text_origin_y: Y coordinate of text origin (from RootTextBlock.pos_y)
        """
        self.text_origin_x = text_origin_x
        self.text_origin_y = text_origin_y

    def to_absolute(
        self,
        native_x: float,
        native_y: float,
        parent_id: "CrdtId | None",
        anchor_x: float | None = None,
        stroke_center_y: float | None = None,
    ) -> tuple[float, float]:
        """Transform native coordinates to absolute page coordinates.

        Args:
            native_x: X coordinate in native space
            native_y: Y coordinate in native space
            parent_id: Parent layer CrdtId
            anchor_x: Per-parent X anchor offset (from TreeNodeBlock)
            stroke_center_y: Y center of the stroke (for offset calculation)

        Returns:
            Tuple of (absolute_x, absolute_y)
        """
        # If root layer, coordinates are already absolute
        if parent_id is None or is_root_layer(parent_id):
            return native_x, native_y

        # Calculate X offset
        x_offset = anchor_x if anchor_x is not None else self.text_origin_x
        absolute_x = native_x + x_offset

        # Calculate Y offset based on coordinate space
        # Positive Y: relative to text origin (top of text area)
        # Negative Y: relative to baseline + line height
        if stroke_center_y is not None and stroke_center_y < 0:
            y_offset = NEGATIVE_Y_OFFSET
        else:
            y_offset = 0

        absolute_y = self.text_origin_y + y_offset + native_y

        return absolute_x, absolute_y

    def transform_point(
        self,
        point: "Point",
        parent_id: "CrdtId | None",
        anchor_x: float | None = None,
        stroke_center_y: float | None = None,
    ) -> "Point":
        """Transform a Point to absolute coordinates.

        Args:
            point: Point object with x, y attributes
            parent_id: Parent layer CrdtId
            anchor_x: Per-parent X anchor offset
            stroke_center_y: Y center of the stroke

        Returns:
            New Point with absolute coordinates
        """
        from .annotations import Point

        abs_x, abs_y = self.to_absolute(point.x, point.y, parent_id, anchor_x, stroke_center_y)
        return Point(x=abs_x, y=abs_y)

    def transform_bbox(
        self,
        bbox: "Rectangle",
        parent_id: "CrdtId | None",
        anchor_x: float | None = None,
    ) -> "Rectangle":
        """Transform bounding box to absolute coordinates.

        Args:
            bbox: Rectangle bounding box
            parent_id: Parent layer CrdtId
            anchor_x: Per-parent X anchor offset

        Returns:
            New Rectangle with absolute coordinates
        """
        from .annotations import Rectangle

        # Calculate stroke center for offset determination
        stroke_center_y = bbox.y + bbox.h / 2

        # Transform top-left corner
        abs_x, abs_y = self.to_absolute(bbox.x, bbox.y, parent_id, anchor_x, stroke_center_y)

        return Rectangle(x=abs_x, y=abs_y, w=bbox.w, h=bbox.h)


def extract_text_origin(rm_file: Path) -> TextOrigin:
    """Extract text origin coordinates from .rm file.

    Args:
        rm_file: Path to .rm file

    Returns:
        TextOrigin with coordinates from RootTextBlock
    """
    import rmscene
    from rmscene.scene_stream import RootTextBlock

    try:
        with rm_file.open("rb") as f:
            blocks = list(rmscene.read_blocks(f))

        for block in blocks:
            if isinstance(block, RootTextBlock):
                return TextOrigin(
                    x=block.value.pos_x,
                    y=block.value.pos_y,
                    width=block.value.width,
                )

    except Exception as e:
        logger.warning(f"Failed to extract text origin from {rm_file}: {e}")

    return TextOrigin(
        x=DEFAULT_TEXT_ORIGIN_X,
        y=DEFAULT_TEXT_ORIGIN_Y,
        width=DEFAULT_TEXT_WIDTH,
    )


def build_parent_anchor_map(rm_file: Path) -> dict["CrdtId", AnchorOrigin]:
    """Build mapping of parent_ids to anchor origins.

    Each TreeNodeBlock (parent layer) has:
    - anchor_id: pointing to a specific text character
    - anchor_origin_x: X offset for this parent's coordinate space

    Args:
        rm_file: Path to .rm file

    Returns:
        Dictionary mapping parent_id to AnchorOrigin
    """
    import rmscene
    from rmscene.scene_stream import TreeNodeBlock

    from .annotations.common.text_extraction import extract_text_blocks_from_rm

    parent_to_anchor: dict[CrdtId, AnchorOrigin] = {}

    try:
        # Extract TreeNodeBlocks
        with rm_file.open("rb") as f:
            blocks = list(rmscene.read_blocks(f))

        tree_node_blocks = [b for b in blocks if isinstance(b, TreeNodeBlock)]

        # Build parent_id → anchor_id and anchor_origin_x mappings
        parent_to_anchor_id = {}
        parent_to_anchor_x = {}

        for tnb in tree_node_blocks:
            if not hasattr(tnb, "group"):
                continue

            node_id = tnb.group.node_id
            anchor_id = tnb.group.anchor_id
            anchor_origin_x = tnb.group.anchor_origin_x

            # Extract anchor_id from LwwValue wrapper
            if anchor_id and hasattr(anchor_id, "value"):
                anchor_crdt_id = anchor_id.value
                if anchor_crdt_id:
                    parent_to_anchor_id[node_id] = anchor_crdt_id

            # Extract anchor_origin_x from LwwValue wrapper
            if anchor_origin_x and hasattr(anchor_origin_x, "value"):
                anchor_x_value = anchor_origin_x.value
                if anchor_x_value is not None:
                    parent_to_anchor_x[node_id] = anchor_x_value

        # Extract text blocks for Y baseline mapping
        rm_text_blocks, _ = extract_text_blocks_from_rm(rm_file)

        # Build character index → text block mapping
        char_to_block = {}
        char_index = 0
        for block_idx, block in enumerate(rm_text_blocks):
            block_char_count = len(block.content)
            for i in range(block_char_count):
                char_to_block[char_index + i] = block_idx
            char_index += block_char_count

        # Map parent_ids to AnchorOrigin
        for parent_id, anchor_id in parent_to_anchor_id.items():
            char_idx = anchor_id.part2 if hasattr(anchor_id, "part2") else anchor_id

            # Find Y baseline from text block
            baseline_y = None
            if char_idx in char_to_block:
                block_idx = char_to_block[char_idx]
                text_block = rm_text_blocks[block_idx]
                baseline_y = text_block.y_start

            # Get anchor_origin_x
            anchor_x = parent_to_anchor_x.get(parent_id, 0.0)

            if baseline_y is not None:
                parent_to_anchor[parent_id] = AnchorOrigin(x=anchor_x, y=baseline_y)

    except Exception as e:
        logger.warning(f"Failed to build parent anchor map from {rm_file}: {e}")

    return parent_to_anchor


class ParentAnchorResolver:
    """Resolves per-parent anchor positions from TreeNodeBlocks.

    Each stroke group has a parent_id that maps to a TreeNodeBlock containing:
    - anchor_origin_x: X offset for this parent's coordinate space
    - anchor_id: CrdtId where part2 is a character offset determining Y position

    This class extracts these mappings and resolves character offsets to
    Y positions using a LayoutContext.

    Example:
        resolver = ParentAnchorResolver.from_rm_file(rm_path)
        anchor = resolver.get_anchor(parent_id)
        abs_x = anchor.anchor_x + native_x
        abs_y = anchor.anchor_y + native_y

    Or use the convenience method:
        abs_x, abs_y = resolver.to_absolute(native_x, native_y, parent_id)
    """

    def __init__(
        self,
        parent_to_anchor_x: dict["CrdtId", float],
        parent_to_char_offset: dict["CrdtId", int],
        layout_ctx: "LayoutContext",
        default_origin: TextOrigin,
    ):
        """Initialize resolver with extracted anchor data.

        Use factory methods from_rm_file() or from_blocks() instead of
        calling this directly.

        Args:
            parent_to_anchor_x: Map from parent_id to X anchor offset
            parent_to_char_offset: Map from parent_id to character offset
            layout_ctx: Layout context for character offset to Y resolution
            default_origin: Default text origin for unknown parent_ids
        """
        self._parent_to_anchor_x = parent_to_anchor_x
        self._parent_to_char_offset = parent_to_char_offset
        self._layout_ctx = layout_ctx
        self._default_origin = default_origin
        self._cache: dict[CrdtId, ParentAnchor] = {}

    @property
    def layout_context(self) -> "LayoutContext":
        """Get the layout context used for Y position resolution."""
        return self._layout_ctx

    @property
    def text_content(self) -> str:
        """Get the text content from the layout context."""
        return self._layout_ctx.text_content

    @classmethod
    def from_rm_file(cls, rm_path: "Path") -> "ParentAnchorResolver":
        """Create resolver from .rm file.

        Reads the file, extracts TreeNodeBlocks for anchor mappings,
        and creates a LayoutContext from the RootTextBlock.

        Args:
            rm_path: Path to .rm file

        Returns:
            ParentAnchorResolver ready for anchor lookups
        """
        import rmscene

        with open(rm_path, "rb") as f:
            blocks = list(rmscene.read_blocks(f))

        return cls.from_blocks(blocks)

    @classmethod
    def from_blocks(cls, blocks: list) -> "ParentAnchorResolver":
        """Create resolver from pre-read rmscene blocks.

        Use this when you already have blocks from rmscene.read_blocks()
        to avoid re-reading the file.

        Args:
            blocks: List of rmscene blocks from read_blocks()

        Returns:
            ParentAnchorResolver ready for anchor lookups
        """
        from .layout import LayoutContext, TextAreaConfig

        # Extract text content and origin from RootTextBlock
        full_text = ""
        text_pos_x = DEFAULT_TEXT_ORIGIN_X
        text_pos_y = DEFAULT_TEXT_ORIGIN_Y

        for block in blocks:
            if "RootText" in type(block).__name__:
                text_data = block.value
                text_pos_x = text_data.pos_x
                text_pos_y = text_data.pos_y

                # Extract text from CrdtSequence
                text_parts = []
                for item in text_data.items.sequence_items():
                    if hasattr(item, "value") and isinstance(item.value, str):
                        text_parts.append(item.value)
                full_text = "".join(text_parts)
                break

        # Create layout context for Y position resolution
        layout_ctx = LayoutContext.from_text(
            full_text,
            use_font_metrics=True,
            config=TextAreaConfig(text_pos_x=text_pos_x, text_pos_y=text_pos_y),
        )

        default_origin = TextOrigin(x=text_pos_x, y=text_pos_y)

        # Extract per-parent anchor mappings from TreeNodeBlocks
        parent_to_anchor_x: dict[CrdtId, float] = {}
        parent_to_char_offset: dict[CrdtId, int] = {}

        for block in blocks:
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

                    # Get anchor_id (character offset)
                    if hasattr(g, "anchor_id") and g.anchor_id and g.anchor_id.value:
                        anchor_id = g.anchor_id.value
                        if hasattr(anchor_id, "part2"):
                            parent_to_char_offset[node_id] = anchor_id.part2

        return cls(parent_to_anchor_x, parent_to_char_offset, layout_ctx, default_origin)

    def get_anchor(self, parent_id: "CrdtId | None") -> ParentAnchor:
        """Get anchor position for a parent_id.

        Returns default origin if parent_id is unknown or is root layer.
        Uses caching for efficiency.

        Args:
            parent_id: Parent layer CrdtId

        Returns:
            ParentAnchor with resolved anchor_x and anchor_y
        """
        # Handle None or root layer
        if parent_id is None or is_root_layer(parent_id):
            return ParentAnchor(0.0, 0.0, None)

        # Check cache
        if parent_id in self._cache:
            return self._cache[parent_id]

        # Resolve anchor_x
        anchor_x = self._parent_to_anchor_x.get(parent_id, self._default_origin.x)

        # Resolve anchor_y from character offset
        char_offset = self._parent_to_char_offset.get(parent_id)

        if char_offset is None:
            # No anchor_id for this parent - use default
            anchor_y = self._default_origin.y
        elif char_offset == END_OF_DOC_ANCHOR_MARKER:
            # End of document marker - position after last character
            text_len = len(self._layout_ctx.text_content)
            if text_len > 0:
                _, last_y = self._layout_ctx.offset_to_position(text_len - 1)
                anchor_y = last_y + self._layout_ctx.line_height
            else:
                anchor_y = self._default_origin.y
        elif char_offset < len(self._layout_ctx.text_content):
            # Normal case - resolve character offset to Y position
            _, anchor_y = self._layout_ctx.offset_to_position(char_offset)
        else:
            # Character offset out of bounds - use default
            anchor_y = self._default_origin.y
            char_offset = None  # Mark as invalid for debugging

        anchor = ParentAnchor(anchor_x, anchor_y, char_offset)
        self._cache[parent_id] = anchor
        return anchor

    def to_absolute(
        self,
        native_x: float,
        native_y: float,
        parent_id: "CrdtId | None",
    ) -> tuple[float, float]:
        """Transform native coordinates to absolute using per-parent anchors.

        For root layer items (absolute coordinate space), returns coordinates unchanged.
        For text-relative items, adds the per-parent anchor offset.

        Args:
            native_x: X coordinate in native space
            native_y: Y coordinate in native space
            parent_id: Parent layer CrdtId

        Returns:
            (absolute_x, absolute_y) tuple
        """
        if parent_id is None or is_root_layer(parent_id):
            return native_x, native_y

        anchor = self.get_anchor(parent_id)
        return anchor.anchor_x + native_x, anchor.anchor_y + native_y

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
        return self._default_origin.y


def get_annotation_center_y(block, text_origin_y: float = DEFAULT_TEXT_ORIGIN_Y) -> float | None:
    """Extract center Y coordinate from annotation block in absolute coordinates.

    Handles both Line (strokes) and Glyph (highlights) blocks.

    Args:
        block: rmscene annotation block
        text_origin_y: Text origin Y for text-relative coordinate transformation

    Returns:
        Center Y in absolute coordinates, or None if cannot determine
    """

    try:
        if not hasattr(block, "item") or not hasattr(block.item, "value"):
            return None

        value = block.item.value

        # Determine coordinate space from parent_id
        is_text_rel = False
        if hasattr(block, "parent_id"):
            parent_id = block.parent_id
            is_text_rel = is_text_relative(parent_id)

        # Extract native Y coordinate
        native_y = None

        # For Line blocks (strokes)
        if "Line" in type(value).__name__:
            if hasattr(value, "points") and value.points:
                ys = [p.y for p in value.points if hasattr(p, "y")]
                if ys:
                    native_y = sum(ys) / len(ys)

        # For Glyph blocks (highlights)
        if "Glyph" in type(value).__name__:
            if hasattr(value, "rectangles") and value.rectangles:
                ys = [r.y + r.h / 2 for r in value.rectangles if hasattr(r, "y")]
                if ys:
                    native_y = sum(ys) / len(ys)

        if native_y is None:
            return None

        # Transform to absolute if needed
        if is_text_rel:
            absolute_y = text_origin_y + native_y
            logger.debug(
                f"Transformed text-relative y={native_y:.1f} to absolute y={absolute_y:.1f}"
            )
            return absolute_y
        else:
            return native_y

    except Exception as e:
        logger.warning(f"Failed to get annotation center Y: {e}")
        return None


def apply_y_offset_to_block(block, y_offset: float) -> None:
    """Apply Y offset to all coordinates in annotation block.

    Modifies the block in place.

    Args:
        block: rmscene annotation block
        y_offset: Y offset to apply (positive = move down)
    """
    try:
        if not hasattr(block, "item") or not hasattr(block.item, "value"):
            return

        value = block.item.value

        # For Line blocks (strokes)
        if "Line" in type(value).__name__:
            if hasattr(value, "points") and value.points:
                for point in value.points:
                    if hasattr(point, "y"):
                        point.y += y_offset

        # For Glyph blocks (highlights)
        if "Glyph" in type(value).__name__:
            if hasattr(value, "rectangles") and value.rectangles:
                for rect in value.rectangles:
                    if hasattr(rect, "y"):
                        rect.y += y_offset

    except Exception as e:
        logger.warning(f"Failed to apply Y offset to annotation block: {e}")


# Import Point/Rectangle at end to avoid circular import
from .annotations import Point, Rectangle  # noqa: E402, F401
