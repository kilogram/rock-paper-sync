"""Spatial grouping of strokes for annotation migration.

A StrokeCluster represents a coherent user annotation: a margin note,
underlines on a sentence, a circled word, etc. Strokes in a cluster
move together when document content changes.

Key distinction from StrokeBundle:
- StrokeBundle = CRDT serialization unit (4 blocks per TreeNodeBlock)
- StrokeCluster = Semantic annotation unit (may span multiple bundles)

A cluster may contain strokes with DIFFERENT TreeNodeBlocks if they
were drawn at different times but form a logical unit.

Usage:
    # Extract clusters from .rm file
    index = SceneGraphIndex.from_file(rm_path)
    clusters = StrokeCluster.from_scene_index(index, page_text)

    # Migrate clusters to new document version
    for cluster in clusters:
        new_anchor = resolver.resolve(cluster.anchor, old_text, new_text)
        cluster.anchor = new_anchor

    # Serialize clusters for device
    for cluster in clusters:
        for block in cluster.to_rm_blocks():
            writer.write_block(block)
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .common.spatial import DEFAULT_CLUSTER_THRESHOLD, cluster_bboxes_kdtree
from .stroke import Stroke

if TYPE_CHECKING:
    from .document_model import AnchorContext
    from .scene_adapter.bundle import StrokeBundle
    from .scene_adapter.scene_index import SceneGraphIndex

logger = logging.getLogger(__name__)


@dataclass
class StrokeCluster:
    """Spatial grouping of strokes - moves as a unit when content changes.

    A cluster represents a coherent user annotation: a margin note,
    underlines on a sentence, a circled word, etc.

    Key distinction from StrokeBundle:
    - StrokeBundle = CRDT serialization unit (4 blocks per TreeNodeBlock)
    - StrokeCluster = Semantic annotation unit (may span multiple bundles)

    A cluster may contain strokes with DIFFERENT TreeNodeBlocks if they
    were drawn at different times but form a logical unit.

    Attributes:
        cluster_id: Unique identifier for this cluster
        strokes: List of Stroke objects in this cluster
        bounding_box: Combined bounding box of all strokes (x, y, w, h)
        anchor: Where this cluster belongs in document space

    Properties:
        bundles: List of unique StrokeBundles referenced by strokes
        center: Center point of the cluster's bounding box
        center_y: Vertical center (for paragraph matching)
    """

    cluster_id: str
    strokes: list[Stroke]
    bounding_box: tuple[float, float, float, float]  # (x, y, w, h)
    anchor: AnchorContext | None = None

    # Cached bundles - computed from strokes
    _bundles: list[StrokeBundle] = field(default_factory=list, repr=False)

    @property
    def center(self) -> tuple[float, float]:
        """Get the center point of the cluster's bounding box."""
        x, y, w, h = self.bounding_box
        return (x + w / 2, y + h / 2)

    @property
    def center_y(self) -> float:
        """Get the vertical center of the cluster."""
        return self.center[1]

    @property
    def bundles(self) -> list[StrokeBundle]:
        """Get unique StrokeBundles referenced by this cluster's strokes.

        Returns bundles in order of first appearance. Multiple strokes
        may share the same bundle (same TreeNodeBlock).
        """
        if self._bundles:
            return self._bundles

        # Collect unique bundles preserving order
        seen: set[tuple[int, int]] = set()
        bundles: list[StrokeBundle] = []
        for stroke in self.strokes:
            if stroke.bundle:
                key = (stroke.bundle.node_id.part1, stroke.bundle.node_id.part2)
                if key not in seen:
                    seen.add(key)
                    bundles.append(stroke.bundle)

        self._bundles = bundles
        return bundles

    def to_rm_blocks(self) -> list[Any]:
        """Serialize all strokes' CRDT blocks for device writing.

        Returns blocks from all bundles in correct order:
        1. For each bundle: SceneTreeBlock, TreeNodeBlock, SceneGroupItemBlock
        2. Then all SceneLineItemBlocks

        Returns:
            List of rmscene blocks ready for writing
        """
        blocks: list[Any] = []
        for bundle in self.bundles:
            blocks.extend(bundle.to_raw_blocks())
        return blocks

    @classmethod
    def _generate_cluster_id(cls, strokes: list[Stroke]) -> str:
        """Generate a stable ID for a cluster based on its strokes.

        Uses stroke IDs to create a deterministic hash that survives
        re-clustering of the same strokes.
        """
        stroke_ids = sorted(f"{s.stroke_id.part1}:{s.stroke_id.part2}" for s in strokes)
        content = "|".join(stroke_ids)
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    @classmethod
    def _compute_bounding_box(cls, strokes: list[Stroke]) -> tuple[float, float, float, float]:
        """Compute combined bounding box for a list of strokes."""
        if not strokes:
            return (0.0, 0.0, 0.0, 0.0)

        min_x = min(s.bounding_box[0] for s in strokes)
        min_y = min(s.bounding_box[1] for s in strokes)
        max_x = max(s.bounding_box[0] + s.bounding_box[2] for s in strokes)
        max_y = max(s.bounding_box[1] + s.bounding_box[3] for s in strokes)

        return (min_x, min_y, max_x - min_x, max_y - min_y)

    @classmethod
    def from_strokes(
        cls,
        strokes: list[Stroke],
        distance_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
    ) -> list[StrokeCluster]:
        """Create StrokeClusters by spatially clustering strokes.

        Uses KDTree-based proximity clustering to group nearby strokes
        into semantic units (margin notes, underlines, circled words, etc.).

        Args:
            strokes: List of Stroke objects to cluster
            distance_threshold: Maximum distance between stroke centers
                              for clustering (default: 80px)

        Returns:
            List of StrokeCluster objects
        """
        if not strokes:
            return []

        # Extract bounding boxes for clustering
        bboxes = [s.bounding_box for s in strokes]

        # Cluster using spatial proximity
        cluster_indices = cluster_bboxes_kdtree(bboxes, distance_threshold)

        # Build StrokeCluster objects
        clusters: list[StrokeCluster] = []
        for indices in cluster_indices:
            cluster_strokes = [strokes[i] for i in indices]
            cluster = cls(
                cluster_id=cls._generate_cluster_id(cluster_strokes),
                strokes=cluster_strokes,
                bounding_box=cls._compute_bounding_box(cluster_strokes),
            )
            clusters.append(cluster)

        logger.debug(
            f"Created {len(clusters)} cluster(s) from {len(strokes)} strokes "
            f"(threshold={distance_threshold}px)"
        )
        return clusters

    @classmethod
    def from_scene_index(
        cls,
        index: SceneGraphIndex,
        distance_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
    ) -> list[StrokeCluster]:
        """Extract StrokeClusters from a SceneGraphIndex.

        Combines:
        1. Stroke extraction with CRDT context
        2. Spatial clustering into semantic groups

        Args:
            index: SceneGraphIndex containing all blocks from an .rm file
            distance_threshold: Maximum distance for clustering

        Returns:
            List of StrokeCluster objects with full CRDT context
        """
        # Extract strokes with CRDT context
        strokes = Stroke.from_scene_index(index)

        if not strokes:
            return []

        # Cluster spatially
        return cls.from_strokes(strokes, distance_threshold)

    @classmethod
    def from_rm_file(
        cls,
        rm_path: Path,
        distance_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
    ) -> list[StrokeCluster]:
        """Extract StrokeClusters from an .rm file.

        Convenience method that loads the file and extracts clusters.

        Args:
            rm_path: Path to .rm file
            distance_threshold: Maximum distance for clustering

        Returns:
            List of StrokeCluster objects
        """
        from .scene_adapter.scene_index import SceneGraphIndex

        index = SceneGraphIndex.from_file(rm_path)
        return cls.from_scene_index(index, distance_threshold)

    def __str__(self) -> str:
        anchor_str = f"anchor={self.anchor}" if self.anchor else "no anchor"
        return (
            f"StrokeCluster({self.cluster_id[:8]}..., "
            f"{len(self.strokes)} strokes, "
            f"{len(self.bundles)} bundles, "
            f"{anchor_str})"
        )

    def __repr__(self) -> str:
        return (
            f"StrokeCluster(cluster_id={self.cluster_id!r}, "
            f"strokes=[{len(self.strokes)} items], "
            f"bounding_box={self.bounding_box})"
        )
