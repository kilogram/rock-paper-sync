"""Anchor dispersion guards — the 2026-07-05 stacking failure class.

In the recorded device session (docs/bugs/STROKE_SESSION_2026-07-05_FORENSICS.md),
a content edit invalidated every stroke's verbatim anchor text; each bundle was
resolved INDEPENDENTLY and all fell through to the same coarse fallback offset:
page 0 collapsed from 5 distinct anchors to 1 (char 267), piling all strokes
onto one line. Sibling strokes of one glyph could also resolve to different
anchors (p1: node 866 -> 163 while its siblings -> 152), splitting glyphs.

StrokeHandler.resolve_page_anchor_offsets() makes both impossible:
  * one resolution per original device anchor (cluster preservation),
  * a second group can never silently land on an already-claimed offset.

These tests reconstruct the failure shape with the real resolver (no mocks of
the strategy logic): near-identical underscore-gap paragraphs whose anchor
text was rewritten, exactly like the trip-2 edit.
"""

from dataclasses import dataclass, field

import pytest
from rmscene.tagged_block_common import CrdtId

from rock_paper_sync.annotations.document_model import AnchorContext
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.layout.device import DEFAULT_DEVICE

pytestmark = pytest.mark.native_layout

TEXT_BASE = 16


@dataclass
class _Group:
    node_id: object
    anchor_id: object = None
    anchor_origin_x: object = None


@dataclass
class _Value:
    value: object = None


@dataclass
class _TreeNode:
    group: _Group = field(default_factory=lambda: _Group(node_id=None))


def _anno(anno_id: str, anchor_part2: int, anchor_text: str, y_hint: float):
    tn = _TreeNode(
        _Group(
            node_id=CrdtId(2, 500 + anchor_part2),
            anchor_id=_Value(CrdtId(1, anchor_part2)),
        )
    )

    class _Anno:
        annotation_id = anno_id
        original_tree_node = tn
        anchor_context = AnchorContext(
            content_hash="x",
            text_content=anchor_text,
            y_position_hint=y_hint,
        )

    return _Anno()


# Trip-2-shaped page: the edit renamed every gap label, so the stored anchor
# texts below no longer occur verbatim, and the page is dominated by
# near-identical underscore paragraphs (non-unique fallback targets).
PAGE_TEXT = (
    "Comprehensive Stroke Test\n"
    "MODIFIED GAP A: ____________________\n"
    "Use normal width, default color (black).\n"
    "MODIFIED GAP B: ____________________\n"
    "MODIFIED GAP C: ____________________\n"
    "This should remain unchanged through all modifications.\n"
)


def _stale(label: str) -> str:
    return f"Write {label} here: ______________________"  # rewritten by the edit


def test_distinct_anchors_never_collapse_to_one_offset():
    """Five bundles with five distinct original anchors and stale anchor text
    (the exact trip-2 shape) must NOT converge on a single offset."""
    handler = StrokeHandler()
    annos = [
        _anno(f"a{i}", part2, _stale(chr(65 + i)), y_hint=300.0 + 90 * i)
        for i, part2 in enumerate([219 - 190, 60, 90, 120, 150])
    ]
    resolved = handler.resolve_page_anchor_offsets(annos, PAGE_TEXT, DEFAULT_DEVICE)
    assert len(resolved) == 5
    distinct = set(resolved.values())
    assert len(distinct) == 5, (
        f"anchor collapse: 5 distinct original anchors resolved to only "
        f"{len(distinct)} offset(s): {sorted(distinct)}"
    )


def test_sibling_bundles_of_one_anchor_stay_together():
    """Bundles sharing one original device anchor (one glyph's strokes) must
    all receive the SAME offset — the split-'t' failure mode."""
    handler = StrokeHandler()
    a1 = _anno("s1", 70, "unchanged through all", y_hint=500.0)
    a2 = _anno("s2", 70, "unchanged through all", y_hint=505.0)
    a2.anchor_context = AnchorContext(
        content_hash="x",
        # Slightly different stored context for the sibling — per-bundle
        # resolution could send it elsewhere; grouping must not.
        text_content="remain unchanged",
        y_position_hint=505.0,
    )
    resolved = handler.resolve_page_anchor_offsets([a1, a2], PAGE_TEXT, DEFAULT_DEVICE)
    assert resolved["s1"] == resolved["s2"]


def test_unique_anchor_text_resolves_normally():
    handler = StrokeHandler()
    a = _anno("u1", 40, "remain unchanged", y_hint=400.0)
    resolved = handler.resolve_page_anchor_offsets([a], PAGE_TEXT, DEFAULT_DEVICE)
    assert resolved["u1"] == PAGE_TEXT.index("remain unchanged")


def test_collapse_fallback_prefers_original_offset(caplog):
    """When a second group would land on a claimed offset, it falls back to
    its ORIGINAL semantic offset (part2 - TEXT_BASE) and logs an error."""
    handler = StrokeHandler()
    # Identical stale anchor text and same y-hint region: both groups resolve
    # to the same coarse target.
    a1 = _anno("c1", TEXT_BASE + 30, _stale("X"), y_hint=310.0)
    a2 = _anno("c2", TEXT_BASE + 95, _stale("X"), y_hint=312.0)
    import logging

    with caplog.at_level(logging.ERROR):
        resolved = handler.resolve_page_anchor_offsets([a1, a2], PAGE_TEXT, DEFAULT_DEVICE)
    assert resolved["c1"] != resolved["c2"], "collapse was not prevented"
    assert resolved["c2"] == 95, "loser group must keep its original semantic offset"
    assert any("collapse prevented" in r.message for r in caplog.records)
