"""Annotation storage, clustering, and retrieval.

This module provides AnnotationStore, which encapsulates:
- Storage of DocumentAnnotation instances
- Spatial clustering of strokes by proximity
- Cluster-aware iteration and retrieval
- Anchor resolution in document text

Extracted from DocumentModel to achieve single responsibility:
- DocumentModel: Content representation and page projection
- AnnotationStore: Annotation storage, clustering, and retrieval
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rock_paper_sync.annotations.document_model import (
        AnchorContext,
        DocumentAnnotation,
    )

logger = logging.getLogger(__name__)


@dataclass
class AnnotationStore:
    """Store, cluster, and provide access to document annotations.

    Responsibilities:
    - Storage: Hold DocumentAnnotation instances
    - Clustering: Assign cluster_ids to spatially-related strokes
    - Retrieval: Provide cluster-aware and type-filtered access
    - Resolution: Find anchor positions in document text

    Design:
    - Immutable after construction (cluster assignment happens once)
    - Text reference passed in, not owned (avoids content duplication)
    - Clustering happens during from_annotations() construction

    Example:
        # Create from extracted annotations
        store = AnnotationStore.from_annotations(annotations, full_text)

        # Access clusters for OCR
        for cluster in store.get_clusters():
            process_cluster(cluster)

        # Filter by type
        for stroke in store.strokes():
            render_stroke(stroke)

        # Find anchor position
        pos = store.find_anchor_position(anchor)
    """

    _annotations: list[DocumentAnnotation] = field(default_factory=list)
    _text_ref: str = ""  # Reference to document text (for anchor resolution)

    @classmethod
    def from_annotations(
        cls,
        annotations: list[DocumentAnnotation],
        full_text: str,
        cluster_strokes: bool = True,
    ) -> AnnotationStore:
        """Create store from extracted annotations.

        Args:
            annotations: List of DocumentAnnotation instances
            full_text: Document text for anchor resolution
            cluster_strokes: Whether to assign cluster IDs to strokes (default True)

        Returns:
            AnnotationStore with clustered annotations
        """
        store = cls(_annotations=list(annotations), _text_ref=full_text)

        if cluster_strokes:
            store._assign_stroke_clusters()

        return store

    @classmethod
    def empty(cls, full_text: str = "") -> AnnotationStore:
        """Create empty store (for new documents without annotations).

        Args:
            full_text: Document text for anchor resolution (optional)

        Returns:
            Empty AnnotationStore
        """
        return cls(_annotations=[], _text_ref=full_text)

    # =========================================================================
    # Read-only access
    # =========================================================================

    @property
    def annotations(self) -> list[DocumentAnnotation]:
        """All annotations (read-only copy)."""
        return list(self._annotations)

    def __len__(self) -> int:
        """Number of annotations in store."""
        return len(self._annotations)

    def __iter__(self) -> Iterator[DocumentAnnotation]:
        """Iterate over all annotations."""
        return iter(self._annotations)

    def __bool__(self) -> bool:
        """True if store has any annotations."""
        return len(self._annotations) > 0

    # =========================================================================
    # Clustering
    # =========================================================================

    def _assign_stroke_clusters(self) -> None:
        """Assign cluster IDs to spatially-related stroke annotations.

        Uses KDTree-based proximity clustering with DEFAULT_CLUSTER_THRESHOLD.
        Strokes on different pages are never grouped in the same cluster.
        """
        from rock_paper_sync.annotations.common.spatial import (
            DEFAULT_CLUSTER_THRESHOLD,
            KDTreeProximityStrategy,
        )

        # Get stroke annotations with valid stroke_data, grouped by source page
        # Strokes on different pages should NEVER be in the same cluster
        strokes_by_page: dict[int | None, list[tuple[int, DocumentAnnotation]]] = {}
        for i, anno in enumerate(self._annotations):
            if anno.annotation_type == "stroke" and anno.stroke_data:
                page_idx = anno.source_page_idx
                strokes_by_page.setdefault(page_idx, []).append((i, anno))

        if not strokes_by_page:
            return

        total_clusters = 0
        total_strokes = 0

        # Cluster strokes separately for each source page
        strategy = KDTreeProximityStrategy(distance_threshold=DEFAULT_CLUSTER_THRESHOLD)
        for page_idx, stroke_annos in strokes_by_page.items():
            total_strokes += len(stroke_annos)

            if len(stroke_annos) < 2:
                continue  # Need at least 2 strokes to form a cluster

            strokes = [anno.stroke_data for _, anno in stroke_annos]
            clusters = strategy.cluster(strokes)

            # Assign cluster IDs (only for actual clusters with >1 member)
            for cluster_indices in clusters:
                if len(cluster_indices) > 1:
                    cluster_id = str(uuid.uuid4())[:8]
                    for idx in cluster_indices:
                        anno_idx, _ = stroke_annos[idx]
                        self._annotations[anno_idx].cluster_id = cluster_id
                    total_clusters += 1

        logger.debug(
            f"Assigned {total_clusters} clusters to {total_strokes} stroke annotations "
            f"across {len(strokes_by_page)} pages"
        )

    def get_clusters(self) -> list[list[DocumentAnnotation]]:
        """Get annotations grouped by cluster_id.

        Returns a list of annotation clusters. Each cluster is a list of
        DocumentAnnotation objects that should be processed together.
        Unclustered annotations are returned as single-element lists.

        Used by both OCR processing and annotation reanchoring.

        Returns:
            List of annotation clusters (list of lists)
        """
        clusters: dict[str, list[DocumentAnnotation]] = {}
        unclustered: list[DocumentAnnotation] = []

        for anno in self._annotations:
            if anno.cluster_id:
                clusters.setdefault(anno.cluster_id, []).append(anno)
            else:
                unclustered.append(anno)

        # Return multi-annotation clusters + single-annotation "clusters"
        result = list(clusters.values())
        result.extend([[a] for a in unclustered])
        return result

    def get_cluster(self, cluster_id: str) -> list[DocumentAnnotation]:
        """Get all annotations in a specific cluster.

        Args:
            cluster_id: The cluster ID to retrieve

        Returns:
            List of annotations in that cluster (empty if not found)
        """
        return [a for a in self._annotations if a.cluster_id == cluster_id]

    # =========================================================================
    # Type-filtered iteration
    # =========================================================================

    def strokes(self) -> Iterator[DocumentAnnotation]:
        """Iterate over stroke annotations only."""
        return (a for a in self._annotations if a.annotation_type == "stroke")

    def highlights(self) -> Iterator[DocumentAnnotation]:
        """Iterate over highlight annotations only."""
        return (a for a in self._annotations if a.annotation_type == "highlight")

    # =========================================================================
    # Anchor Resolution
    # =========================================================================

    def find_anchor_position(self, anchor: AnchorContext) -> int | None:
        """Find character position of an anchor in the document text.

        Tries exact match first, then falls back to diff anchor resolution.

        Args:
            anchor: The anchor context to resolve

        Returns:
            Character position in document text, or None if not found
        """
        # Try exact match first
        pos = self._text_ref.find(anchor.text_content)
        if pos != -1:
            return pos

        # Try diff anchor
        if anchor.diff_anchor:
            span = anchor.diff_anchor.resolve_in(self._text_ref)
            if span:
                return span[0]

        return None

    # =========================================================================
    # Utility methods
    # =========================================================================

    def with_text_ref(self, full_text: str) -> AnnotationStore:
        """Create a new store with updated text reference.

        Useful when merging annotations into a new document with different text.

        Args:
            full_text: New document text for anchor resolution

        Returns:
            New AnnotationStore with same annotations but different text_ref
        """
        return AnnotationStore(_annotations=list(self._annotations), _text_ref=full_text)
