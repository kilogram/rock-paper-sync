"""Pull sync engine for reMarkable → Obsidian synchronization (M5).

This module implements the pull direction of bidirectional sync:
extracting annotations from device and rendering them into markdown files.

Key responsibilities:
- Detect annotation changes on device
- Download and extract annotations from .rm files
- Reanchor annotations to current markdown content
- Render annotations to markdown (highlights, strokes)
- Handle orphaned annotations (hidden layer + comment)
- Update pull state tracking
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from rock_paper_sync.annotation_renderer import (
    AnnotationRenderer,
    RenderConfig,
)
from rock_paper_sync.annotation_sync_helper import AnnotationChange, AnnotationSyncHelper
from rock_paper_sync.annotations.document_model import DocumentAnnotation, DocumentModel
from rock_paper_sync.config import VaultConfig
from rock_paper_sync.layout import DEFAULT_DEVICE
from rock_paper_sync.rm_cloud_sync import RmCloudSync
from rock_paper_sync.state import OrphanedAnnotation, StateManager

logger = logging.getLogger("rock_paper_sync.pull_sync")


@dataclass
class PullResult:
    """Result of pulling annotations for a single file."""

    vault_name: str
    obsidian_path: str
    success: bool
    highlights_added: int = 0
    strokes_added: int = 0
    orphans_count: int = 0
    error: str | None = None


@dataclass
class PullStats:
    """Statistics for a pull sync operation."""

    files_checked: int = 0
    files_updated: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    total_highlights: int = 0
    total_strokes: int = 0
    total_orphans: int = 0


class PullSyncEngine:
    """Orchestrates pull sync from reMarkable to Obsidian.

    This engine handles the complete flow of detecting annotation changes
    on the device and rendering them into markdown files.
    """

    def __init__(
        self,
        state: StateManager,
        cloud_sync: RmCloudSync,
        annotation_helper: AnnotationSyncHelper,
        cache_dir: Path,
        render_config: RenderConfig | None = None,
    ) -> None:
        """Initialize pull sync engine.

        Args:
            state: State manager for tracking sync status
            cloud_sync: Cloud sync client for downloading files
            annotation_helper: Helper for annotation operations
            cache_dir: Directory for caching downloaded files
            render_config: Optional render configuration
        """
        self.state = state
        self.cloud_sync = cloud_sync
        self.annotation_helper = annotation_helper
        self.cache_dir = cache_dir
        self.render_config = render_config or RenderConfig()
        self.renderer = AnnotationRenderer(self.render_config)

    def detect_changes(self, vault_name: str | None = None) -> list[AnnotationChange]:
        """Detect annotation changes from device.

        Args:
            vault_name: Optional vault name to filter by

        Returns:
            List of AnnotationChange records for documents with changes
        """
        return self.annotation_helper.detect_annotation_changes(vault_name)

    def pull_file(
        self,
        change: AnnotationChange,
        vault_path: Path,
        dry_run: bool = False,
    ) -> PullResult:
        """Pull annotations for a single file.

        Args:
            change: AnnotationChange describing what changed
            vault_path: Path to the vault root
            dry_run: If True, don't modify files

        Returns:
            PullResult with statistics and status
        """
        try:
            # Get file path
            file_path = vault_path / change.obsidian_path
            if not file_path.exists():
                return PullResult(
                    vault_name=change.vault_name,
                    obsidian_path=change.obsidian_path,
                    success=False,
                    error=f"File not found: {file_path}",
                )

            # Read current markdown content
            markdown_content = file_path.read_text(encoding="utf-8")

            # Download .rm files
            temp_dir = self.cache_dir / "pull" / change.remarkable_uuid
            page_uuids = self.cloud_sync.get_existing_page_uuids(change.remarkable_uuid)
            if not page_uuids:
                return PullResult(
                    vault_name=change.vault_name,
                    obsidian_path=change.obsidian_path,
                    success=True,  # No pages = no annotations
                    highlights_added=0,
                    strokes_added=0,
                )

            rm_files = self.cloud_sync.download_page_rm_files(
                change.remarkable_uuid, page_uuids, temp_dir
            )

            # Build document model from .rm files
            valid_files = [f for f in rm_files if f and f.exists()]
            if not valid_files:
                return PullResult(
                    vault_name=change.vault_name,
                    obsidian_path=change.obsidian_path,
                    success=True,
                    highlights_added=0,
                    strokes_added=0,
                )

            document_model = DocumentModel.from_rm_files(valid_files, DEFAULT_DEVICE)

            # Reanchor annotations to current markdown content
            migrated, orphaned = self._reanchor_annotations(
                document_model.annotations, markdown_content
            )

            # Create new document model with migrated annotations
            migrated_model = DocumentModel(
                paragraphs=document_model.paragraphs,
                annotations=migrated,
                full_text=markdown_content,
                annotation_store=document_model.annotation_store,
                geometry=document_model.geometry,
            )

            # Render annotations to markdown
            render_result = self.renderer.render(markdown_content, migrated_model, orphaned)

            if not dry_run:
                # Write updated content
                file_path.write_text(render_result.content, encoding="utf-8")

                # Update pull state
                self.annotation_helper.update_pull_state(
                    vault_name=change.vault_name,
                    obsidian_path=change.obsidian_path,
                    remarkable_uuid=change.remarkable_uuid,
                    annotation_hash=change.current_annotation_hash,
                )

                # Record orphaned annotations
                self._record_orphans(change, orphaned)

            logger.info(
                f"Pulled annotations for {change.vault_name}:{change.obsidian_path}: "
                f"{render_result.highlights_rendered} highlights, "
                f"{render_result.strokes_rendered} strokes, "
                f"{render_result.orphans_count} orphans"
            )

            return PullResult(
                vault_name=change.vault_name,
                obsidian_path=change.obsidian_path,
                success=True,
                highlights_added=render_result.highlights_rendered,
                strokes_added=render_result.strokes_rendered,
                orphans_count=render_result.orphans_count,
            )

        except Exception as e:
            logger.error(
                f"Failed to pull annotations for {change.vault_name}:{change.obsidian_path}: {e}",
                exc_info=True,
            )
            return PullResult(
                vault_name=change.vault_name,
                obsidian_path=change.obsidian_path,
                success=False,
                error=str(e),
            )

    def pull_vault(
        self,
        vault: VaultConfig,
        dry_run: bool = False,
    ) -> tuple[list[PullResult], PullStats]:
        """Pull annotations for all changed files in a vault.

        Args:
            vault: Vault configuration
            dry_run: If True, don't modify files

        Returns:
            Tuple of (list of PullResult, PullStats)
        """
        stats = PullStats()
        results: list[PullResult] = []

        # Detect changes
        changes = self.detect_changes(vault.name)
        stats.files_checked = len(self.state.get_all_synced_files(vault.name))

        if not changes:
            logger.info(f"No annotation changes detected for vault {vault.name}")
            return results, stats

        logger.info(f"Found {len(changes)} files with annotation changes in {vault.name}")

        for change in changes:
            result = self.pull_file(change, vault.path, dry_run)
            results.append(result)

            if result.success:
                if result.highlights_added > 0 or result.strokes_added > 0:
                    stats.files_updated += 1
                    stats.total_highlights += result.highlights_added
                    stats.total_strokes += result.strokes_added
                    stats.total_orphans += result.orphans_count
                else:
                    stats.files_skipped += 1
            else:
                stats.files_errored += 1

        return results, stats

    def _reanchor_annotations(
        self,
        annotations: list[DocumentAnnotation],
        new_content: str,
    ) -> tuple[list[DocumentAnnotation], list[DocumentAnnotation]]:
        """Reanchor annotations to new content using AnchorContext.

        Args:
            annotations: List of annotations to reanchor
            new_content: New markdown content

        Returns:
            Tuple of (migrated annotations, orphaned annotations)
        """
        migrated = []
        orphaned = []

        for annotation in annotations:
            if not annotation.anchor_context:
                orphaned.append(annotation)
                continue

            # Try to resolve anchor in new content
            # resolve() needs old_text - use anchor's text_content as a proxy
            # since we're reanchoring to new content from device annotations
            old_text = annotation.anchor_context.text_content
            resolved = annotation.anchor_context.resolve(old_text, new_content)

            if resolved and resolved.confidence >= 0.6:
                # Create new annotation with updated anchor
                from rock_paper_sync.annotations.document_model import AnchorContext

                new_anchor = AnchorContext.from_text_span(
                    new_content, resolved.start_offset, resolved.end_offset
                )
                new_annotation = DocumentAnnotation(
                    annotation_id=annotation.annotation_id,
                    annotation_type=annotation.annotation_type,
                    source_page_idx=annotation.source_page_idx,
                    anchor_context=new_anchor,
                    stroke_data=annotation.stroke_data,
                    highlight_data=annotation.highlight_data,
                )
                migrated.append(new_annotation)
                logger.debug(
                    f"Reanchored {annotation.annotation_type} "
                    f"(confidence={resolved.confidence:.2f})"
                )
            else:
                orphaned.append(annotation)
                logger.debug(
                    f"Orphaned {annotation.annotation_type}: "
                    f"anchor text not found in new content"
                )

        return migrated, orphaned

    def _record_orphans(
        self,
        change: AnnotationChange,
        orphaned: list[DocumentAnnotation],
    ) -> None:
        """Record orphaned annotations in state database.

        Args:
            change: AnnotationChange for the file
            orphaned: List of orphaned annotations
        """
        # Clear previous orphans for this file
        self.state.delete_all_orphaned_annotations(change.vault_name, change.obsidian_path)

        # Record new orphans
        for annotation in orphaned:
            anchor_text = None
            if annotation.anchor_context:
                anchor_text = annotation.anchor_context.text_content

            orphan_record = OrphanedAnnotation(
                vault_name=change.vault_name,
                obsidian_path=change.obsidian_path,
                annotation_id=annotation.annotation_id,
                annotation_type=annotation.annotation_type,
                original_anchor_text=anchor_text,
                orphaned_at=int(time.time()),
            )
            self.state.add_orphaned_annotation(orphan_record)
