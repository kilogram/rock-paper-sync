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

if TYPE_CHECKING:
    from rmscene.tagged_block_common import CrdtId

logger = logging.getLogger("rock_paper_sync.coordinate_transformer")

# Typography-based constants for coordinate transformation
# See docs/STROKE_ANCHORING.md for derivation
NEGATIVE_Y_OFFSET = 60  # LINE_HEIGHT (35) + BASELINE_OFFSET (25)
ROOT_LAYER_ID = (0, 11)  # CrdtId for root layer (absolute coordinates)

# Standard text positioning
DEFAULT_TEXT_ORIGIN_X = -375.0
DEFAULT_TEXT_ORIGIN_Y = 94.0
DEFAULT_TEXT_WIDTH = 750.0


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
