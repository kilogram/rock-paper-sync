"""Shared text matching utilities for OCR paragraph mapping.

This module provides text similarity functions for matching reMarkable text blocks
to markdown paragraphs. Used by both annotation_mapper and paragraph_mapper to
avoid code duplication.
"""

import logging

from rock_paper_sync.annotation_mapper import RmTextBlock
from rock_paper_sync.parser import BlockType, ContentBlock

logger = logging.getLogger(__name__)

# Minimum text length for prefix matching
MIN_TEXT_LENGTH_FOR_PREFIX_MATCH = 20


def match_rm_block_to_markdown(
    rm_block: RmTextBlock,
    markdown_blocks: list[ContentBlock],
    fuzzy_threshold: float = 0.8,
) -> int | None:
    """Match an rm text block to a markdown paragraph by content similarity.

    Uses substring matching and prefix matching for fuzzy alignment.
    Can be enhanced with Levenshtein distance in the future.

    Args:
        rm_block: Text block extracted from .rm file
        markdown_blocks: Parsed markdown content blocks
        fuzzy_threshold: Minimum similarity for fuzzy matching (0-1), currently unused

    Returns:
        Index of best matching markdown block, or None if no match
    """
    rm_text = rm_block.content.strip().lower()

    for i, md_block in enumerate(markdown_blocks):
        # Only match text-bearing blocks
        if md_block.type not in (
            BlockType.PARAGRAPH,
            BlockType.HEADER,
            BlockType.LIST_ITEM,
            BlockType.BLOCKQUOTE,
        ):
            continue

        md_text = md_block.text.strip().lower()

        # Substring matching - handles line wrapping differences
        if md_text in rm_text or rm_text in md_text:
            return i

        # Prefix matching for longer blocks
        if len(md_text) > MIN_TEXT_LENGTH_FOR_PREFIX_MATCH and len(rm_text) > MIN_TEXT_LENGTH_FOR_PREFIX_MATCH:
            if md_text[:MIN_TEXT_LENGTH_FOR_PREFIX_MATCH] == rm_text[:MIN_TEXT_LENGTH_FOR_PREFIX_MATCH]:
                return i

    return None
