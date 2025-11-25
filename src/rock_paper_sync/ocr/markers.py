"""OCR marker parsing and generation.

Handles embedding and extracting OCR results in markdown files using
a structured marker format that enables inline correction.

Marker Format:
    <!-- RPS:ANNOTATED highlights=2 strokes=1 -->
    Original paragraph text here.
    <!-- RPS:OCR -->
    recognized handwriting line 1
    recognized handwriting line 2
    <!-- RPS:END -->

Correction Semantics:
    - Text between RPS:OCR and RPS:END can be edited for corrections
    - Text between RPS:ANNOTATED and RPS:OCR is the original paragraph
    - Edits to original text indicate conflict requiring re-sync
"""

import hashlib
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("rock_paper_sync.ocr.markers")

# Marker patterns
ANNOTATED_PATTERN = re.compile(
    r'<!-- RPS:ANNOTATED\s+highlights=(\d+)\s+strokes=(\d+)\s*-->'
)
OCR_MARKER = "<!-- RPS:OCR -->"
END_MARKER = "<!-- RPS:END -->"

# Full block pattern for extraction
BLOCK_PATTERN = re.compile(
    r'<!-- RPS:ANNOTATED\s+highlights=(\d+)\s+strokes=(\d+)\s*-->\n'
    r'(.*?)\n'
    r'<!-- RPS:OCR -->\n'
    r'(.*?)\n'
    r'<!-- RPS:END -->',
    re.DOTALL
)


@dataclass
class OCRBlock:
    """Represents an OCR-annotated block in markdown."""

    paragraph_index: int
    highlights: int
    strokes: int
    original_text: str
    ocr_text: str
    original_text_hash: str
    ocr_text_hash: str

    @property
    def has_ocr_correction(self) -> bool:
        """Check if OCR text was edited (correction for training)."""
        # Will be compared against stored hash in state
        return True  # Always true initially; compare with state externally

    @property
    def has_original_edit(self) -> bool:
        """Check if original text was edited (requires re-sync)."""
        # Will be compared against stored hash in state
        return True  # Always true initially; compare with state externally


@dataclass
class AnnotationInfo:
    """Annotation counts for a paragraph."""

    paragraph_index: int
    highlights: int
    strokes: int


def generate_ocr_block(
    annotation: AnnotationInfo,
    original_text: str,
    ocr_lines: list[str],
) -> str:
    """Generate OCR marker block for a paragraph.

    Args:
        annotation: Annotation information (counts)
        original_text: Original paragraph text
        ocr_lines: List of OCR recognized text lines

    Returns:
        Formatted OCR block string
    """
    ocr_text = "\n".join(ocr_lines)

    return (
        f"<!-- RPS:ANNOTATED highlights={annotation.highlights} strokes={annotation.strokes} -->\n"
        f"{original_text}\n"
        f"{OCR_MARKER}\n"
        f"{ocr_text}\n"
        f"{END_MARKER}"
    )


def parse_ocr_blocks(markdown: str) -> list[OCRBlock]:
    """Parse all OCR blocks from markdown content.

    Args:
        markdown: Markdown content with OCR markers

    Returns:
        List of OCRBlock objects
    """
    blocks = []

    for i, match in enumerate(BLOCK_PATTERN.finditer(markdown)):
        highlights = int(match.group(1))
        strokes = int(match.group(2))
        original_text = match.group(3).strip()
        ocr_text = match.group(4).strip()

        blocks.append(OCRBlock(
            paragraph_index=i,  # Will be updated by caller with actual index
            highlights=highlights,
            strokes=strokes,
            original_text=original_text,
            ocr_text=ocr_text,
            original_text_hash=_hash_text(original_text),
            ocr_text_hash=_hash_text(ocr_text),
        ))

    return blocks


def strip_ocr_markers(markdown: str) -> str:
    """Remove all OCR marker blocks from markdown.

    Keeps only the original paragraph text, removing markers and OCR text.
    Used before syncing to device to prevent marker pollution.

    Args:
        markdown: Markdown content with OCR markers

    Returns:
        Clean markdown with markers removed
    """
    def replace_block(match: re.Match) -> str:
        # Return only the original text
        return match.group(3).strip()

    return BLOCK_PATTERN.sub(replace_block, markdown)


def add_ocr_markers(
    markdown: str,
    ocr_results: dict[int, tuple[AnnotationInfo, list[str]]],
) -> str:
    """Add OCR markers to markdown content.

    Args:
        markdown: Original markdown content
        ocr_results: Dict mapping paragraph_index to (annotation_info, ocr_lines)

    Returns:
        Markdown with OCR markers added after annotated paragraphs

    Note:
        This function expects paragraphs to be separated by blank lines.
        It inserts OCR blocks after the paragraph they annotate.
    """
    if not ocr_results:
        return markdown

    lines = markdown.split('\n')
    result_lines = []
    paragraph_index = 0
    in_paragraph = False
    paragraph_start = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip existing OCR blocks
        if ANNOTATED_PATTERN.match(line):
            # Find end of block
            while i < len(lines) and END_MARKER not in lines[i]:
                i += 1
            i += 1
            continue

        # Track paragraphs (non-empty lines after blank lines)
        if line.strip():
            if not in_paragraph:
                in_paragraph = True
                paragraph_start = len(result_lines)
            result_lines.append(line)
        else:
            if in_paragraph:
                # End of paragraph
                in_paragraph = False

                # Check if this paragraph has OCR results
                if paragraph_index in ocr_results:
                    annotation, ocr_lines = ocr_results[paragraph_index]

                    # Get paragraph text
                    para_text = '\n'.join(result_lines[paragraph_start:])

                    # Replace paragraph with OCR block
                    result_lines = result_lines[:paragraph_start]
                    result_lines.append(generate_ocr_block(annotation, para_text, ocr_lines))

                paragraph_index += 1

            result_lines.append(line)

        i += 1

    # Handle final paragraph
    if in_paragraph and paragraph_index in ocr_results:
        annotation, ocr_lines = ocr_results[paragraph_index]
        para_text = '\n'.join(result_lines[paragraph_start:])
        result_lines = result_lines[:paragraph_start]
        result_lines.append(generate_ocr_block(annotation, para_text, ocr_lines))

    return '\n'.join(result_lines)


def extract_paragraph_index_mapping(markdown: str) -> dict[int, OCRBlock]:
    """Extract OCR blocks with their paragraph indices.

    Analyzes markdown structure to determine which paragraph index
    each OCR block corresponds to.

    Args:
        markdown: Markdown content with OCR markers

    Returns:
        Dict mapping paragraph_index to OCRBlock
    """
    blocks = {}
    lines = markdown.split('\n')
    paragraph_index = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check for OCR block
        match = ANNOTATED_PATTERN.match(line)
        if match:
            # Parse the full block
            highlights = int(match.group(1))
            strokes = int(match.group(2))

            # Collect original text
            i += 1
            original_lines = []
            while i < len(lines) and OCR_MARKER not in lines[i]:
                original_lines.append(lines[i])
                i += 1

            # Skip OCR marker
            i += 1

            # Collect OCR text
            ocr_lines = []
            while i < len(lines) and END_MARKER not in lines[i]:
                ocr_lines.append(lines[i])
                i += 1

            original_text = '\n'.join(original_lines).strip()
            ocr_text = '\n'.join(ocr_lines).strip()

            blocks[paragraph_index] = OCRBlock(
                paragraph_index=paragraph_index,
                highlights=highlights,
                strokes=strokes,
                original_text=original_text,
                ocr_text=ocr_text,
                original_text_hash=_hash_text(original_text),
                ocr_text_hash=_hash_text(ocr_text),
            )

            paragraph_index += 1
            i += 1
            continue

        # Track regular paragraphs
        if line.strip():
            # In a paragraph, find end
            while i < len(lines) and lines[i].strip():
                i += 1
            paragraph_index += 1
        else:
            i += 1

    return blocks


def _hash_text(text: str) -> str:
    """Compute SHA-256 hash of text.

    Args:
        text: Text to hash

    Returns:
        Hexadecimal hash string
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def find_ocr_blocks_needing_attention(
    markdown: str,
    stored_hashes: dict[int, tuple[str, str]],
) -> tuple[list[int], list[int]]:
    """Find OCR blocks that have been modified.

    Args:
        markdown: Current markdown content
        stored_hashes: Dict mapping paragraph_index to (original_hash, ocr_hash)

    Returns:
        Tuple of (corrections, conflicts):
        - corrections: paragraph indices with OCR text corrections
        - conflicts: paragraph indices with original text edits (need re-sync)
    """
    blocks = extract_paragraph_index_mapping(markdown)
    corrections = []
    conflicts = []

    for para_idx, block in blocks.items():
        if para_idx not in stored_hashes:
            continue

        stored_original_hash, stored_ocr_hash = stored_hashes[para_idx]

        # Check for original text modification (conflict)
        if block.original_text_hash != stored_original_hash:
            conflicts.append(para_idx)
            logger.warning(
                f"Paragraph {para_idx} original text modified - requires re-sync"
            )

        # Check for OCR text correction
        elif block.ocr_text_hash != stored_ocr_hash:
            corrections.append(para_idx)
            logger.info(
                f"Paragraph {para_idx} OCR text corrected - will use for training"
            )

    return corrections, conflicts
