"""Centralized annotation relocation service.

This module provides a single point for all coordinate transformation logic
during annotation relocation. By centralizing this logic, we prevent the class
of bugs where a handler forgets to transform coordinates.

Design principles:
- Handlers declare a RelocationStrategy, they don't implement relocation
- All transformation logic lives here - single source of truth
- Explicit strategies (ABSOLUTE is explicit no-op, not silent)
- Reuses existing infrastructure (ParentAnchorResolver, CoordinateTransformer, etc.)

Usage:
    service = RelocationService()
    context = RelocationContext(
        old_text=old_text,
        new_text=new_text,
        old_origin=(x, y),
        new_origin=(x, y),
        layout_engine=engine,
        geometry=geometry,
    )
    adjusted_block = service.relocate_block(block, strategy, context)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rmscene.tagged_block_common import CrdtId

    from rock_paper_sync.layout import DeviceGeometry, WordWrapLayoutEngine

logger = logging.getLogger(__name__)


class RelocationStrategy(Enum):
    """How to relocate an annotation when content changes.

    Each strategy represents a different coordinate transformation approach:

    CONTENT_DELTA:
        For highlights - uses text anchor matching + layout engine delta.
        Finds where highlighted text was, finds where it is now, calculates
        pixel delta, applies to rectangles.

    ANCHOR_DELTA:
        For strokes - uses TreeNodeBlock anchor offset + Y adjustment.
        Resolves anchor in old text, finds in new text, applies Y delta
        to stroke points.

    ABSOLUTE:
        Explicit no-op for root-layer items that use absolute coordinates.
        Unlike a silent pass-through, this is a declared strategy.
    """

    CONTENT_DELTA = auto()  # Text anchor + layout delta (highlights)
    ANCHOR_DELTA = auto()  # TreeNodeBlock anchor + Y shift (strokes)
    ABSOLUTE = auto()  # No transformation (root layer items)


@dataclass
class RelocationContext:
    """All context needed for annotation relocation.

    Bundles the parameters needed for coordinate transformation,
    making the API clean and easy to test.
    """

    old_text: str
    new_text: str
    old_origin: tuple[float, float]
    new_origin: tuple[float, float]
    layout_engine: WordWrapLayoutEngine
    geometry: DeviceGeometry
    crdt_base_id: int | None = None

    # Optional: source .rm file path for extracting anchor information
    source_rm_path: Any | None = None

    # Optional: parent anchor map for stroke relocation
    parent_anchor_map: dict[CrdtId, Any] | None = None


class RelocationService:
    """Centralized coordinate transformation for annotation relocation.

    This service owns all relocation logic. Handlers declare which
    RelocationStrategy they use, but the actual transformation happens here.

    This prevents the class of bugs where a handler implements relocate()
    as a silent pass-through (like the old StrokeHandler.relocate()).
    """

    def relocate_block(
        self,
        block: Any,
        strategy: RelocationStrategy,
        context: RelocationContext,
    ) -> Any:
        """Relocate annotation block using the specified strategy.

        Args:
            block: Raw rmscene annotation block (SceneGlyphItemBlock or SceneLineItemBlock)
            strategy: Which relocation strategy to use
            context: All context needed for relocation

        Returns:
            Modified block with adjusted coordinates/anchors

        Raises:
            ValueError: If strategy is unknown
        """
        if strategy == RelocationStrategy.ABSOLUTE:
            logger.debug("RelocationStrategy.ABSOLUTE: no transformation needed")
            return block

        elif strategy == RelocationStrategy.CONTENT_DELTA:
            return self._relocate_highlight(block, context)

        elif strategy == RelocationStrategy.ANCHOR_DELTA:
            return self._relocate_stroke(block, context)

        else:
            raise ValueError(f"Unknown relocation strategy: {strategy}")

    def _relocate_highlight(
        self,
        block: Any,
        context: RelocationContext,
    ) -> Any:
        """Relocate highlight using content-based anchoring.

        Uses delta-based approach to preserve pixel-perfect rectangle positions:
        1. Find where highlighted text was in old document (anchor)
        2. Resolve where that text is in new document (new_offset)
        3. Calculate position delta using SAME layout model for both
        4. Apply delta to original pixel-perfect rectangles
        5. Update CRDT anchor in extra_value_data for firmware 3.6+
        """
        # Import pure functions from highlight_handler
        from rock_paper_sync.annotations.handlers.highlight_handler import (
            apply_delta_to_rectangles,
            calculate_position_delta,
            extract_glyph_highlight_info,
            find_and_resolve_anchor,
            rebuild_rectangles_for_reflow,
        )
        from rock_paper_sync.generator import update_glyph_extra_value_data

        # Step 1: Extract highlight information from block
        highlight_info = extract_glyph_highlight_info(block)
        if highlight_info is None:
            logger.warning("Could not extract highlight info, keeping original position")
            return block

        highlight_text, rectangles, old_position = highlight_info
        glyph_value = block.item.value

        # Step 2: Find anchor in old text and resolve to new position
        anchor_result = find_and_resolve_anchor(
            highlight_text=highlight_text,
            old_text=context.old_text,
            new_text=context.new_text,
            old_position=old_position,
        )
        if anchor_result is None:
            logger.warning("Could not resolve anchor, keeping original position")
            return block

        old_offset, new_offset, confidence = anchor_result

        # Step 3: Calculate position delta using layout engine
        delta = calculate_position_delta(
            old_offset=old_offset,
            new_offset=new_offset,
            old_text=context.old_text,
            new_text=context.new_text,
            old_origin=context.old_origin,
            new_origin=context.new_origin,
            layout_engine=context.layout_engine,
            text_width=context.geometry.text_width,
        )
        if delta is None:
            logger.warning("Could not calculate position delta, keeping original position")
            return block

        x_delta, y_delta = delta

        # Step 4: Check for reflow (highlight spanning different number of lines)
        old_rect_count = len(rectangles)
        new_end_offset = new_offset + len(highlight_text)
        new_rects = context.layout_engine.calculate_highlight_rectangles(
            new_offset,
            new_end_offset,
            context.new_text,
            context.new_origin,
            context.geometry.text_width,
        )
        new_rect_count = len(new_rects)

        if new_rect_count != old_rect_count:
            # Reflow case: rebuild rectangles for new line structure
            logger.debug(f"  Reflow detected: {old_rect_count} rect(s) → {new_rect_count} rect(s)")
            new_rectangles = rebuild_rectangles_for_reflow(
                original_rectangles=list(rectangles),
                new_rects_from_layout=new_rects,
                delta=delta,
                geometry=context.geometry,
                new_origin=context.new_origin,
            )
            glyph_value.rectangles.clear()
            glyph_value.rectangles.extend(new_rectangles)
        else:
            # Delta case: apply delta to preserve pixel-perfect positions
            apply_delta_to_rectangles(rectangles, delta)

        # Step 5: Update glyph metadata
        glyph_value.start = new_offset

        new_highlighted_text = context.new_text[new_offset : new_offset + len(highlight_text)]
        if new_highlighted_text:
            glyph_value.text = new_highlighted_text
            glyph_value.length = len(new_highlighted_text)

        # Step 6: Update CRDT anchor for firmware 3.6+
        if (
            context.crdt_base_id is not None
            and hasattr(block, "extra_value_data")
            and block.extra_value_data
        ):
            block.extra_value_data = update_glyph_extra_value_data(
                block.extra_value_data,
                new_offset,
                len(highlight_text),
                context.crdt_base_id,
            )

        logger.debug(
            f"Relocated highlight '{highlight_text[:30]}...' by delta=({x_delta:.1f}, {y_delta:.1f}), "
            f"offset={old_offset}->{new_offset}, confidence={confidence:.2f}"
        )

        return block

    def _relocate_stroke(
        self,
        block: Any,
        context: RelocationContext,
    ) -> Any:
        """Pass through stroke blocks without coordinate transformation.

        Stroke coordinates are relative to their TreeNodeBlock anchor. When content
        changes, the TreeNodeBlock anchor is reanchored separately (via the roundtrip
        mechanism through ctx.tree_nodes). The stroke points themselves should remain
        unchanged - they automatically appear in the correct position because they're
        relative to the (updated) anchor.

        This is different from highlights, which have absolute coordinates and need
        explicit delta-based relocation when content shifts.
        """
        logger.debug(
            "Stroke relocation: passing through (anchor reanchoring is handled separately)"
        )
        return block


# Convenience function for common case
def create_relocation_context(
    old_text: str,
    new_text: str,
    old_origin: tuple[float, float],
    new_origin: tuple[float, float],
    layout_engine: WordWrapLayoutEngine,
    geometry: DeviceGeometry,
    crdt_base_id: int | None = None,
) -> RelocationContext:
    """Create a RelocationContext from common parameters.

    This is a convenience wrapper for creating RelocationContext objects.
    """
    return RelocationContext(
        old_text=old_text,
        new_text=new_text,
        old_origin=old_origin,
        new_origin=new_origin,
        layout_engine=layout_engine,
        geometry=geometry,
        crdt_base_id=crdt_base_id,
    )
