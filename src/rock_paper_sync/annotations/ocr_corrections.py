"""OCR correction detection coordinator.

Simple coordinator function for detecting OCR corrections in markdown files.
This is a minimal, focused implementation for collecting training data only.
"""

import logging
from pathlib import Path

from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.annotations.core.data_types import RenderConfig, OCRCorrection
from rock_paper_sync.annotations.common.snapshots import SnapshotStore

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
    stroke_handler = StrokeHandler()

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
            logger.debug(
                f"No snapshot for {vault_name}:{file_path}[{para_idx}], skipping"
            )
            continue

        # Check each stroke in this paragraph
        for stroke_info in strokes:
            correction = stroke_handler.detect_ocr_corrections(
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
