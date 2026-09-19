"""Classify ink clusters by their relationship to the word map.

Each cluster gets one classification:
- ``highlight``: cluster is a GlyphRange (device highlight), not a stroke cluster.
- ``appended``: ink on a trailing blank page or device-added page (D6).
- ``drawing``: everything else in Phase 4 (gestures are Phase 5).

Highlights come from ``SceneGlyphItemBlock`` (not stroke clusters).
Strokes on pages without ``redir`` or on trailing pages are ``appended``.
All other stroke clusters are ``drawing``.

Import restrictions (enforced by import-linter):
    Must NOT import ``weasyprint``, ``fitz``, ``pymupdf``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from rock_paper_sync.device_profile import DeviceProfile, device_to_pdf
from rock_paper_sync.ink.clusters import InkCluster
from rock_paper_sync.render.wordmap import Rect, WordMap

__all__ = [
    "Classification",
    "GlyphHighlight",
    "classify_cluster",
    "classify_glyph_highlights",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Classification:
    """Classification result for a stroke cluster.

    Attributes:
        kind: One of ``'appended'``, ``'drawing'``.
        cluster: The classified cluster.
        nearest_anchor_page: For ``'drawing'``: the redir page.
        nearest_anchor_rect: For ``'drawing'``: the bbox of the nearest paragraph
            on the same page, in PDF points.
    """

    kind: Literal["appended", "drawing"]
    cluster: InkCluster
    nearest_anchor_page: int | None = None
    nearest_anchor_rect: Rect | None = None


@dataclass(frozen=True, slots=True)
class GlyphHighlight:
    """A device highlight from SceneGlyphItemBlock.

    Not a stroke cluster — highlights are identified by their GlyphRange,
    not by spatial clustering.

    Attributes:
        text: The highlighted text from ``GlyphRange.text``.
        page: PDF page index (from cPages redir).
        device_rects: Rectangles in device coordinates.
    """

    text: str
    page: int
    device_rects: list[tuple[float, float, float, float]]  # (x, y, w, h)


def classify_cluster(
    cluster: InkCluster,
    word_map: WordMap,
    profile: DeviceProfile,
    content_page_count: int,
) -> Classification:
    """Classify a single stroke cluster.

    Args:
        cluster: The cluster to classify.
        word_map: Word map from the pushed PDF.
        profile: Device profile for coordinate transform.
        content_page_count: Number of content pages in the PDF
            (excluding trailing blank pages).

    Returns:
        ``Classification`` with kind and anchor information.
    """
    # Device-added pages (no redir) are always appended
    if cluster.redir is None:
        return Classification(kind="appended", cluster=cluster)

    # Trailing blank pages (redir >= content_page_count) are appended
    if cluster.redir >= content_page_count:
        return Classification(kind="appended", cluster=cluster)

    # Stroke cluster on a content page → drawing
    # Find the nearest paragraph box on this page
    page_redir = cluster.redir
    nearest_rect = _find_nearest_paragraph_box(cluster, word_map, profile, page_redir)

    return Classification(
        kind="drawing",
        cluster=cluster,
        nearest_anchor_page=page_redir,
        nearest_anchor_rect=nearest_rect,
    )


def _find_nearest_paragraph_box(
    cluster: InkCluster,
    word_map: WordMap,
    profile: DeviceProfile,
    page: int,
) -> Rect | None:
    """Find the paragraph box nearest to the cluster's centre on the given page.

    Groups tokens by their vertical position (y-band) to approximate paragraph boxes,
    then finds the one nearest to the cluster's centre.

    Returns:
        The nearest paragraph's bounding ``Rect`` in PDF points, or None.
    """
    # Transform cluster centre from device coords to PDF coords
    cx_dev = (cluster.bbox[0] + cluster.bbox[2]) / 2.0
    cy_dev = (cluster.bbox[1] + cluster.bbox[3]) / 2.0
    cx_pdf, cy_pdf = device_to_pdf(profile, cx_dev, cy_dev)

    # Collect all token rects on this page
    page_tokens: list[Rect] = []
    for pw in word_map.pages:
        if pw.page == page:
            for _, rect in pw.tokens:
                page_tokens.append(rect)

    if not page_tokens:
        return None

    # Group tokens into y-bands (paragraphs) using a tolerance
    # Tokens within 5pt of each other vertically are in the same band
    y_tolerance = 15.0
    bands: list[list[Rect]] = []
    sorted_tokens = sorted(page_tokens, key=lambda r: r.y0)

    current_band: list[Rect] = [sorted_tokens[0]]
    band_y_max = sorted_tokens[0].y1

    for rect in sorted_tokens[1:]:
        if rect.y0 <= band_y_max + y_tolerance:
            current_band.append(rect)
            band_y_max = max(band_y_max, rect.y1)
        else:
            bands.append(current_band)
            current_band = [rect]
            band_y_max = rect.y1
    bands.append(current_band)

    # Compute each band's bounding box and find the nearest one
    best_rect: Rect | None = None
    best_dist = float("inf")

    for band in bands:
        bx0 = min(r.x0 for r in band)
        by0 = min(r.y0 for r in band)
        bx1 = max(r.x1 for r in band)
        by1 = max(r.y1 for r in band)
        band_rect = Rect(x0=bx0, y0=by0, x1=bx1, y1=by1)

        # Distance from cluster centre to band centre
        band_cx = (bx0 + bx1) / 2.0
        band_cy = (by0 + by1) / 2.0
        dist = ((cx_pdf - band_cx) ** 2 + (cy_pdf - band_cy) ** 2) ** 0.5

        if dist < best_dist:
            best_dist = dist
            best_rect = band_rect

    return best_rect


def classify_glyph_highlights(
    rm_files: dict[str, object],
    cpages_entries: list[dict[str, object]],
    known_stroke_ids: frozenset[str],
) -> list[GlyphHighlight]:
    """Extract GlyphRange highlights from .rm files.

    Highlights are ``SceneGlyphItemBlock`` entries, not strokes.
    They are identified by their text content and rectangles, not
    by spatial clustering.

    Args:
        rm_files: Mapping of page UUID -> .rm file path (Path objects).
        cpages_entries: The ``cPages.pages`` list from ``.content``.
        known_stroke_ids: Already-processed stroke ids (used to skip
            highlights whose glyph item id is already recorded).

    Returns:
        List of ``GlyphHighlight`` objects.
    """
    from pathlib import Path

    from rmscene import SceneGlyphItemBlock

    from rock_paper_sync.rm_file_extractor import RmFileExtractor

    highlights: list[GlyphHighlight] = []

    for page_idx, cpage in enumerate(cpages_entries):
        page_id = str(cpage.get("id", ""))
        if not page_id:
            continue

        # Only content pages (with redir) can have highlights
        redir_raw = cpage.get("redir")
        if redir_raw is None:
            continue
        if isinstance(redir_raw, dict):
            redir = int(redir_raw.get("value", -1))
        elif isinstance(redir_raw, (int, float)):
            redir = int(redir_raw)
        else:
            continue

        rm_path = rm_files.get(page_id)
        if rm_path is None or (isinstance(rm_path, Path) and not rm_path.exists()):
            continue

        try:
            extractor = RmFileExtractor.from_path(rm_path)  # type: ignore[arg-type]
            # Walk raw blocks for SceneGlyphItemBlock
            for block in extractor.blocks:
                if isinstance(block, SceneGlyphItemBlock):
                    glyph = block.item.value
                    glyph_id = f"{block.item.item_id.part1}:{block.item.item_id.part2}"

                    # Skip if already processed
                    if glyph_id in known_stroke_ids:
                        continue

                    rects = [(r.x, r.y, r.w, r.h) for r in glyph.rectangles]
                    highlights.append(
                        GlyphHighlight(
                            text=glyph.text,
                            page=redir,
                            device_rects=rects,
                        )
                    )
        except Exception:
            logger.warning(f"Failed to extract highlights from page {page_id}", exc_info=True)

    logger.debug(f"Found {len(highlights)} glyph highlight(s)")
    return highlights
