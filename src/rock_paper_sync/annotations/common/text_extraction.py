"""Shared utilities for text extraction from .rm files.

This module provides utilities for extracting text blocks and positions
from reMarkable v6 .rm files. These utilities are used by annotation
handlers to map annotations to markdown paragraphs.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import rmscene

# Import constants from single source of truth
from rock_paper_sync.layout.constants import (
    RM_TEXT_BLOCK_LINE_HEIGHT,
    TEXT_POS_Y,
)

logger = logging.getLogger(__name__)


@dataclass
class RmTextBlock:
    """Text block extracted from .rm file with position information."""

    content: str
    y_start: float
    y_end: float


def extract_text_blocks_from_rm(rm_file_path: Path) -> tuple[list[RmTextBlock], float]:
    """Extract text blocks and origin Y from .rm file.

    Args:
        rm_file_path: Path to .rm file

    Returns:
        Tuple of (text_blocks, text_origin_y)
        - text_blocks: List of text blocks with Y-positions
        - text_origin_y: Y-coordinate of text origin (for coordinate transforms)
    """
    try:
        with open(rm_file_path, "rb") as f:
            blocks = list(rmscene.read_blocks(f))

        text_blocks = []
        text_origin_y = TEXT_POS_Y

        # Find RootTextBlock
        for block in blocks:
            if "RootText" in type(block).__name__:
                text_data = block.value
                text_origin_y = text_data.pos_y

                # Extract text content
                text_parts = []
                for item in text_data.items.sequence_items():
                    if hasattr(item, "value") and isinstance(item.value, str):
                        text_parts.append(item.value)

                full_text = "".join(text_parts)

                # Split into lines and create separate blocks
                # Each line becomes a separate text block with estimated Y-position
                if full_text:
                    lines = full_text.split("\n")
                    current_y = text_origin_y

                    line_height = RM_TEXT_BLOCK_LINE_HEIGHT

                    for line in lines:
                        line_text = line.strip()
                        if line_text:  # Skip empty lines
                            # Each line gets its own text block with calculated Y range
                            y_start = current_y
                            y_end = current_y + line_height

                            text_blocks.append(
                                RmTextBlock(content=line_text, y_start=y_start, y_end=y_end)
                            )

                        # Move to next line position
                        current_y += line_height

                break

        return text_blocks, text_origin_y

    except Exception as e:
        logger.warning(f"Failed to extract text blocks from {rm_file_path}: {e}")
        return [], TEXT_POS_Y
