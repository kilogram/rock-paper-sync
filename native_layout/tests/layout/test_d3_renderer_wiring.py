"""D3 wiring test: renderer layout == production engine, exactly.

Layer A of docs/DEVICE_REPLICA_DESIGN.md: the renderer must contain no
private layout math. This test asserts (with 0.0 tolerance) that the
renderer's computed text line positions and stroke anchor y values are
the SAME numbers the production engine produces, because they come from
the same code path (LayoutContext.offset_to_position / AnchorResolver).

Device-free: uses a recorded corpus .rm file, no device or network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import rmscene
from rmscene import SceneLineItemBlock

from native_layout.layout.device import DEFAULT_DEVICE
from native_layout.tools.rmlib.renderer import compute_anchor_points, compute_text_line_layout
from rock_paper_sync.coordinates import AnchorResolver, is_root_layer
from rock_paper_sync.rm_file_extractor import RmFileExtractor

CORPUS_DOC_DIR = (
    Path(__file__).resolve().parents[2]
    / "testdata"
    / "calibration"
    / "paper_pro_move"
    / "rm_files"
    / "01_wrapped_paragraphs"
)


@pytest.fixture(scope="module")
def rm_path() -> Path:
    """First page .rm file of corpus doc 01."""
    if not CORPUS_DOC_DIR.exists():
        pytest.skip(f"Corpus doc not found: {CORPUS_DOC_DIR}")
    rm_files = sorted(CORPUS_DOC_DIR.glob("*.rm"))
    if not rm_files:
        pytest.skip(f"No .rm files in {CORPUS_DOC_DIR}")
    return rm_files[0]


@pytest.fixture(scope="module")
def extractor(rm_path: Path) -> RmFileExtractor:
    return RmFileExtractor.from_path(rm_path)


class TestTextLineWiring:
    """Renderer text line positions == LayoutContext.offset_to_position."""

    def test_corpus_doc_has_text(self, extractor: RmFileExtractor) -> None:
        assert extractor.text_content, "Corpus doc 01 should contain text"

    def test_line_positions_exactly_match_engine(self, extractor: RmFileExtractor) -> None:
        layout_ctx = extractor.get_layout_context(DEFAULT_DEVICE)
        lines = compute_text_line_layout(layout_ctx)
        assert lines, "Expected at least one laid-out line"

        for start, end, doc_x, doc_y in lines:
            expected_x, expected_y = layout_ctx.offset_to_position(start)
            # Exact equality (0.0 tolerance): same code path, same floats.
            assert doc_x == expected_x, f"x mismatch at offset {start}"
            assert doc_y == expected_y, f"y mismatch at offset {start}"

    def test_line_pitch_from_device_geometry(self, extractor: RmFileExtractor) -> None:
        """Consecutive line y values step by exact engine multiples of line_height."""
        layout_ctx = extractor.get_layout_context(DEFAULT_DEVICE)
        lines = compute_text_line_layout(layout_ctx)
        origin_y = layout_ctx.origin[1]
        for start, _end, _x, doc_y in lines:
            # y must be origin + n * line_height for integer n, computed by
            # the engine itself (offset_to_position); recompute exactly.
            _, engine_y = layout_ctx.offset_to_position(start)
            assert doc_y == engine_y
            n = round((doc_y - origin_y) / layout_ctx.line_height)
            assert doc_y == origin_y + n * layout_ctx.line_height


class TestAnchorWiring:
    """Renderer anchor map y values == AnchorResolver (production path)."""

    def test_anchor_points_exactly_match_resolver(self, extractor: RmFileExtractor) -> None:
        resolver = AnchorResolver.from_extractor(extractor)
        anchor_points = compute_anchor_points(resolver, extractor.blocks)

        ctx = resolver.layout_context
        for parent_id, anchor in anchor_points.items():
            if is_root_layer(parent_id):
                assert anchor.x == 0.0 and anchor.y == 0.0
                continue
            assert anchor.char_offset is not None
            expected = resolver.get_anchor(parent_id)
            assert anchor == expected
            if anchor.char_offset < len(ctx.text_content):
                _, expected_y = ctx.offset_to_position(anchor.char_offset)
                # Exact equality (0.0 tolerance): same code path.
                assert anchor.y == expected_y

    def test_all_stroke_parents_covered_or_skipped(self, rm_path: Path) -> None:
        """compute_anchor_points covers every resolvable stroke parent."""
        with rm_path.open("rb") as f:
            blocks = list(rmscene.read_blocks(f))
        extractor = RmFileExtractor.from_blocks(blocks)
        resolver = AnchorResolver.from_extractor(extractor)
        anchor_points = compute_anchor_points(resolver, blocks)

        parent_ids = {b.parent_id for b in blocks if isinstance(b, SceneLineItemBlock)}
        for pid in parent_ids:
            expected = resolver.get_anchor(pid)
            if expected is None:
                assert pid not in anchor_points
            else:
                assert anchor_points[pid] == expected
