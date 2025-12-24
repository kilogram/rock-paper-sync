"""Spatial grouping of strokes for annotation migration.

A StrokeCluster represents a group of spatially related strokes that should
move together when document content changes. Examples include:
- Margin notes (multiple strokes forming handwritten text)
- Underlines (single or multiple strokes under a word/sentence)
- Circled words or annotations
- Diagrams or sketches near a paragraph

Key insight: Spatial grouping is NOT the same as CRDT grouping. Strokes
in a cluster may have different CRDT parents (TreeNodeBlocks), but they
should move together because they're visually/semantically related.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .stroke import Stroke

if TYPE_CHECKING:
    from .document_model import AnchorContext


@dataclass
class StrokeCluster:
    """Spatial grouping of strokes - moves as a unit when context changes.

    A cluster contains strokes that are spatially close and should be
    treated as a single annotation unit. When document content changes,
    the entire cluster moves together based on its anchor context.

    Strokes in a cluster may have DIFFERENT CRDT parents (TreeNodeBlocks).
    The cluster serialization handles deduplication of parent blocks.

    Attributes:
        cluster_id: Unique identifier for this cluster
        strokes: List of Stroke objects in this cluster
        anchor: Where this cluster belongs in document context (set during merge)

    Example:
        # Load clusters from an rm file
        clusters = StrokeCluster.from_rm_file(rm_path)

        for cluster in clusters:
            print(f"Cluster {cluster.cluster_id[:8]}...")
            print(f"  {len(cluster.strokes)} strokes")
            print(f"  Bounding box: {cluster.bounding_box}")
            print(f"  Center Y: {cluster.center_y:.1f}")
    """

    cluster_id: str
    strokes: list[Stroke]
    anchor: AnchorContext | None = None

    # Cached bounding box (computed lazily)
    _bounding_box: tuple[float, float, float, float] | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def bounding_box(self) -> tuple[float, float, float, float]:
        """Get combined bounding box of all strokes (x, y, w, h)."""
        if self._bounding_box is None:
            if not self.strokes:
                self._bounding_box = (0.0, 0.0, 0.0, 0.0)
            else:
                min_x = float("inf")
                min_y = float("inf")
                max_x = float("-inf")
                max_y = float("-inf")

                for stroke in self.strokes:
                    x, y, w, h = stroke.bounding_box
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x + w)
                    max_y = max(max_y, y + h)

                self._bounding_box = (min_x, min_y, max_x - min_x, max_y - min_y)
        return self._bounding_box

    @property
    def center_y(self) -> float:
        """Get vertical center of the cluster."""
        x, y, w, h = self.bounding_box
        return y + h / 2

    @property
    def center(self) -> tuple[float, float]:
        """Get center point (x, y) for matching."""
        x, y, w, h = self.bounding_box
        return (x + w / 2, y + h / 2)

    @property
    def node_ids(self) -> set[tuple[int, int]]:
        """Get unique CRDT node IDs in this cluster."""
        return {(s.node_id.part1, s.node_id.part2) for s in self.strokes}

    @property
    def is_complete(self) -> bool:
        """Check if all strokes have complete CRDT structure."""
        return all(s.is_complete for s in self.strokes)

    def to_rm_blocks(self) -> list:
        """Serialize all strokes' CRDT blocks with deduplication.

        Parent blocks (TreeNodeBlock, SceneTreeBlock, SceneGroupItemBlock)
        are deduplicated since multiple strokes may share the same parent.

        Returns blocks in correct order for serialization:
        1. All unique SceneTreeBlocks
        2. All unique TreeNodeBlocks
        3. All unique SceneGroupItemBlocks
        4. All SceneLineItemBlocks
        """
        # Track which parent blocks we've seen
        seen_nodes: set[tuple[int, int]] = set()

        scene_trees = []
        tree_nodes = []
        scene_group_items = []
        line_blocks = []

        for stroke in self.strokes:
            node_key = (stroke.node_id.part1, stroke.node_id.part2)

            # Add parent blocks only if we haven't seen this node
            if node_key not in seen_nodes:
                seen_nodes.add(node_key)
                if stroke.scene_tree:
                    scene_trees.append(stroke.scene_tree)
                if stroke.tree_node:
                    tree_nodes.append(stroke.tree_node)
                if stroke.scene_group_item:
                    scene_group_items.append(stroke.scene_group_item)

            # Always add the line block
            line_blocks.append(stroke.line_block)

        # Return in correct order for serialization
        return scene_trees + tree_nodes + scene_group_items + line_blocks

    @classmethod
    def from_strokes(
        cls,
        strokes: list[Stroke],
        cluster_id: str | None = None,
        anchor: AnchorContext | None = None,
    ) -> StrokeCluster:
        """Create a cluster from a list of strokes.

        Args:
            strokes: List of Stroke objects to include
            cluster_id: Optional cluster ID (generated if not provided)
            anchor: Optional anchor context for document positioning

        Returns:
            StrokeCluster containing the strokes
        """
        return cls(
            cluster_id=cluster_id or str(uuid.uuid4()),
            strokes=list(strokes),
            anchor=anchor,
        )

    @classmethod
    def from_rm_file(
        cls,
        rm_path: Path | str,
        distance_threshold: float = 80.0,
    ) -> list[StrokeCluster]:
        """Extract stroke clusters from an .rm file using spatial clustering.

        Uses KDTree-based clustering to group spatially close strokes.
        Strokes within distance_threshold of each other (transitively)
        form a single cluster.

        Args:
            rm_path: Path to .rm file
            distance_threshold: Maximum distance between stroke centers
                              to include in same cluster (default: 80px)

        Returns:
            List of StrokeCluster objects

        Example:
            clusters = StrokeCluster.from_rm_file("page.rm")
            for cluster in clusters:
                print(f"Cluster with {len(cluster.strokes)} strokes")
        """
        from .common.spatial import cluster_bboxes_kdtree

        # Extract all strokes with CRDT structure
        strokes = Stroke.all_from_rm_file(rm_path)

        if not strokes:
            return []

        # Get bounding boxes for clustering
        bboxes = [s.bounding_box for s in strokes]

        # Cluster by spatial proximity
        cluster_indices = cluster_bboxes_kdtree(bboxes, distance_threshold)

        # Create StrokeCluster for each cluster
        clusters = []
        for indices in cluster_indices:
            cluster_strokes = [strokes[i] for i in indices]
            clusters.append(cls.from_strokes(cluster_strokes))

        return clusters

    @classmethod
    def from_scene_index(
        cls,
        index,  # SceneGraphIndex
        distance_threshold: float = 80.0,
    ) -> list[StrokeCluster]:
        """Extract stroke clusters from a SceneGraphIndex.

        Args:
            index: SceneGraphIndex containing parsed blocks
            distance_threshold: Maximum distance for clustering

        Returns:
            List of StrokeCluster objects
        """
        from .common.spatial import cluster_bboxes_kdtree

        # Extract all strokes with CRDT structure
        strokes = Stroke.all_from_scene_index(index)

        if not strokes:
            return []

        # Get bounding boxes for clustering
        bboxes = [s.bounding_box for s in strokes]

        # Cluster by spatial proximity
        cluster_indices = cluster_bboxes_kdtree(bboxes, distance_threshold)

        # Create StrokeCluster for each cluster
        clusters = []
        for indices in cluster_indices:
            cluster_strokes = [strokes[i] for i in indices]
            clusters.append(cls.from_strokes(cluster_strokes))

        return clusters

    def with_anchor(self, anchor: AnchorContext) -> StrokeCluster:
        """Return a new cluster with the given anchor context.

        This is used during merge to update the cluster's document position.

        Args:
            anchor: New anchor context for document positioning

        Returns:
            New StrokeCluster with updated anchor (strokes are shared)
        """
        return StrokeCluster(
            cluster_id=self.cluster_id,
            strokes=self.strokes,  # Share stroke references
            anchor=anchor,
        )
