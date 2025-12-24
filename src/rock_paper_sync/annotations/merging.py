"""Annotation merging - orchestration layer for three-way merge.

This module provides the AnnotationMerger class which coordinates annotation
migration across document versions. It acts as an orchestration layer that:
- Works with already-loaded DocumentModels (preserves layer separation)
- Injects ContextResolver for testability
- Delegates to handlers for type-specific migration
- Keeps DocumentModel.migrate_annotations_to() as backward-compatible facade

Layer responsibilities:
- converter.py: Policy ("should we merge?") + file orchestration
- generator.py: Infrastructure (load files, call merger)
- AnnotationMerger: Orchestration (coordinate handlers + resolver)
- Handlers: Type-specific migration logic

Example:
    # Direct usage with explicit dependencies
    merger = AnnotationMerger(resolver=ContextResolver())
    result = merger.merge(MergeContext(old_model=old, new_model=new))

    # Access results
    merged_model = result.merged_model
    print(f"Success rate: {result.success_rate:.1%}")

    # Or use the backward-compatible facade
    merged, report = old_model.migrate_annotations_to(new_model, merger=merger)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rock_paper_sync.annotations.document_model import (
        ContextResolver,
        DocumentAnnotation,
        DocumentModel,
        MigrationReport,
        ResolvedAnchorContext,
    )
    from rock_paper_sync.layout import LayoutContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MergeContext:
    """Immutable inputs for three-way merge.

    Note: Takes already-loaded DocumentModels, not raw files.
    File loading stays in converter/generator (infrastructure layer).

    Attributes:
        old_model: Document model from .rm files (device state with annotations)
        new_model: Document model from new markdown (vault state without annotations)
    """

    old_model: DocumentModel
    new_model: DocumentModel


@dataclass
class MergeResult:
    """Output of three-way merge.

    Contains the merged document model with annotations migrated from the old
    version, plus a report with migration statistics.

    Attributes:
        merged_model: New document with migrated annotations
        report: Migration statistics (success rate, orphans, confidence)
    """

    merged_model: DocumentModel
    report: MigrationReport

    @property
    def success_rate(self) -> float:
        """Get the migration success rate (0.0 to 1.0)."""
        return self.report.success_rate

    @property
    def migrated_count(self) -> int:
        """Get the number of successfully migrated annotations."""
        return len(self.report.migrations)

    @property
    def orphan_count(self) -> int:
        """Get the number of orphaned (unmigrated) annotations."""
        return len(self.report.orphans)


class AnnotationMerger:
    """Orchestrates annotation migration across document versions.

    Design principles:
    - Works with DocumentModel (domain), not files (infrastructure)
    - Injects ContextResolver for testability
    - Delegates to handlers for type-specific migration
    - Keeps converter/generator layering intact

    Layer responsibilities preserved:
    - converter.py: Policy ("should we merge?") + file orchestration
    - generator.py: Infrastructure (load files, call merger)
    - AnnotationMerger: Orchestration (coordinate handlers + resolver)
    - Handlers: Type-specific migration logic

    Example:
        resolver = ContextResolver(fuzzy_threshold=0.8)
        merger = AnnotationMerger(resolver=resolver)

        context = MergeContext(old_model=old, new_model=new)
        result = merger.merge(context)

        print(f"Migrated {result.migrated_count} annotations")
        print(f"Success rate: {result.success_rate:.1%}")
    """

    def __init__(
        self,
        resolver: ContextResolver | None = None,
        handlers: list[Any] | None = None,
    ):
        """Initialize the AnnotationMerger.

        Args:
            resolver: ContextResolver for anchor resolution. If None, creates
                a default resolver when merge() is called.
            handlers: Optional list of AnnotationHandlers for type-specific
                migration. Currently unused but reserved for future handler
                delegation.
        """
        self._resolver = resolver
        self._handlers = handlers or []

    def merge(self, context: MergeContext) -> MergeResult:
        """Perform three-way merge with explicit inputs/outputs.

        Algorithm:
        1. Build layout contexts for old and new documents
        2. Group annotations by cluster_id
        3. For clustered annotations: resolve leader, apply to all members
        4. For unclustered annotations: resolve individually
        5. Return merged model with migrated annotations

        Args:
            context: MergeContext containing old and new document models

        Returns:
            MergeResult with merged model and migration report
        """
        from rock_paper_sync.annotations.document_model import (
            ContextResolver,
            DocumentModel,
            MigrationReport,
        )

        old_model = context.old_model
        new_model = context.new_model

        # Use provided resolver or create default
        resolver = self._resolver or ContextResolver()
        report = MigrationReport()

        # Build layout contexts for spatial matching
        old_layout = self._build_layout_context(old_model)
        new_layout = self._build_layout_context(new_model)

        migrated_annotations: list[DocumentAnnotation] = []

        # Group annotations by cluster_id
        clusters: dict[str, list[int]] = {}  # cluster_id -> annotation indices
        unclustered: list[int] = []

        for i, anno in enumerate(old_model.annotations):
            if anno.cluster_id:
                clusters.setdefault(anno.cluster_id, []).append(i)
            else:
                unclustered.append(i)

        # Migrate clustered annotations (all follow the leader)
        for cluster_id, indices in clusters.items():
            leader_resolution = self._resolve_cluster_leader(
                old_model, indices, resolver, new_model, old_layout, new_layout
            )

            for idx in indices:
                annotation = old_model.annotations[idx]
                if leader_resolution:
                    new_annotation = self._migrate_with_resolution(
                        annotation, leader_resolution, new_model, cluster_id
                    )
                    migrated_annotations.append(new_annotation)
                    report.add_migration(annotation, new_annotation, leader_resolution)

                    logger.debug(
                        f"Migrated clustered {annotation.annotation_type} "
                        f"(cluster={cluster_id}) with {leader_resolution.match_type} "
                        f"match (confidence={leader_resolution.confidence:.2f})"
                    )
                else:
                    report.add_orphan(annotation)
                    logger.warning(
                        f"Could not resolve cluster {cluster_id} "
                        f"({annotation.annotation_type} annotation)"
                    )

        # Migrate unclustered annotations individually
        for idx in unclustered:
            annotation = old_model.annotations[idx]
            resolved = resolver.resolve(
                annotation.anchor_context,
                old_model.full_text,
                new_model.full_text,
                old_layout,
                new_layout,
            )

            if resolved:
                new_annotation = self._migrate_with_resolution(
                    annotation, resolved, new_model, None
                )
                migrated_annotations.append(new_annotation)
                report.add_migration(annotation, new_annotation, resolved)
                logger.debug(
                    f"Migrated {annotation.annotation_type} with {resolved.match_type} "
                    f"match (confidence={resolved.confidence:.2f})"
                )
            else:
                report.add_orphan(annotation)
                logger.warning(f"Could not resolve {annotation.annotation_type} annotation")

        # Create new model with migrated annotations
        merged_model = DocumentModel(
            paragraphs=new_model.paragraphs,
            content_blocks=new_model.content_blocks,
            full_text=new_model.full_text,
            annotations=migrated_annotations,
            geometry=new_model.geometry,
            lines_per_page=new_model.lines_per_page,
            allow_paragraph_splitting=new_model.allow_paragraph_splitting,
        )

        logger.info(
            f"Migration complete: {len(migrated_annotations)} migrated, "
            f"{len(report.orphans)} orphaned (success rate: {report.success_rate:.1%})"
        )

        return MergeResult(merged_model=merged_model, report=report)

    def _build_layout_context(self, model: DocumentModel) -> LayoutContext | None:
        """Build layout context for a document model.

        Args:
            model: Document model to build layout for

        Returns:
            LayoutContext or None if geometry not available
        """
        from rock_paper_sync.layout import LayoutContext, TextAreaConfig

        if not model.geometry:
            return None

        return LayoutContext.from_text(
            model.full_text,
            use_font_metrics=True,
            config=TextAreaConfig(
                text_width=model.geometry.text_width,
                text_pos_x=model.geometry.text_pos_x,
                text_pos_y=model.geometry.text_pos_y,
            ),
        )

    def _resolve_cluster_leader(
        self,
        old_model: DocumentModel,
        indices: list[int],
        resolver: ContextResolver,
        new_model: DocumentModel,
        old_layout: LayoutContext | None,
        new_layout: LayoutContext | None,
    ) -> ResolvedAnchorContext | None:
        """Resolve all cluster members, return highest-confidence resolution.

        The leader is the annotation whose anchor resolves with highest
        confidence. All other cluster members will follow this resolution.

        Args:
            old_model: Source document model
            indices: Annotation indices in old_model.annotations
            resolver: ContextResolver instance
            new_model: Target document model
            old_layout: Layout context for old document
            new_layout: Layout context for new document

        Returns:
            Highest-confidence resolution, or None if no member resolves
        """
        resolutions: list[ResolvedAnchorContext] = []

        for idx in indices:
            anno = old_model.annotations[idx]
            resolved = resolver.resolve(
                anno.anchor_context,
                old_model.full_text,
                new_model.full_text,
                old_layout,
                new_layout,
            )
            if resolved:
                resolutions.append(resolved)

        if not resolutions:
            return None

        # Pick highest confidence, prefer better match types on tie
        match_type_priority = {"exact": 3, "fuzzy": 2, "diff_anchor": 1, "spatial": 0}
        return max(
            resolutions,
            key=lambda r: (r.confidence, match_type_priority.get(r.match_type, 0)),
        )

    def _migrate_with_resolution(
        self,
        annotation: DocumentAnnotation,
        resolution: ResolvedAnchorContext,
        new_model: DocumentModel,
        cluster_id: str | None = None,
    ) -> DocumentAnnotation:
        """Create migrated annotation using provided resolution.

        Args:
            annotation: Source annotation to migrate
            resolution: Resolved anchor context in new document
            new_model: Target document model
            cluster_id: Cluster ID to preserve (or None for unclustered)

        Returns:
            New DocumentAnnotation with updated anchor
        """
        from rock_paper_sync.annotations.document_model import (
            AnchorContext,
            DocumentAnnotation,
        )

        new_anchor = AnchorContext.from_text_span(
            new_model.full_text,
            resolution.start_offset,
            resolution.end_offset,
        )

        # Preserve Y-position hint for strokes (needed for page assignment)
        if (
            annotation.annotation_type == "stroke"
            and annotation.anchor_context.y_position_hint is not None
        ):
            new_anchor = AnchorContext(
                content_hash=new_anchor.content_hash,
                text_content=new_anchor.text_content,
                paragraph_index=new_anchor.paragraph_index,
                context_before=new_anchor.context_before,
                context_after=new_anchor.context_after,
                y_position_hint=annotation.anchor_context.y_position_hint,
                diff_anchor=new_anchor.diff_anchor,
            )

        return DocumentAnnotation(
            annotation_id=annotation.annotation_id,
            annotation_type=annotation.annotation_type,
            anchor_context=new_anchor,
            stroke_data=annotation.stroke_data,
            highlight_data=annotation.highlight_data,
            original_rm_block=annotation.original_rm_block,
            original_tree_node=annotation.original_tree_node,
            original_scene_group_item=annotation.original_scene_group_item,
            original_scene_tree_block=annotation.original_scene_tree_block,
            cluster_id=cluster_id or annotation.cluster_id,
            source_page_idx=annotation.source_page_idx,
        )
