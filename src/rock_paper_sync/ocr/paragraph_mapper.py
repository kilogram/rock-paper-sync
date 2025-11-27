"""Abstract interface for mapping annotation clusters to paragraphs.

This module provides an abstraction layer for determining which paragraph
an annotation cluster belongs to. The interface allows swapping between
different strategies (spatial overlap, vision models, etc.).
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from rock_paper_sync.annotations.common.text_extraction import RmTextBlock
from rock_paper_sync.ocr.protocol import BoundingBox
from rock_paper_sync.ocr.text_matching import match_rm_block_to_markdown
from rock_paper_sync.parser import ContentBlock

logger = logging.getLogger(__name__)


@dataclass
class AnnotationCluster:
    """A cluster of related annotations with a bounding box."""

    bbox: BoundingBox
    annotation_indices: list[int]  # Indices into original annotation list


class ParagraphMapper(ABC):
    """Abstract interface for mapping annotation clusters to paragraphs.

    This abstraction allows swapping between different mapping strategies:
    - Spatial overlap algorithms (current implementation)
    - Vision models (future enhancement)
    - Hybrid approaches
    """

    @abstractmethod
    def map_cluster_to_paragraph(
        self,
        cluster_bbox: BoundingBox,
        markdown_blocks: list[ContentBlock],
        rm_text_blocks: list[RmTextBlock],
    ) -> int | None:
        """Map an annotation cluster to a paragraph index.

        Args:
            cluster_bbox: Bounding box of the annotation cluster
            markdown_blocks: Parsed markdown content blocks
            rm_text_blocks: Text blocks extracted from .rm file

        Returns:
            Index of the paragraph this cluster belongs to, or None if no match
        """
        pass


class SpatialOverlapMapper(ParagraphMapper):
    """Map annotation clusters to paragraphs using spatial overlap algorithms.

    This implementation uses bounding box intersection and proximity to determine
    which text block (and therefore which paragraph) an annotation cluster is
    associated with.

    Algorithm:
    1. Calculate intersection area between cluster bbox and each text block bbox
    2. Score candidates by intersection area and vertical proximity
    3. Return the paragraph index of the best-scoring text block
    """

    def __init__(
        self,
        intersection_weight: float = 0.7,
        proximity_weight: float = 0.3,
        max_distance: float = 50.0,
    ):
        """Initialize the spatial overlap mapper.

        Args:
            intersection_weight: Weight for intersection area in scoring (0-1)
            proximity_weight: Weight for vertical proximity in scoring (0-1)
            max_distance: Maximum vertical distance to consider (in reMarkable units)
        """
        self.intersection_weight = intersection_weight
        self.proximity_weight = proximity_weight
        self.max_distance = max_distance

    def map_cluster_to_paragraph(
        self,
        cluster_bbox: BoundingBox,
        markdown_blocks: list[ContentBlock],
        rm_text_blocks: list[RmTextBlock],
    ) -> int | None:
        """Map cluster to paragraph using spatial overlap scoring."""
        if not rm_text_blocks or not markdown_blocks:
            logger.debug(
                f"Cannot map cluster: rm_text_blocks={len(rm_text_blocks)}, "
                f"markdown_blocks={len(markdown_blocks)}"
            )
            return None

        best_score = -1.0
        best_rm_block_idx = None

        logger.debug(
            f"Mapping cluster bbox: x={cluster_bbox.x:.1f}, y={cluster_bbox.y:.1f}, "
            f"w={cluster_bbox.width:.1f}, h={cluster_bbox.height:.1f}"
        )

        # Find best matching rm text block
        for idx, rm_block in enumerate(rm_text_blocks):
            score = self._score_overlap(cluster_bbox, rm_block)

            logger.debug(
                f"  Block {idx} (y=[{rm_block.y_start:.1f}, {rm_block.y_end:.1f}]): score={score:.3f}"
            )

            if score > best_score:
                best_score = score
                best_rm_block_idx = idx

        if best_rm_block_idx is None or best_score <= 0:
            logger.debug("✗ No text block overlap found")
            return None

        # Match rm block to markdown paragraph
        best_rm_block = rm_text_blocks[best_rm_block_idx]
        para_idx = self._match_rm_block_to_markdown(best_rm_block, markdown_blocks)

        logger.debug(
            f"✓ Matched to rm_block {best_rm_block_idx} (score={best_score:.3f}) "
            f"→ paragraph {para_idx}"
        )

        return para_idx

    def _score_overlap(self, cluster_bbox: BoundingBox, rm_block: RmTextBlock) -> float:
        """Score the overlap between a cluster and a text block.

        Args:
            cluster_bbox: Bounding box of annotation cluster
            rm_block: Text block from .rm file

        Returns:
            Score between 0 and 1 (higher is better match)
        """
        # Calculate vertical intersection
        cluster_y_min = cluster_bbox.y
        cluster_y_max = cluster_bbox.y + cluster_bbox.height
        block_y_min = rm_block.y_start
        block_y_max = rm_block.y_end

        # Intersection
        intersect_y_min = max(cluster_y_min, block_y_min)
        intersect_y_max = min(cluster_y_max, block_y_max)
        intersect_height = max(0, intersect_y_max - intersect_y_min)

        # Intersection score (normalized by cluster height)
        if cluster_bbox.height > 0:
            intersection_score = intersect_height / cluster_bbox.height
        else:
            intersection_score = 0.0

        # Vertical proximity score (distance from cluster center to block center)
        cluster_center_y = cluster_bbox.y + cluster_bbox.height / 2
        block_center_y = (block_y_min + block_y_max) / 2
        distance = abs(cluster_center_y - block_center_y)

        # Normalize distance to 0-1 (0 = far, 1 = close)
        proximity_score = max(0, 1 - (distance / self.max_distance))

        # Weighted combination
        total_score = (
            self.intersection_weight * intersection_score
            + self.proximity_weight * proximity_score
        )

        return total_score

    def _match_rm_block_to_markdown(
        self, rm_block: RmTextBlock, markdown_blocks: list[ContentBlock]
    ) -> int | None:
        """Match an rm text block to a markdown paragraph by content.

        Delegates to shared text matching utility.
        """
        return match_rm_block_to_markdown(rm_block, markdown_blocks)
