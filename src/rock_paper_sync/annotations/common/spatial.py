"""Spatial matching utilities for annotation-to-paragraph mapping.

Provides common logic for matching annotations to text paragraphs based on
Y-coordinate proximity when text-based matching isn't available.

Also includes spatial clustering for grouping nearby annotations, with
support for efficient KDTree-based clustering and pluggable strategies
for future visual model integration.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from rock_paper_sync.annotations.core_types import StrokeData

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "cluster_by_proximity",
    "find_nearest_paragraph_by_y",
    "StrokeData",
    "cluster_bboxes_kdtree",
    "ClusteringStrategy",
    "KDTreeProximityStrategy",
    "VisualModelStrategy",
    "get_clustering_strategy",
]


def cluster_by_proximity(
    centers: list[tuple[float, float]],
    distance_threshold: float = 60.0,
) -> list[list[int]]:
    """Cluster points by Euclidean distance using connected components.

    This is a generic clustering algorithm that groups points within a distance
    threshold. Used for grouping nearby strokes (e.g., forming a word) so they
    move together when content changes.

    Algorithm:
    1. Each point is a node in a graph
    2. Nodes within distance_threshold are connected
    3. Clusters are connected components (found via DFS)

    Args:
        centers: List of (x, y) coordinates to cluster
        distance_threshold: Maximum Euclidean distance to consider points
                          as part of the same cluster (default: 60px)

    Returns:
        List of clusters, where each cluster is a list of indices into `centers`

    Example:
        >>> centers = [(0, 0), (10, 10), (100, 100), (105, 105)]
        >>> clusters = cluster_by_proximity(centers, distance_threshold=20)
        >>> # Returns: [[0, 1], [2, 3]] - two clusters
    """
    if not centers:
        return []

    if len(centers) == 1:
        return [[0]]

    n = len(centers)
    graph = defaultdict(list)

    # Build adjacency graph
    for i in range(n):
        cx_i, cy_i = centers[i]
        for j in range(i + 1, n):
            cx_j, cy_j = centers[j]
            distance = math.sqrt((cx_j - cx_i) ** 2 + (cy_j - cy_i) ** 2)

            if distance < distance_threshold:
                graph[i].append(j)
                graph[j].append(i)

    # Find connected components using DFS
    visited = set()
    clusters = []

    for i in range(n):
        if i not in visited:
            cluster_indices = []
            stack = [i]

            while stack:
                node = stack.pop()
                if node not in visited:
                    visited.add(node)
                    cluster_indices.append(node)
                    stack.extend(graph[node])

            clusters.append(cluster_indices)

    logger.debug(f"Clustered {n} points into {len(clusters)} cluster(s)")
    return clusters


def find_nearest_paragraph_by_y(
    annotation_y: float,
    markdown_blocks: list[Any],
    text_origin_y: float | None = None,
) -> int | None:
    """Find the nearest paragraph to an annotation by Y-coordinate.

    Uses Y-position matching to find the closest paragraph. Requires that
    markdown_blocks have `page_y_start` attribute set (see issue #5 for
    pagination metadata persistence implementation).

    Args:
        annotation_y: Y-coordinate of annotation (in absolute page coordinates)
        markdown_blocks: List of markdown content blocks
        text_origin_y: Optional text origin Y for coordinate transformation

    Returns:
        Index of nearest paragraph, or None if matching unavailable

    Note:
        All blocks must have valid `page_y_start` attribute. Blocks without
        this attribute will be skipped with a warning.
    """
    # Check if pagination data is available on ALL blocks
    # IMPORTANT: Must validate each block, not just the first one
    if not markdown_blocks:
        logger.debug("No markdown blocks provided for Y-position matching")
        return None

    if not hasattr(markdown_blocks[0], "page_y_start") or markdown_blocks[0].page_y_start is None:
        logger.debug(
            "Y-position matching unavailable: ContentBlock missing page_y_start attribute (see issue #5)"
        )
        return None

    # Find closest paragraph by Y position
    min_distance = float("inf")
    nearest_index = None

    for idx, md_block in enumerate(markdown_blocks):
        # Validate EACH block has page_y_start, not just the first
        if not hasattr(md_block, "page_y_start") or md_block.page_y_start is None:
            logger.warning(f"Y-position matching: Block {idx} missing page_y_start, skipping")
            continue

        block_y = md_block.page_y_start
        distance = abs(annotation_y - block_y)
        if distance < min_distance:
            min_distance = distance
            nearest_index = idx

    if nearest_index is not None:
        logger.debug(
            f"Y-position match: y={annotation_y:.1f} → paragraph {nearest_index} "
            f"(distance={min_distance:.1f})"
        )
    else:
        logger.warning("Could not find valid paragraph with page_y_start")

    return nearest_index


# =============================================================================
# Efficient Clustering
# =============================================================================


def cluster_bboxes_kdtree(
    bboxes: list[tuple[float, float, float, float]],
    distance_threshold: float = 80.0,
) -> list[list[int]]:
    """Cluster bounding boxes using KDTree for O(n log n) efficiency.

    Uses scipy.spatial.cKDTree for efficient neighbor queries, then finds
    connected components via DFS. This enables transitive chaining: if
    stroke A is near B, and B is near C, all three cluster together even
    if A and C are far apart.

    Args:
        bboxes: List of (x, y, width, height) bounding boxes
        distance_threshold: Maximum distance between bbox centers to cluster
                          (default: 80px, captures multi-line handwriting)

    Returns:
        List of clusters, where each cluster is a list of indices into bboxes

    Example:
        >>> bboxes = [(0, 0, 10, 10), (30, 0, 10, 10), (60, 0, 10, 10)]
        >>> clusters = cluster_bboxes_kdtree(bboxes, distance_threshold=40)
        >>> # Returns: [[0, 1, 2]] - all connected via chain
    """
    if not bboxes:
        return []

    if len(bboxes) == 1:
        return [[0]]

    try:
        from scipy.spatial import cKDTree
    except ImportError:
        logger.warning("scipy not available, falling back to O(n²) clustering")
        centers = [(x + w / 2, y + h / 2) for x, y, w, h in bboxes]
        return cluster_by_proximity(centers, distance_threshold)

    # Extract centroids
    centers = [(x + w / 2, y + h / 2) for x, y, w, h in bboxes]

    # Account for bbox sizes: expand search radius by max dimension
    # This ensures overlapping/nearby boxes are found
    max_dim = max(max(w, h) for _, _, w, h in bboxes)
    search_radius = distance_threshold + max_dim / 2

    # Build KDTree - O(n log n)
    tree = cKDTree(centers)

    # Query all neighbors within radius for each point - O(n log n + nk)
    # workers=-1 uses all CPU cores
    neighbor_lists = tree.query_ball_point(centers, r=search_radius, workers=-1)

    # Build adjacency graph - O(nk)
    graph: dict[int, set[int]] = defaultdict(set)
    for i, neighbors in enumerate(neighbor_lists):
        for j in neighbors:
            if i != j:
                graph[i].add(j)
                graph[j].add(i)

    # Find connected components via DFS - O(n + e)
    visited: set[int] = set()
    clusters: list[list[int]] = []

    for i in range(len(centers)):
        if i not in visited:
            cluster: list[int] = []
            stack = [i]
            while stack:
                node = stack.pop()
                if node not in visited:
                    visited.add(node)
                    cluster.append(node)
                    stack.extend(graph[node] - visited)
            clusters.append(cluster)

    logger.debug(
        f"KDTree clustered {len(bboxes)} bboxes into {len(clusters)} cluster(s) "
        f"(threshold={distance_threshold}px, search_radius={search_radius:.1f}px)"
    )
    return clusters


# =============================================================================
# Clustering Strategy Protocol and Implementations
# =============================================================================


@runtime_checkable
class ClusteringStrategy(Protocol):
    """Protocol for pluggable stroke clustering algorithms.

    Enables swapping between:
    - Spatial proximity (current default)
    - Visual model (future)
    - Hybrid approaches

    Example:
        >>> strategy = KDTreeProximityStrategy(distance_threshold=80.0)
        >>> strokes = [StrokeData(bbox=(0, 0, 10, 10)), ...]
        >>> clusters = strategy.cluster(strokes)
    """

    @property
    def name(self) -> str:
        """Strategy identifier (e.g., 'kdtree_proximity', 'visual_model')."""
        ...

    def cluster(self, strokes: list[StrokeData]) -> list[list[int]]:
        """Cluster strokes into semantic groups.

        Args:
            strokes: List of StrokeData with full stroke information

        Returns:
            List of clusters, each cluster is list of indices into strokes
        """
        ...


class KDTreeProximityStrategy:
    """Efficient spatial clustering using KDTree + connected components.

    Uses bounding box centroids for O(n log n) neighbor queries with
    transitive chaining via connected components.

    Attributes:
        distance_threshold: Maximum distance between bbox centers to cluster
    """

    def __init__(self, distance_threshold: float = 80.0):
        """Initialize with distance threshold.

        Args:
            distance_threshold: Maximum distance between bbox centers (default: 80px)
        """
        self.distance_threshold = distance_threshold

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "kdtree_proximity"

    def cluster(self, strokes: list[StrokeData]) -> list[list[int]]:
        """Cluster strokes using KDTree for efficient spatial indexing.

        Args:
            strokes: List of StrokeData (only bbox is used)

        Returns:
            List of clusters as indices into strokes
        """
        if not strokes:
            return []
        bboxes = [s.bbox for s in strokes]
        return cluster_bboxes_kdtree(bboxes, self.distance_threshold)


class VisualModelStrategy:
    """Placeholder for future visual model integration.

    Would use a trained model to identify semantic groups
    (words, sentences, diagrams) from stroke appearance.

    The model would have access to full stroke data including
    points, pressure, timestamps, and color for rich feature extraction.
    """

    def __init__(self, model_path: str | None = None):
        """Initialize with optional model path.

        Args:
            model_path: Path to trained model (not yet implemented)
        """
        self.model_path = model_path
        self._model: Any = None  # Lazy load

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "visual_model"

    def cluster(self, strokes: list[StrokeData]) -> list[list[int]]:
        """Cluster strokes using visual model.

        Has access to full stroke data: points, pressure, timestamps, color.

        Args:
            strokes: List of StrokeData with full stroke information

        Returns:
            List of clusters as indices into strokes

        Raises:
            NotImplementedError: Visual model not yet available
        """
        # Future implementation:
        # 1. Render strokes to image using points/pressure/color
        # 2. Run visual model to detect semantic groups
        # 3. Return cluster assignments
        raise NotImplementedError(
            "Visual model clustering not yet implemented. "
            "Use 'kdtree_proximity' strategy instead."
        )


# Default strategy instance
_default_clustering_strategy: ClusteringStrategy = KDTreeProximityStrategy()


def get_clustering_strategy(
    name: str = "kdtree_proximity",
    **kwargs: Any,
) -> ClusteringStrategy:
    """Get clustering strategy by name.

    Args:
        name: Strategy name ('kdtree_proximity' or 'visual_model')
        **kwargs: Strategy-specific parameters (e.g., distance_threshold)

    Returns:
        ClusteringStrategy instance

    Raises:
        ValueError: If strategy name is unknown

    Example:
        >>> strategy = get_clustering_strategy("kdtree_proximity", distance_threshold=100)
        >>> clusters = strategy.cluster(strokes)
    """
    strategies: dict[str, type] = {
        "kdtree_proximity": KDTreeProximityStrategy,
        "visual_model": VisualModelStrategy,
    }

    if name not in strategies:
        available = ", ".join(strategies.keys())
        raise ValueError(f"Unknown clustering strategy: {name!r}. Available: {available}")

    return strategies[name](**kwargs)
