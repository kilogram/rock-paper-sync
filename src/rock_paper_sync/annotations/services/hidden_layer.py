"""HiddenLayerManager - preservation of orphaned annotations in hidden .rm layers.

Orphaned annotations are those that could not be re-anchored after content changes.
Rather than losing them permanently, M5.5 serialises the original rmscene blocks into
the database (via pull_sync._record_orphans) and re-emits them on a hidden PRESERVATION
layer during the next push.

Architecture
------------
- pull_sync: calls serialize_annotation_blocks() when recording orphans → stores in DB
- generator: calls HiddenLayerManager.build_preservation_layer() with blobs from DB
  → appends LayerPlan(PRESERVATION, visible=False) to PageTransformPlan.layers
- executor: emits SceneTreeBlock + TreeNodeBlock(visible=False) + SceneGroupItemBlock
  for the preservation layer, then appends the PreserveUnknown blocks verbatim

Re-parenting
------------
Orphaned blocks have parent_id == CONTENT_LAYER_ID (CrdtId(0,11)) from their original
layer.  To put them on the preservation layer (CrdtId(0,21)) we replace parent_id on
any block where it equals the content layer id.  Specifically:
  - SceneGlyphItemBlock.parent_id  (highlights)
  - SceneGroupItemBlock.parent_id  (stroke bundle's layer link)
  - SceneTreeBlock.parent_id       (stroke bundle's subtree declaration)
TreeNodeBlock and SceneLineItemBlock do NOT reference the layer directly.
"""

from __future__ import annotations

import io
import logging
from dataclasses import replace as dc_replace
from typing import Any

import rmscene

from rock_paper_sync.annotations.domain.intents import (
    LayerPlan,
    LayerType,
    PreserveUnknown,
)
from rock_paper_sync.annotations.scene_adapter.scene_index import (
    SYSTEM_LAYER_1,
    SYSTEM_LAYER_2,
)

logger = logging.getLogger(__name__)

# The PRESERVATION layer always occupies layer index 1 (CrdtId(0,21)).
PRESERVATION_LAYER_ID = SYSTEM_LAYER_2
CONTENT_LAYER_ID = SYSTEM_LAYER_1

PRESERVATION_LAYER_LABEL = "Rock Paper Sync \u2014 Orphans"


# =============================================================================
# Block serialisation helpers
# =============================================================================


def serialize_annotation_blocks(annotation: Any) -> bytes | None:
    """Serialise the rmscene blocks of a DocumentAnnotation to bytes for DB storage.

    Args:
        annotation: DocumentAnnotation with original_rm_block (and optional
                    original_tree_node, original_scene_group_item,
                    original_scene_tree_block for strokes).

    Returns:
        Serialised bytes, or None if no blocks are available.
    """
    blocks = _collect_annotation_blocks(annotation)
    if not blocks:
        return None
    buf = io.BytesIO()
    try:
        rmscene.write_blocks(buf, blocks)
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"Failed to serialise annotation blocks for {annotation.annotation_id}: {e}")
        return None


def _collect_annotation_blocks(annotation: Any) -> list[Any]:
    """Collect the relevant rmscene blocks from a DocumentAnnotation."""
    blocks: list[Any] = []
    if annotation.annotation_type == "stroke":
        # Stroke bundle: SceneTreeBlock, TreeNodeBlock, SceneGroupItemBlock, SceneLineItemBlock
        if annotation.original_scene_tree_block is not None:
            blocks.append(annotation.original_scene_tree_block)
        if annotation.original_tree_node is not None:
            blocks.append(annotation.original_tree_node)
        if annotation.original_scene_group_item is not None:
            blocks.append(annotation.original_scene_group_item)
        if annotation.original_rm_block is not None:
            blocks.append(annotation.original_rm_block)
    else:
        # Highlight: just the SceneGlyphItemBlock
        if annotation.original_rm_block is not None:
            blocks.append(annotation.original_rm_block)
    return blocks


def deserialize_annotation_blocks(blob: bytes) -> list[Any]:
    """Deserialise rmscene blocks from a stored blob.

    Args:
        blob: Bytes produced by serialize_annotation_blocks().

    Returns:
        List of rmscene block objects.
    """
    return list(rmscene.read_blocks(io.BytesIO(blob)))


# =============================================================================
# Re-parenting
# =============================================================================


def reparent_blocks_to_preservation(blocks: list[Any]) -> list[Any]:
    """Re-parent annotation blocks from the content layer to the preservation layer.

    Replaces parent_id == CONTENT_LAYER_ID with PRESERVATION_LAYER_ID on any
    block that references the layer directly.  Blocks whose parent_id points
    elsewhere (e.g. SceneLineItemBlock → stroke node id) are left unchanged.

    Args:
        blocks: Blocks from deserialize_annotation_blocks().

    Returns:
        New list with updated parent_id values.
    """
    result = []
    for block in blocks:
        if hasattr(block, "parent_id") and block.parent_id == CONTENT_LAYER_ID:
            result.append(dc_replace(block, parent_id=PRESERVATION_LAYER_ID))
        else:
            result.append(block)
    return result


# =============================================================================
# HiddenLayerManager
# =============================================================================


class HiddenLayerManager:
    """Builds a hidden PRESERVATION LayerPlan from serialised orphan blobs.

    Usage::

        manager = HiddenLayerManager()
        layer = manager.build_preservation_layer(blobs)
        if layer:
            plan.layers.append(layer)
    """

    def build_preservation_layer(
        self,
        orphan_blobs: list[bytes],
    ) -> LayerPlan | None:
        """Build a hidden LayerPlan from a list of serialised annotation blobs.

        Each blob is deserialised, re-parented to PRESERVATION_LAYER_ID, and
        wrapped in a PreserveUnknown intent.  If no valid blocks are produced
        (e.g. all blobs fail to deserialise), returns None.

        Args:
            orphan_blobs: List of blobs from state.get_orphan_blobs_for_document().

        Returns:
            LayerPlan(PRESERVATION, visible=False) or None if nothing to preserve.
        """
        if not orphan_blobs:
            return None

        unknown_blocks: list[PreserveUnknown] = []
        for blob in orphan_blobs:
            try:
                raw_blocks = deserialize_annotation_blocks(blob)
                reparented = reparent_blocks_to_preservation(raw_blocks)
                for block in reparented:
                    unknown_blocks.append(PreserveUnknown(opaque_handle=block))
            except Exception as e:
                logger.warning(f"Failed to deserialise orphan blob ({len(blob)} bytes): {e}")

        if not unknown_blocks:
            return None

        return LayerPlan(
            layer_type=LayerType.PRESERVATION,
            visible=False,
            label=PRESERVATION_LAYER_LABEL,
            unknown_blocks=unknown_blocks,
        )
