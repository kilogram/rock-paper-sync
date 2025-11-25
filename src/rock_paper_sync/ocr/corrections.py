"""OCR correction detection and storage.

Detects user corrections to OCR text and stores them for fine-tuning.
"""

import hashlib
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rock_paper_sync.ocr.markers import extract_paragraph_index_mapping

if TYPE_CHECKING:
    from rock_paper_sync.state import StateManager

logger = logging.getLogger("rock_paper_sync.ocr.corrections")


@dataclass
class Correction:
    """A correction record for fine-tuning."""

    id: str
    image_hash: str
    image_path: Path
    original_text: str
    corrected_text: str
    paragraph_context: str
    document_id: str
    vault_name: str
    obsidian_path: str
    paragraph_index: int
    created_at: int
    dataset_version: str | None = None


class CorrectionManager:
    """Manages OCR corrections for fine-tuning.

    Detects when users edit OCR text in markdown files and stores
    the corrections along with the original annotation images for
    supervised training data.
    """

    def __init__(self, cache_dir: Path, state_manager: "StateManager") -> None:
        """Initialize correction manager.

        Args:
            cache_dir: XDG cache directory for OCR data
            state_manager: State manager for database access
        """
        self.cache_dir = cache_dir
        self.state_manager = state_manager

        # Ensure directories exist
        self.corrections_dir = cache_dir / "corrections"
        self.images_dir = self.corrections_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(f"CorrectionManager initialized with cache dir: {cache_dir}")

    def detect_corrections(
        self,
        vault_name: str,
        obsidian_path: str,
        markdown: str,
    ) -> list[Correction]:
        """Detect corrections in markdown by comparing with stored state.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file
            markdown: Current markdown content

        Returns:
            List of detected corrections
        """
        corrections = []

        # Get current OCR blocks from markdown
        current_blocks = extract_paragraph_index_mapping(markdown)

        # Get stored OCR results from state
        stored_results = self.state_manager.get_all_ocr_results(vault_name, obsidian_path)

        for para_idx, block in current_blocks.items():
            if para_idx not in stored_results:
                continue

            stored = stored_results[para_idx]

            # Check if OCR text was corrected
            if block.ocr_text_hash != stored["ocr_text_hash"]:
                # This is a correction!
                correction = Correction(
                    id=str(uuid.uuid4()),
                    image_hash=stored["image_hash"],
                    image_path=self.images_dir / f"{stored['image_hash']}.png",
                    original_text=stored["ocr_text"],
                    corrected_text=block.ocr_text,
                    paragraph_context=block.original_text,
                    document_id=stored["annotation_uuid"],
                    vault_name=vault_name,
                    obsidian_path=obsidian_path,
                    paragraph_index=para_idx,
                    created_at=int(time.time()),
                )
                corrections.append(correction)

                logger.info(
                    f"Detected correction in {vault_name}:{obsidian_path}[{para_idx}]: "
                    f"'{stored['ocr_text'][:30]}...' -> '{block.ocr_text[:30]}...'"
                )

        return corrections

    def store_correction(self, correction: Correction) -> None:
        """Store a correction in the database.

        Args:
            correction: Correction to store
        """
        self.state_manager.add_ocr_correction(
            correction_id=correction.id,
            image_hash=correction.image_hash,
            image_path=str(correction.image_path),
            original_text=correction.original_text,
            corrected_text=correction.corrected_text,
            paragraph_context=correction.paragraph_context,
            document_id=correction.document_id,
        )

        logger.debug(f"Stored correction {correction.id}")

    def store_annotation_image(self, image_data: bytes, annotation_uuid: str) -> str:
        """Store an annotation image for potential future corrections.

        Uses atomic write via temp file to avoid TOCTOU race conditions
        when multiple processes write the same image concurrently.

        Args:
            image_data: PNG image data
            annotation_uuid: UUID of the annotation

        Returns:
            Image hash for reference
        """
        image_hash = hashlib.sha256(image_data).hexdigest()
        image_path = self.images_dir / f"{image_hash}.png"

        if not image_path.exists():
            # Use temp file + atomic rename to avoid race conditions
            temp_path = image_path.with_suffix('.tmp')
            try:
                with open(temp_path, "wb") as f:
                    f.write(image_data)
                # Atomic rename (on POSIX systems)
                temp_path.replace(image_path)
                logger.debug(f"Stored annotation image: {image_hash}")
            except FileExistsError:
                # Another process beat us to it, that's fine
                temp_path.unlink(missing_ok=True)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

        return image_hash

    def get_pending_corrections(self) -> list[dict]:
        """Get all corrections not yet assigned to a dataset.

        Returns:
            List of correction records
        """
        return self.state_manager.get_pending_ocr_corrections()

    def get_correction_count(self) -> int:
        """Get count of pending corrections.

        Returns:
            Number of unassigned corrections
        """
        return len(self.get_pending_corrections())

    def check_conflicts(
        self,
        vault_name: str,
        obsidian_path: str,
        markdown: str,
    ) -> list[int]:
        """Check for conflicts (edited original text) that require re-sync.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file
            markdown: Current markdown content

        Returns:
            List of paragraph indices with conflicts
        """
        conflicts = []

        # Get current OCR blocks from markdown
        current_blocks = extract_paragraph_index_mapping(markdown)

        # Get stored OCR results from state
        stored_results = self.state_manager.get_all_ocr_results(vault_name, obsidian_path)

        for para_idx, block in current_blocks.items():
            if para_idx not in stored_results:
                continue

            stored = stored_results[para_idx]

            # Check if original text was edited (conflict)
            if block.original_text_hash != stored["original_text_hash"]:
                conflicts.append(para_idx)
                logger.warning(
                    f"Conflict in {vault_name}:{obsidian_path}[{para_idx}]: "
                    f"original text modified, re-sync required"
                )

        return conflicts

    def process_markdown_file(
        self,
        vault_name: str,
        obsidian_path: str,
        markdown: str,
    ) -> tuple[list[Correction], list[int]]:
        """Process a markdown file for corrections and conflicts.

        Args:
            vault_name: Name of the vault
            obsidian_path: Relative path of file
            markdown: Current markdown content

        Returns:
            Tuple of (corrections, conflicts)
        """
        # Detect corrections
        corrections = self.detect_corrections(vault_name, obsidian_path, markdown)

        # Store corrections
        for correction in corrections:
            self.store_correction(correction)

        # Check for conflicts
        conflicts = self.check_conflicts(vault_name, obsidian_path, markdown)

        return corrections, conflicts

    def cleanup_orphaned_images(self) -> int:
        """Remove images not referenced by any correction.

        Returns:
            Number of images removed
        """
        # Get all image hashes referenced in corrections
        referenced_hashes = set(self.state_manager.get_all_correction_image_hashes())

        # Check all images in directory
        removed = 0
        for image_file in self.images_dir.glob("*.png"):
            image_hash = image_file.stem
            if image_hash not in referenced_hashes:
                image_file.unlink()
                removed += 1
                logger.debug(f"Removed orphaned image: {image_hash}")

        if removed > 0:
            logger.info(f"Cleaned up {removed} orphaned images")

        return removed
