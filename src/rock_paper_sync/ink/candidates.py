"""Candidate stroke extraction for pull sync.

Filters device strokes to only those not yet recorded in the
``ink_strokes`` ledger, producing the candidate set for clustering.

Invariants relied upon:
    INV-I4: A consumed stroke never re-enters a candidate set.
            Enforcement: ``ink_strokes`` primary key; this module
            subtracts known stroke ids before returning.

Import restrictions (enforced by import-linter):
    Must NOT import ``weasyprint``, ``fitz``, ``pymupdf``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from rmscene import CrdtId, SceneLineItemBlock

from rock_paper_sync.annotations.domain.stroke import Stroke
from rock_paper_sync.rm_file_extractor import RmFileExtractor

__all__ = ["CandidateStroke", "extract_candidates"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CandidateStroke:
    """A device stroke not yet in the ink_strokes ledger.

    Attributes:
        stroke: Full stroke with CRDT context.
        page_index: cPages position (not redir value).
        redir: PDF page index from cPages ``redir``, or None for device-added pages.
    """

    stroke: Stroke
    page_index: int
    redir: int | None


def _stroke_id_str(cid: CrdtId) -> str:
    """Canonical string form of a CrdtId for ledger keys."""
    return f"{cid.part1}:{cid.part2}"


def extract_candidates(
    rm_files: dict[str, Path],
    cpages_entries: list[dict[str, object]],
    known_stroke_ids: frozenset[str],
) -> list[CandidateStroke]:
    """Extract candidate strokes from device .rm files.

    For each cPages entry, reads the corresponding .rm file, extracts
    all strokes, and filters out those already in the ink_strokes ledger.

    Args:
        rm_files: Mapping of page UUID → downloaded .rm file path.
        cpages_entries: The ``cPages.pages`` list from the ``.content`` file.
            Each entry has ``id`` and optionally ``redir``.
        known_stroke_ids: Set of ``"part1:part2"`` stroke id strings
            already in the ``ink_strokes`` table.

    Returns:
        List of ``CandidateStroke`` objects for strokes not in the ledger.
    """
    candidates: list[CandidateStroke] = []

    for page_idx, cpage in enumerate(cpages_entries):
        page_id = str(cpage.get("id", ""))
        if not page_id:
            logger.warning(f"cPages entry {page_idx} has no id, skipping")
            continue

        # Determine redir value
        redir_raw = cpage.get("redir")
        redir: int | None = None
        if redir_raw is not None:
            if isinstance(redir_raw, dict):
                redir = int(redir_raw.get("value", -1))
            elif isinstance(redir_raw, (int, float)):
                redir = int(redir_raw)

        # Find .rm file for this page
        rm_path = rm_files.get(page_id)
        if rm_path is None or not rm_path.exists():
            logger.debug(f"No .rm file for page {page_id} (index {page_idx}), skipping")
            continue

        # Extract strokes from the .rm file
        try:
            extractor = RmFileExtractor.from_path(rm_path)
            # Try full CRDT-aware extraction first
            index = extractor.get_scene_index()
            strokes = Stroke.from_scene_index(index)

            # Fallback: if no strokes found via bundles, extract raw
            # SceneLineItemBlock entries (handles synthetic .rm files
            # and simple device-created strokes without full CRDT tree)
            if not strokes:
                for block in extractor.blocks:
                    if isinstance(block, SceneLineItemBlock):
                        stroke = Stroke.from_line_block(block)
                        strokes.append(stroke)
        except Exception:
            logger.warning(f"Failed to extract strokes from {rm_path}", exc_info=True)
            continue

        # Filter to candidates (not in the ledger)
        for stroke in strokes:
            sid = _stroke_id_str(stroke.stroke_id)
            if sid not in known_stroke_ids:
                candidates.append(
                    CandidateStroke(
                        stroke=stroke,
                        page_index=page_idx,
                        redir=redir,
                    )
                )

    logger.debug(
        f"Extracted {len(candidates)} candidate stroke(s) "
        f"from {len(rm_files)} page(s) "
        f"({len(known_stroke_ids)} already known)"
    )
    return candidates
