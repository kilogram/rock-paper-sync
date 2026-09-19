"""Spatial clustering of candidate strokes.

Groups candidate strokes into clusters using the existing
``cluster_bboxes_kdtree`` algorithm from ``annotations/common/spatial``.
Cluster id is sha256 over sorted member stroke ids, making it stable
across re-runs on the same set of strokes.

Import restrictions (enforced by import-linter):
    Must NOT import ``weasyprint``, ``fitz``, ``pymupdf``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from rock_paper_sync.annotations.common.spatial import cluster_bboxes_kdtree
from rock_paper_sync.ink.candidates import CandidateStroke

__all__ = ["InkCluster", "cluster_candidates"]

logger = logging.getLogger(__name__)


def _stroke_id_str(stroke: CandidateStroke) -> str:
    """Canonical string for a stroke id."""
    cid = stroke.stroke.stroke_id
    return f"{cid.part1}:{cid.part2}"


def _compute_cluster_id(stroke_ids: list[str]) -> str:
    """Deterministic cluster id from sorted stroke ids."""
    joined = "\n".join(sorted(stroke_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class InkCluster:
    """A group of spatially-related candidate strokes.

    Attributes:
        cluster_id: sha256 over sorted member stroke ids.
        strokes: The member candidate strokes.
        bbox: Union bounding box (x0, y0, x1, y1) in device coordinates.
        page_index: cPages position of the first stroke.
        redir: PDF page index from cPages ``redir``, or None for device-added pages.
    """

    cluster_id: str
    strokes: tuple[CandidateStroke, ...]
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    page_index: int
    redir: int | None


def cluster_candidates(
    candidates: list[CandidateStroke],
    distance_threshold: float = 80.0,
) -> list[InkCluster]:
    """Group candidate strokes into clusters by spatial proximity.

    Strokes on different pages are never clustered together. Within a page,
    uses ``cluster_bboxes_kdtree`` for O(n log n) clustering.

    Args:
        candidates: Candidate strokes from ``extract_candidates``.
        distance_threshold: Maximum distance between bbox centres to cluster.

    Returns:
        List of ``InkCluster`` objects, one per cluster.
    """
    if not candidates:
        return []

    # Group by (page_index, redir) to avoid cross-page clustering
    by_page: dict[int, list[CandidateStroke]] = {}
    for c in candidates:
        by_page.setdefault(c.page_index, []).append(c)

    clusters: list[InkCluster] = []

    for page_idx in sorted(by_page):
        page_strokes = by_page[page_idx]
        redir = page_strokes[0].redir  # all on same page share redir

        # Build bounding boxes for clustering
        bboxes = []
        for cs in page_strokes:
            x, y, w, h = cs.stroke.bounding_box
            bboxes.append((x, y, w, h))

        # Cluster
        groups = cluster_bboxes_kdtree(bboxes, distance_threshold)

        for group_indices in groups:
            group_strokes = [page_strokes[i] for i in group_indices]
            stroke_ids = [_stroke_id_str(s) for s in group_strokes]
            cluster_id = _compute_cluster_id(stroke_ids)

            # Compute union bbox
            all_bboxes = [s.stroke.bounding_box for s in group_strokes]
            x0 = min(bx for bx, _, _, _ in all_bboxes)
            y0 = min(by for _, by, _, _ in all_bboxes)
            x1 = max(bx + bw for bx, _, bw, _ in all_bboxes)
            y1 = max(by + bh for _, by, _, bh in all_bboxes)

            clusters.append(
                InkCluster(
                    cluster_id=cluster_id,
                    strokes=tuple(group_strokes),
                    bbox=(x0, y0, x1, y1),
                    page_index=page_idx,
                    redir=redir,
                )
            )

    logger.debug(f"Clustered {len(candidates)} candidate(s) into {len(clusters)} cluster(s)")
    return clusters
