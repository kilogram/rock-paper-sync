"""Spatial matching utilities for annotation-to-paragraph mapping.

Provides common logic for matching annotations to text paragraphs based on
Y-coordinate proximity when text-based matching isn't available.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def find_nearest_paragraph_by_y(
    annotation_y: float,
    markdown_blocks: list[Any],
    text_origin_y: float | None = None,
) -> int | None:
    """Find the nearest paragraph to an annotation by Y-coordinate.

    Uses Y-position matching to find the closest paragraph. Requires that
    markdown_blocks have `page_y_start` attribute set (see issue #5 for
    pagination metadata persistence implementation).

    Args:
        annotation_y: Y-coordinate of annotation (in absolute page coordinates)
        markdown_blocks: List of markdown content blocks
        text_origin_y: Optional text origin Y for coordinate transformation

    Returns:
        Index of nearest paragraph, or None if matching unavailable

    Note:
        All blocks must have valid `page_y_start` attribute. Blocks without
        this attribute will be skipped with a warning.
    """
    # Check if pagination data is available on ALL blocks
    # IMPORTANT: Must validate each block, not just the first one
    if not markdown_blocks:
        logger.debug("No markdown blocks provided for Y-position matching")
        return None

    if not hasattr(markdown_blocks[0], "page_y_start") or markdown_blocks[0].page_y_start is None:
        logger.debug(
            "Y-position matching unavailable: ContentBlock missing page_y_start attribute (see issue #5)"
        )
        return None

    # Find closest paragraph by Y position
    min_distance = float("inf")
    nearest_index = None

    for idx, md_block in enumerate(markdown_blocks):
        # Validate EACH block has page_y_start, not just the first
        if not hasattr(md_block, "page_y_start") or md_block.page_y_start is None:
            logger.warning(f"Y-position matching: Block {idx} missing page_y_start, skipping")
            continue

        block_y = md_block.page_y_start
        distance = abs(annotation_y - block_y)
        if distance < min_distance:
            min_distance = distance
            nearest_index = idx

    if nearest_index is not None:
        logger.debug(
            f"Y-position match: y={annotation_y:.1f} → paragraph {nearest_index} "
            f"(distance={min_distance:.1f})"
        )
    else:
        logger.warning("Could not find valid paragraph with page_y_start")

    return nearest_index
