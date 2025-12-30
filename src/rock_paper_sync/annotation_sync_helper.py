"""Helper class for annotation synchronization.

Handles all annotation-related operations for the sync engine including:
- Downloading annotations from device
- Mapping annotations to markdown paragraphs
- Updating annotation markers in markdown files
- OCR processing and correction detection
"""

import logging
from pathlib import Path

from .annotation_markers_v2 import add_annotation_markers_aligned
from .annotations.core.data_types import AnnotationInfo
from .annotations.core.processor import AnnotationProcessor
from .generator import RemarkableGenerator
from .layout import LayoutContext
from .ocr.integration import OCRProcessor
from .rm_cloud_sync import RmCloudSync
from .state import StateManager

logger = logging.getLogger("rock_paper_sync.annotation_sync_helper")


def merge_annotation_maps(
    target: dict[int, AnnotationInfo], source: dict[int, AnnotationInfo]
) -> None:
    """Merge source annotation map into target, combining counts for matching indices.

    Args:
        target: Target annotation map to merge into (modified in place)
        source: Source annotation map to merge from
    """
    for idx, info in source.items():
        if idx in target:
            target[idx].highlights += info.highlights
            target[idx].strokes += info.strokes
            target[idx].notes += info.notes
        else:
            target[idx] = info


class AnnotationSyncHelper:
    """Handles annotation downloading, mapping, and marker insertion.

    This helper class encapsulates all annotation-related logic that was
    previously embedded in SyncEngine. It provides a clean separation between
    sync orchestration and annotation domain logic.

    Responsibilities:
    - Check if annotations need downloading (cloud version comparison)
    - Download .rm files from cloud
    - Map annotations to paragraph indices
    - Update annotation markers in markdown files
    - Process OCR for handwriting recognition
    - Detect OCR corrections for training data
    """

    def __init__(
        self,
        cloud_sync: RmCloudSync,
        state: StateManager,
        generator: RemarkableGenerator,
        annotation_processor: AnnotationProcessor,
        ocr_processor: OCRProcessor | None,
        cache_dir: Path,
    ) -> None:
        """Initialize annotation sync helper.

        Args:
            cloud_sync: Cloud sync client for downloading files
            state: State manager for tracking sync status
            generator: Document generator for pagination metadata
            annotation_processor: Processor for mapping annotations
            ocr_processor: Optional OCR processor for handwriting recognition
            cache_dir: Directory for caching downloaded files
        """
        self.cloud_sync = cloud_sync
        self.state = state
        self.generator = generator
        self.annotation_processor = annotation_processor
        self.ocr_processor = ocr_processor
        self.cache_dir = cache_dir

    def should_download_annotations(
        self,
        vault_name: str,
        relative_path: str,
        doc_uuid: str,
        current_generation: int | None = None,
        doc_hash_map: dict[str, str | None] | None = None,
    ) -> bool:
        """Check if annotations should be downloaded from the device.

        Uses cloud versioning primitives (root_generation + doc_index_hash) to
        efficiently detect annotation changes without downloading files.

        Args:
            vault_name: Name of the vault
            relative_path: Relative path within vault
            doc_uuid: Document UUID
            current_generation: Pre-fetched cloud generation (avoids redundant API call)
            doc_hash_map: Pre-fetched map of doc_uuid -> hash (avoids redundant API calls)

        Returns:
            True if annotations should be downloaded, False otherwise

        Detection Logic:
            1. Get current cloud state (generation + doc_index_hash)
            2. Compare to stored state
            3. If generation unchanged → no cloud changes
            4. If doc_index_hash unchanged → document unchanged
            5. Otherwise → download annotations
        """
        # Get current state
        state = self.state.get_file_state(vault_name, relative_path)
        if not state:
            # No previous sync - will be handled by normal upload flow
            logger.debug(
                f"No previous state for {vault_name}:{relative_path}, skipping annotation check"
            )
            return False

        # Use pre-fetched cloud state if provided, otherwise fetch
        if current_generation is None:
            _, _, current_generation = self.cloud_sync.get_root_state()

        if doc_hash_map is not None:
            current_doc_hash = doc_hash_map.get(doc_uuid)
        else:
            current_doc_hash = self.cloud_sync.get_document_index_hash(doc_uuid)

        logger.debug(
            f"Annotation check for {vault_name}:{relative_path}: "
            f"state_gen={state.last_root_generation}, cloud_gen={current_generation}, "
            f"state_hash={state.last_doc_index_hash[:8] if state.last_doc_index_hash else 'None'}, "
            f"cloud_hash={current_doc_hash[:8] if current_doc_hash else 'None'}"
        )

        # Check if cloud has changed since last sync
        if state.last_root_generation is not None:
            if current_generation <= state.last_root_generation:
                # No cloud changes since last sync
                logger.debug(
                    f"Cloud unchanged (gen {current_generation} <= {state.last_root_generation})"
                )
                return False

        # Check if document has changed
        if state.last_doc_index_hash is not None and current_doc_hash is not None:
            if current_doc_hash == state.last_doc_index_hash:
                # Document unchanged (no new annotations)
                logger.debug(
                    f"Document unchanged (hash {current_doc_hash[:8]}... == {state.last_doc_index_hash[:8]}...)"
                )
                return False

        # Cloud changed and document changed - download annotations
        logger.info(
            f"Annotation changes detected for {vault_name}:{relative_path} "
            f"(gen {state.last_root_generation} -> {current_generation}, "
            f"hash {state.last_doc_index_hash[:8] if state.last_doc_index_hash else 'None'}... -> {current_doc_hash[:8] if current_doc_hash else 'None'}...)"
        )
        return True

    def build_annotation_map(
        self, rm_files: list[Path | None], content_blocks: list
    ) -> dict[int, AnnotationInfo]:
        """Build annotation map from .rm files.

        Uses LayoutContext.from_rm_file() to enable position-based mapping
        for strokes. This was the missing connection that caused strokes to
        be lost when page_y_start wasn't available on ContentBlocks.

        Args:
            rm_files: List of .rm file paths (may contain None)
            content_blocks: ContentBlock list for annotation mapping

        Returns:
            Dictionary mapping paragraph_index to AnnotationInfo
        """
        annotation_map = {}
        for rm_file in rm_files:
            if rm_file and rm_file.exists():
                # Create layout context from .rm file for position-based mapping
                # This enables stroke mapping using position_to_offset()
                layout_context = LayoutContext.from_rm_file(rm_file, use_font_metrics=True)

                file_annotations = self.annotation_processor.map_annotations_to_paragraphs(
                    rm_file, content_blocks, layout_context=layout_context
                )
                merge_annotation_maps(annotation_map, file_annotations)
        return annotation_map

    def download_and_map_annotations(
        self,
        doc_uuid: str,
        content_blocks: list,
    ) -> tuple[list[Path | None], dict[int, AnnotationInfo]]:
        """Download .rm files and build annotation map.

        Consolidates the download + mapping pattern that appears in multiple
        places in sync_file().

        Args:
            doc_uuid: Document UUID to download
            content_blocks: ContentBlock list for annotation mapping

        Returns:
            Tuple of (rm_files, annotation_map)
        """
        # Get existing page UUIDs
        page_uuids = self.cloud_sync.get_existing_page_uuids(doc_uuid)
        if not page_uuids:
            logger.debug(f"No existing pages found for document {doc_uuid}")
            return [], {}

        # Download .rm files
        temp_dir = self.cache_dir / "annotations" / doc_uuid
        rm_files = self.cloud_sync.download_page_rm_files(doc_uuid, page_uuids, temp_dir)

        logger.info(f"Downloaded {len([f for f in rm_files if f])} .rm files for {doc_uuid}")

        # Build annotation map
        annotation_map = self.build_annotation_map(rm_files, content_blocks)

        return rm_files, annotation_map

    def _detect_and_store_ocr_corrections(
        self,
        vault_name: str,
        file_path: str,
        markdown_path: Path,
        annotation_map: dict[int, AnnotationInfo],
    ) -> None:
        """Detect and store OCR corrections for training data.

        Compares current markdown against stored snapshots to detect user edits
        to OCR text. Stores detected corrections in database for training.

        Args:
            vault_name: Vault name
            file_path: Relative file path
            markdown_path: Absolute path to markdown file
            annotation_map: Map of paragraph_index -> AnnotationInfo
        """
        import uuid

        from rock_paper_sync.annotations.core.data_types import RenderConfig
        from rock_paper_sync.annotations.ocr_corrections import (
            detect_ocr_corrections_for_file,
        )

        try:
            # Read current markdown content
            current_markdown = markdown_path.read_text(encoding="utf-8")

            # Build stroke metadata from OCR state
            # Get all OCR results for this file to find strokes
            stroke_metadata = {}
            all_ocr_results = self.state.get_all_ocr_results(vault_name, file_path)

            for para_idx, ocr_data in all_ocr_results.items():
                if para_idx in annotation_map and annotation_map[para_idx].strokes > 0:
                    stroke_metadata[para_idx] = [
                        {
                            "annotation_id": ocr_data["annotation_uuid"],
                            "image_hash": ocr_data["image_hash"],
                        }
                    ]

            if not stroke_metadata:
                logger.debug(
                    f"No stroke metadata for {vault_name}:{file_path}, skipping correction detection"
                )
                return

            # Detect corrections
            corrections = detect_ocr_corrections_for_file(
                vault_name=vault_name,
                file_path=file_path,
                current_markdown=current_markdown,
                snapshot_store=self.state.snapshots,
                stroke_metadata=stroke_metadata,
                config=RenderConfig(stroke_style="comment"),  # Default config
            )

            # Store corrections in database
            for correction in corrections:
                # Generate correction ID
                correction_id = str(uuid.uuid4())

                # Get image path from OCR state
                image_path = ""
                for ocr_data in all_ocr_results.values():
                    if ocr_data["image_hash"] == correction.image_hash:
                        # Reconstruct image path (stored in corrections cache)
                        image_path = str(
                            self.cache_dir
                            / "corrections"
                            / "images"
                            / f"{correction.image_hash}.png"
                        )
                        break

                self.state.add_ocr_correction(
                    correction_id=correction_id,
                    image_hash=correction.image_hash,
                    image_path=image_path,
                    original_text=correction.original_text,
                    corrected_text=correction.corrected_text,
                    paragraph_context=correction.paragraph_context,
                    document_id=correction.document_id,
                )

                logger.info(
                    f"Stored OCR correction: '{correction.original_text[:30]}...' -> "
                    f"'{correction.corrected_text[:30]}...'"
                )

        except Exception as e:
            logger.warning(
                f"Failed to detect OCR corrections for {vault_name}:{file_path}: {e}",
                exc_info=True,
            )
            # Don't fail the sync if correction detection fails

    def update_annotation_markers(
        self,
        markdown_path: Path,
        content_blocks: list,
        annotation_map: dict[int, AnnotationInfo],
        vault_name: str,
        relative_path: str,
        rm_files: list[Path] | None = None,
    ) -> None:
        """Update annotation markers in markdown file (automatic bi-directional sync).

        This method adds or updates HTML comment markers in the markdown file to
        indicate which paragraphs have annotations on the device. This is part of
        the automatic bi-directional sync - no user interaction required.

        If OCR is enabled and rm_files are provided, also processes annotations
        for handwriting recognition.

        Also detects OCR corrections (user edits to OCR text) for training data
        collection before updating with new OCR results.

        Uses ContentBlock-aligned marker insertion to ensure markers appear at
        correct paragraph boundaries.

        Args:
            markdown_path: Path to markdown file
            content_blocks: Parsed ContentBlock list from parser
            annotation_map: Dictionary mapping block index to annotation info
            vault_name: Name of vault
            relative_path: Relative path in vault
            rm_files: Optional list of .rm files for OCR processing
        """
        logger.debug(f"Updating annotation markers for {len(annotation_map)} blocks")

        # Detect OCR corrections before processing new OCR results
        # This captures user edits to OCR text for training data
        if self.ocr_processor and rm_files:
            self._detect_and_store_ocr_corrections(
                vault_name=vault_name,
                file_path=relative_path,
                markdown_path=markdown_path,
                annotation_map=annotation_map,
            )

        # Add markers using ContentBlock alignment
        marked_content = add_annotation_markers_aligned(content_blocks, annotation_map)

        # Run OCR if enabled and rm_files available
        if self.ocr_processor and rm_files:
            logger.info(f"Running OCR on {len(rm_files)} .rm file(s)")

            from rock_paper_sync.annotations.document_model import DocumentModel
            from rock_paper_sync.layout import DEFAULT_DEVICE

            # Extract paragraph texts from content blocks
            paragraph_texts = [block.text for block in content_blocks]

            # Build DocumentModel from rm_files (handles clustering)
            document_model = DocumentModel.from_rm_files(rm_files, DEFAULT_DEVICE)

            # Process annotations with OCR using DocumentModel
            marked_content = self.ocr_processor.process_annotations(
                vault_name=vault_name,
                obsidian_path=relative_path,
                markdown_content=marked_content,
                annotation_map=annotation_map,
                document_model=document_model,
                paragraph_texts=paragraph_texts,
            )
            logger.info(f"OCR processing complete for {vault_name}:{relative_path}")

            # Update snapshots after OCR processing for future correction detection
            # Parse the new content with OCR results into paragraphs
            from rock_paper_sync.annotations.ocr_corrections import parse_paragraphs

            new_paragraphs = parse_paragraphs(marked_content)
            for para_idx, para_text in enumerate(new_paragraphs):
                # Only snapshot paragraphs that have strokes
                if para_idx in annotation_map and annotation_map[para_idx].strokes > 0:
                    self.state.snapshots.snapshot_block(
                        vault_name=vault_name,
                        file_path=relative_path,
                        paragraph_index=para_idx,
                        block_content=para_text,
                        annotation_types=["stroke"],
                    )
            logger.debug(
                f"Updated {len([i for i in annotation_map if annotation_map[i].strokes > 0])} "
                f"paragraph snapshots for future correction detection"
            )

        # Write marked content back to file
        with open(markdown_path, "w", encoding="utf-8") as f:
            f.write(marked_content)

        # Note: State doesn't need updating here since content_hash is semantic (markers stripped)
        # The annotation markers are local presentation only and don't affect sync state

        logger.info(
            f"Updated {len(annotation_map)} annotation markers in {vault_name}:{relative_path}"
        )
