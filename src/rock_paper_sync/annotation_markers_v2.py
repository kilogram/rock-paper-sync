"""Format and manage annotation markers in markdown files (v2 - aligned).

This module provides functionality to add HTML comment markers to annotated paragraphs
using the actual parser ContentBlock boundaries, ensuring perfect alignment.

Key Improvement over v1:
    Uses ContentBlock list from parser instead of naive `\n\n` splitting,
    eliminating paragraph index misalignment issues.

Marker Format:
    <!-- ANNOTATED: 2 highlights, 1 stroke -->
    This paragraph has device annotations.
    <!-- /ANNOTATED -->

Architecture:
    1. Parse markdown to ContentBlock list
    2. Add markers around annotated blocks
    3. Reconstruct markdown with markers at correct positions
    4. Strip markers before device sync

Example Usage:
    >>> from parser import parse_markdown_file
    >>> from annotations.core.processor import AnnotationProcessor
    >>>
    >>> # Parse markdown
    >>> doc = parse_markdown_file(Path("Document.md"))
    >>>
    >>> # Map annotations from device
    >>> processor = AnnotationProcessor()
    >>> annotation_map = processor.map_annotations_to_paragraphs("Document.rm", doc.content)
    >>>
    >>> # Add markers (aligned with parser blocks!)
    >>> marked_content = add_annotation_markers_aligned(doc.content, annotation_map)
    >>>
    >>> # Write back to file
    >>> with open("Document.md", "w") as f:
    >>>     f.write(marked_content)
"""

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .annotations.core.data_types import AnnotationInfo
    from .parser import ContentBlock

from .markdown_reconstruction import block_to_markdown, blocks_to_markdown

logger = logging.getLogger("rock_paper_sync.annotation_markers")

# Marker format constants
MARKER_START_PREFIX = "<!-- ANNOTATED: "
MARKER_START_SUFFIX = " -->"
MARKER_END = "<!-- /ANNOTATED -->"

# Regex patterns for marker detection and stripping
MARKER_START_PATTERN = re.compile(r"^<!-- ANNOTATED: .+? -->$", re.MULTILINE)
MARKER_END_PATTERN = re.compile(r"^<!-- /ANNOTATED -->$", re.MULTILINE)


def format_marker(annotation_info: "AnnotationInfo") -> str:
    """Format an annotation marker comment.

    Args:
        annotation_info: Summary of annotations for the paragraph

    Returns:
        HTML comment marker string

    Examples:
        >>> from annotation_mapper import AnnotationInfo
        >>> info = AnnotationInfo(highlights=2, strokes=1)
        >>> format_marker(info)
        '<!-- ANNOTATED: 2 highlights, 1 stroke -->'
    """
    return f"{MARKER_START_PREFIX}{annotation_info}{MARKER_START_SUFFIX}"


def add_annotation_markers_aligned(
    content_blocks: list["ContentBlock"], annotation_map: dict[int, "AnnotationInfo"]
) -> str:
    """Add HTML comment markers to annotated blocks (ALIGNED version).

    This function uses the actual ContentBlock list from the parser to ensure
    markers align perfectly with block boundaries. This fixes the critical
    paragraph index mismatch bug from v1.

    Args:
        content_blocks: Parsed ContentBlock list from parser
        annotation_map: Dictionary mapping block index to annotation info

    Returns:
        Markdown content with markers added at correct positions

    Example:
        >>> from parser import parse_markdown_file, ContentBlock, BlockType
        >>> from annotation_mapper import AnnotationInfo
        >>>
        >>> # Parse markdown
        >>> doc = parse_markdown_file(Path("doc.md"))
        >>> # doc.content = [Header, Paragraph, ListItem, Paragraph]
        >>>
        >>> # Annotate block index 1 (Paragraph)
        >>> annotation_map = {1: AnnotationInfo(highlights=2)}
        >>>
        >>> # Add markers
        >>> marked = add_annotation_markers_aligned(doc.content, annotation_map)
        >>> # Marker appears around correct paragraph, NOT list items
    """
    if not annotation_map:
        logger.debug("No annotations to mark")
        return blocks_to_markdown(content_blocks)

    marked_blocks = []

    for idx, block in enumerate(content_blocks):
        # Check if this block has annotations
        if idx in annotation_map:
            info = annotation_map[idx]
            marker_start = format_marker(info)

            # Convert block to markdown
            block_text = block_to_markdown(block)

            # Wrap with markers
            marked_block = f"{marker_start}\n{block_text}\n{MARKER_END}"
            marked_blocks.append(marked_block)

            logger.debug(f"Added marker to block {idx}: {info}")
        else:
            # No annotations - add block as-is
            block_text = block_to_markdown(block)
            marked_blocks.append(block_text)

    # Join blocks with double newlines (paragraph separation)
    result = "\n\n".join(marked_blocks)

    logger.info(f"Added {len(annotation_map)} annotation markers (aligned)")

    return result


def strip_annotation_markers(markdown_content: str) -> str:
    """Remove all annotation markers from markdown content.

    This function removes both start and end annotation markers, leaving only
    the original paragraph content. Used before syncing to device to keep the
    device view clean.

    Args:
        markdown_content: Markdown content with annotation markers

    Returns:
        Clean markdown content without markers

    Example:
        >>> content = '''<!-- ANNOTATED: 2 highlights -->
        ... This is annotated text.
        ... <!-- /ANNOTATED -->
        ...
        ... This is normal text.'''
        >>> clean = strip_annotation_markers(content)
        >>> print(clean)
        This is annotated text.
        <BLANKLINE>
        This is normal text.
    """
    # Remove start markers (<!-- ANNOTATED: ... -->)
    content = MARKER_START_PATTERN.sub("", markdown_content)

    # Remove end markers (<!-- /ANNOTATED -->)
    content = MARKER_END_PATTERN.sub("", content)

    # Clean up extra blank lines that might be left behind
    # Replace 3+ consecutive newlines with just 2 (paragraph break)
    content = re.sub(r"\n{3,}", "\n\n", content)

    # Trim leading/trailing whitespace
    content = content.strip()

    # Count how many markers were removed
    marker_count = markdown_content.count(MARKER_START_PREFIX)
    if marker_count > 0:
        logger.debug(f"Stripped {marker_count} annotation markers")

    return content


def has_annotation_markers(markdown_content: str) -> bool:
    """Check if markdown content contains any annotation markers.

    Args:
        markdown_content: Markdown content to check

    Returns:
        True if markers are present, False otherwise

    Example:
        >>> has_annotation_markers("<!-- ANNOTATED: 1 highlight -->\\nText\\n<!-- /ANNOTATED -->")
        True
        >>> has_annotation_markers("Regular markdown text")
        False
    """
    return MARKER_START_PREFIX in markdown_content


def count_annotation_markers(markdown_content: str) -> int:
    """Count the number of annotation markers in markdown content.

    Args:
        markdown_content: Markdown content to analyze

    Returns:
        Number of annotation markers found

    Example:
        >>> content = "<!-- ANNOTATED: 1 -->\\nPara1\\n<!-- /ANNOTATED -->\\n\\n<!-- ANNOTATED: 2 -->\\nPara2\\n<!-- /ANNOTATED -->"
        >>> count_annotation_markers(content)
        2
    """
    return markdown_content.count(MARKER_START_PREFIX)


def update_markers_aligned(
    content_blocks: list["ContentBlock"], new_annotation_map: dict[int, "AnnotationInfo"]
) -> str:
    """Update annotation markers with current annotation state (ALIGNED version).

    This function regenerates markers based on the current ContentBlock list
    and annotation map. It does NOT strip existing markers first - it assumes
    you're working with fresh content from the parser.

    Args:
        content_blocks: Parsed ContentBlock list from parser (fresh parse)
        new_annotation_map: Updated annotation mapping

    Returns:
        Markdown content with current markers

    Example:
        >>> # Re-parse file to get fresh ContentBlocks
        >>> doc = parse_markdown_file(Path("Document.md"))
        >>>
        >>> # Get current annotations from device
        >>> annotation_map = map_annotations_to_paragraphs("Document.rm", doc.content)
        >>>
        >>> # Update markers
        >>> updated = update_markers_aligned(doc.content, annotation_map)
    """
    # Simply add markers to the content blocks
    # (Parser already gives us clean content without old markers)
    updated_content = add_annotation_markers_aligned(content_blocks, new_annotation_map)

    logger.info("Updated annotation markers (aligned)")

    return updated_content
