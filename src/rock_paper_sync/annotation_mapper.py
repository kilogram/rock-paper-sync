"""Map annotations from .rm files to markdown paragraph indices.

This module provides functionality to detect which paragraphs in a markdown document
have annotations on the reMarkable device, enabling annotation-aware editing workflows.

Architecture:
    [.rm file] → [Extract annotations] → [Map to paragraphs] → [Summary per paragraph]
         ↓              ↓                      ↓                        ↓
    rmscene        Y-positions          Text matching            AnnotationInfo

The mapping process:
1. Extract annotations from .rm file (Y-coordinates)
2. Extract text blocks from .rm file (Y-coordinates + content)
3. Match .rm text blocks to markdown paragraphs (content similarity)
4. Map annotations to matched paragraphs via Y-position overlap
5. Return summary: {paragraph_index: AnnotationInfo}

Example:
    >>> blocks = parse_markdown_file("Document.md").content
    >>> annotation_map = map_annotations_to_paragraphs("Document.rm", blocks)
    >>> print(annotation_map)
    {
        0: AnnotationInfo(highlights=2, strokes=0, notes=0),
        3: AnnotationInfo(highlights=0, strokes=1, notes=0)
    }
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import rmscene
from rmscene.tagged_block_common import CrdtId

from rock_paper_sync.annotations import Annotation, AnnotationType, read_annotations
from rock_paper_sync.parser import BlockType, ContentBlock

logger = logging.getLogger("rock_paper_sync.annotation_mapper")

# Constants from generator.py
TEXT_POS_Y = 94.0  # Default text origin Y-coordinate in .rm files

# Text block extraction constants
# Line height in text-relative coordinates (based on reMarkable RootTextBlock rendering)
# This is different from actual rendered line height - the coordinate space is condensed
RM_TEXT_BLOCK_LINE_HEIGHT = 8.0

# Minimum text length for prefix-based matching heuristic
# Texts shorter than this use full-text comparison instead
MIN_TEXT_LENGTH_FOR_PREFIX_MATCH = 20


@dataclass
class AnnotationInfo:
    """Summary of annotations for a single paragraph.

    Attributes:
        highlights: Count of highlight annotations
        strokes: Count of hand-drawn stroke annotations
        notes: Count of text note annotations (future)
    """

    highlights: int = 0
    strokes: int = 0
    notes: int = 0

    @property
    def total(self) -> int:
        """Total number of annotations."""
        return self.highlights + self.strokes + self.notes

    def __str__(self) -> str:
        """Human-readable summary for markers."""
        parts = []
        if self.highlights:
            parts.append(f"{self.highlights} highlight{'s' if self.highlights != 1 else ''}")
        if self.strokes:
            parts.append(f"{self.strokes} stroke{'s' if self.strokes != 1 else ''}")
        if self.notes:
            parts.append(f"{self.notes} note{'s' if self.notes != 1 else ''}")
        return ", ".join(parts) if parts else "0 annotations"


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
                    lines = full_text.split('\n')
                    current_y = text_origin_y

                    line_height = RM_TEXT_BLOCK_LINE_HEIGHT

                    for line in lines:
                        line_text = line.strip()
                        if line_text:  # Skip empty lines
                            # Each line gets its own text block with calculated Y range
                            y_start = current_y
                            y_end = current_y + line_height

                            text_blocks.append(
                                RmTextBlock(
                                    content=line_text,
                                    y_start=y_start,
                                    y_end=y_end
                                )
                            )

                        # Move to next line position
                        current_y += line_height

                break

        return text_blocks, text_origin_y

    except Exception as e:
        logger.warning(f"Failed to extract text blocks from {rm_file_path}: {e}")
        return [], TEXT_POS_Y


def match_rm_block_to_paragraph(
    rm_block: RmTextBlock, markdown_blocks: list[ContentBlock]
) -> int | None:
    """Match an .rm text block to a markdown paragraph by content similarity.

    Args:
        rm_block: Text block from .rm file
        markdown_blocks: List of markdown content blocks

    Returns:
        Index of best matching markdown block, or None if no good match
    """
    # For now, use simple substring matching
    # In the future, could use fuzzy matching (Levenshtein distance)

    rm_text = rm_block.content.strip().lower()

    for i, md_block in enumerate(markdown_blocks):
        # Only match paragraph-like blocks
        if md_block.type not in (BlockType.PARAGRAPH, BlockType.HEADER, BlockType.LIST_ITEM):
            continue

        md_text = md_block.text.strip().lower()

        # Check if markdown text appears in .rm text or vice versa
        # (.rm text might have line wrapping differences)
        if md_text in rm_text or rm_text in md_text:
            return i

        # Check similarity (simple approach)
        min_len = MIN_TEXT_LENGTH_FOR_PREFIX_MATCH
        if len(md_text) > min_len and len(rm_text) > min_len:
            # If first N chars match, consider it the same block
            if md_text[:min_len] == rm_text[:min_len]:
                return i

    return None


def map_annotations_to_paragraphs(
    rm_file_path: Path | str | BinaryIO, markdown_blocks: list[ContentBlock]
) -> dict[int, AnnotationInfo]:
    """Map annotations from .rm file to markdown paragraph indices.

    This function determines which paragraphs in the markdown document have
    annotations on the reMarkable device.

    Args:
        rm_file_path: Path to .rm file (or file-like object)
        markdown_blocks: List of content blocks from parsed markdown

    Returns:
        Dictionary mapping paragraph index to annotation summary
        Example: {0: AnnotationInfo(highlights=2), 3: AnnotationInfo(strokes=1)}
    """
    # Handle file-like objects vs paths
    if isinstance(rm_file_path, (str, Path)):
        rm_path = Path(rm_file_path)
        if not rm_path.exists():
            logger.warning(f".rm file not found: {rm_path}")
            return {}
    else:
        # File-like object - we can't extract text blocks easily
        # For now, just read annotations
        logger.debug("Reading annotations from file-like object")
        rm_path = None

    # Extract annotations
    try:
        annotations = read_annotations(rm_file_path)
        if not annotations:
            logger.debug("No annotations found in .rm file")
            return {}
    except Exception as e:
        logger.warning(f"Failed to read annotations from .rm file: {e}")
        return {}

    # Extract text blocks from .rm file for position mapping
    if rm_path:
        rm_text_blocks, text_origin_y = extract_text_blocks_from_rm(rm_path)
    else:
        rm_text_blocks, text_origin_y = [], TEXT_POS_Y

    # Build paragraph annotation map
    paragraph_annotations: dict[int, AnnotationInfo] = {}

    if not rm_text_blocks:
        # Fallback: Can't map precisely without text blocks
        # For now, just report that annotations exist somewhere
        logger.warning("Could not extract text blocks - annotations detected but not mapped")
        return {}

    # Map each annotation to a paragraph
    for annotation in annotations:
        # For highlights with text content, use direct text matching (more reliable than Y position)
        if annotation.type == AnnotationType.HIGHLIGHT and annotation.highlight and annotation.highlight.text:
            highlight_text = annotation.highlight.text.strip().lower()

            # Search for this text in markdown blocks
            paragraph_index = None
            for idx, md_block in enumerate(markdown_blocks):
                if highlight_text in md_block.text.lower():
                    paragraph_index = idx
                    logger.debug(
                        f"Matched highlight '{annotation.highlight.text}' to paragraph {idx} by text content"
                    )
                    break

            if paragraph_index is None:
                logger.debug(
                    f"Highlight '{annotation.highlight.text}' not found in any paragraph"
                )
                continue
        else:
            # For strokes (or highlights without text), use Y position matching
            anno_y = annotation.center_y()

            # Transform annotation Y to absolute coordinates if needed
            # Coordinate spaces in reMarkable v6 files:
            # - Absolute: Items parented to root layer (CrdtId(0, 11))
            # - Text-relative: Items parented to text layers (e.g., CrdtId(2, 1316))
            #   These use coordinates relative to RootTextBlock.pos_y
            if annotation.parent_id and annotation.parent_id != CrdtId(0, 11):
                # Text-relative coordinates - transform to absolute
                anno_y_absolute = text_origin_y + anno_y
                logger.debug(
                    f"Transformed text-relative y={anno_y:.1f} to absolute y={anno_y_absolute:.1f} "
                    f"(text_origin_y={text_origin_y:.1f}, parent_id={annotation.parent_id})"
                )
            else:
                # Already in absolute coordinates
                anno_y_absolute = anno_y
                logger.debug(
                    f"Annotation already in absolute coordinates: y={anno_y:.1f} (parent_id={annotation.parent_id})"
                )

            # Find which .rm text block this annotation overlaps
            matched_rm_block = None
            for rm_block in rm_text_blocks:
                if rm_block.y_start <= anno_y_absolute <= rm_block.y_end:
                    matched_rm_block = rm_block
                    break

            if not matched_rm_block:
                logger.debug(
                    f"Annotation at Y={anno_y:.1f} (absolute Y={anno_y_absolute:.1f}) "
                    f"doesn't match any text block"
                )
                continue

            # Match .rm block to markdown paragraph
            paragraph_index = match_rm_block_to_paragraph(matched_rm_block, markdown_blocks)

        if paragraph_index is None:
            logger.debug(f"Could not match .rm block to markdown paragraph")
            continue

        # Initialize annotation info if needed
        if paragraph_index not in paragraph_annotations:
            paragraph_annotations[paragraph_index] = AnnotationInfo()

        # Increment appropriate counter
        if annotation.type == AnnotationType.HIGHLIGHT:
            paragraph_annotations[paragraph_index].highlights += 1
        elif annotation.type == AnnotationType.STROKE:
            paragraph_annotations[paragraph_index].strokes += 1

    # Fallback: If some annotations didn't match, try text-based matching
    matched_count = sum(info.total for info in paragraph_annotations.values())
    if matched_count < len(annotations):
        unmatched_count = len(annotations) - matched_count
        logger.info(f"Using text-matching fallback for {unmatched_count} unmatched annotations")

        # Find the longest text block (likely the main paragraph)
        if rm_text_blocks:
            longest_block = max(rm_text_blocks, key=lambda b: len(b.content))
            target_paragraph = match_rm_block_to_paragraph(longest_block, markdown_blocks)

            if target_paragraph is not None:
                logger.info(f"Assigning unmatched annotations to paragraph {target_paragraph}")

                if target_paragraph not in paragraph_annotations:
                    paragraph_annotations[target_paragraph] = AnnotationInfo()

                # Estimate: assume remaining annotations are evenly split by type
                # This is a rough heuristic since we don't know which specific annotations failed
                paragraph_annotations[target_paragraph].strokes += unmatched_count

    logger.info(
        f"Mapped {len(annotations)} annotations to {len(paragraph_annotations)} paragraphs"
    )

    return paragraph_annotations
