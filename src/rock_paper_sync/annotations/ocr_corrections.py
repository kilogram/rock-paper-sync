"""OCR correction detection for stroke annotations.

This module provides OCR-specific correction detection for handwriting-to-text
conversions. Corrections are detected by comparing OCR text between snapshot
versions and current markdown.

This is a focused implementation for collecting training data - not for
bidirectional sync.
"""

import logging

from rock_paper_sync.annotations.common.snapshots import SnapshotStore
from rock_paper_sync.annotations.core.data_types import OCRCorrection, RenderConfig

logger = logging.getLogger(__name__)


def parse_paragraphs(markdown: str) -> list[str]:
    """Split markdown into paragraphs.

    Simple paragraph splitting - splits on blank lines.

    Args:
        markdown: Markdown content

    Returns:
        List of paragraph texts
    """
    # Split on double newlines (blank lines)
    paragraphs = []
    current = []

    for line in markdown.split("\n"):
        if line.strip():
            current.append(line)
        elif current:
            paragraphs.append("\n".join(current))
            current = []

    if current:
        paragraphs.append("\n".join(current))

    return paragraphs


def detect_single_ocr_correction(
    vault_name: str,
    file_path: str,
    paragraph_index: int,
    old_paragraph: str,
    new_paragraph: str,
    annotation_id: str,
    image_hash: str,
    config: RenderConfig,
) -> OCRCorrection | None:
    """Detect OCR correction for a single paragraph.

    Compares OCR text in old vs new paragraph versions to detect user edits.
    This is a simple, focused function for collecting training data.

    Args:
        vault_name: Vault name
        file_path: File path
        paragraph_index: Paragraph index
        old_paragraph: Paragraph from snapshot
        new_paragraph: Current paragraph
        annotation_id: Annotation UUID
        image_hash: Image hash for training
        config: Rendering configuration

    Returns:
        OCRCorrection if text changed, None otherwise
    """
    # Import here to avoid circular dependency
    from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler

    stroke_handler = StrokeHandler()

    # Extract OCR text from both versions
    old_texts = stroke_handler.extract_from_markdown(old_paragraph, config)
    new_texts = stroke_handler.extract_from_markdown(new_paragraph, config)

    # Simple comparison (assumes one OCR annotation per paragraph)
    # For multiple annotations per paragraph, we'd need more sophisticated matching
    if old_texts and new_texts and old_texts[0].text != new_texts[0].text:
        return OCRCorrection(
            image_hash=image_hash,
            original_text=old_texts[0].text,
            corrected_text=new_texts[0].text,
            paragraph_context=new_paragraph,
            document_id=f"{vault_name}/{file_path}",
            annotation_id=annotation_id,
        )

    return None


def detect_ocr_corrections_for_file(
    vault_name: str,
    file_path: str,
    current_markdown: str,
    snapshot_store: SnapshotStore,
    stroke_metadata: dict[int, list[dict]],
    config: RenderConfig | None = None,
) -> list[OCRCorrection]:
    """Detect all OCR corrections in a file.

    Called during sync to check for markdown edits. Compares current markdown
    against stored snapshots to find OCR text changes.

    Args:
        vault_name: Vault name
        file_path: File path
        current_markdown: Current markdown content
        snapshot_store: Snapshot store for retrieving old versions
        stroke_metadata: Map of paragraph_index -> list of stroke info dicts
                         Each dict should have: annotation_id, image_hash
        config: Optional rendering configuration (defaults to RenderConfig())

    Returns:
        List of detected OCR corrections

    Example:
        stroke_metadata = {
            0: [{"annotation_id": "abc-123", "image_hash": "def456"}],
            2: [{"annotation_id": "ghi-789", "image_hash": "jkl012"}],
        }
        corrections = detect_ocr_corrections_for_file(
            vault_name="MyVault",
            file_path="notes/example.md",
            current_markdown=current_content,
            snapshot_store=state.snapshots,
            stroke_metadata=stroke_metadata,
        )
    """
    if config is None:
        config = RenderConfig()

    corrections = []

    # Parse current markdown into paragraphs
    current_paragraphs = parse_paragraphs(current_markdown)

    # Check each paragraph that has strokes
    for para_idx, strokes in stroke_metadata.items():
        # Skip if paragraph index out of range
        if para_idx >= len(current_paragraphs):
            logger.warning(
                f"Paragraph index {para_idx} out of range "
                f"(file has {len(current_paragraphs)} paragraphs)"
            )
            continue

        # Get current paragraph text
        current_para = current_paragraphs[para_idx]

        # Get snapshot of this paragraph
        old_para = snapshot_store.get_block_snapshot(vault_name, file_path, para_idx)

        if not old_para:
            logger.debug(f"No snapshot for {vault_name}:{file_path}[{para_idx}], skipping")
            continue

        # Check each stroke in this paragraph
        for stroke_info in strokes:
            correction = detect_single_ocr_correction(
                vault_name=vault_name,
                file_path=file_path,
                paragraph_index=para_idx,
                old_paragraph=old_para,
                new_paragraph=current_para,
                annotation_id=stroke_info["annotation_id"],
                image_hash=stroke_info["image_hash"],
                config=config,
            )

            if correction:
                corrections.append(correction)
                logger.info(
                    f"Detected OCR correction in {vault_name}:{file_path}[{para_idx}]: "
                    f"'{correction.original_text[:30]}...' -> "
                    f"'{correction.corrected_text[:30]}...'"
                )

    return corrections
