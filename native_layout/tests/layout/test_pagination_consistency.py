"""Pagination consistency tests: page fill vs engine line counts.

These pin the fix for the Phase 4a forensics finding (docs/bugs/
STROKE_SESSION_2026-07-05_FORENSICS.md §1b): the paginator closed pages far
below capacity because block line "estimates" carried phantom padding
(+1 per header, HR counted as 2, code blocks +2) that the renderer never
emits, and the header keep-together threshold was 10 lines (wasting up to 9
lines per page).

Invariants asserted for several document shapes:

(a) Per-page line accounting is exact: the sum of ``estimate_block_lines``
    over a page's blocks equals the engine's line count for that page's
    rendered text (blocks joined with a single newline, exactly as
    ``RemarkableGenerator.blocks_to_text_items`` renders them).

(b) Every non-final page fills to more than ``lines_per_page - X`` where
    X = HEADER_ORPHAN_THRESHOLD_LINES. Derivation: with paragraph splitting
    enabled, the ONLY rule allowed to close a page early is header orphan
    prevention, which breaks when fewer than HEADER_ORPHAN_THRESHOLD_LINES
    lines remain — so at most HEADER_ORPHAN_THRESHOLD_LINES - 1 lines can be
    left unused, i.e. fill >= lines_per_page - (X - 1) > lines_per_page - X.
    No other slack is permitted.

(c) Page text concatenation round-trips the document (whitespace-normalized,
    because ``split_for_pages`` strips the inter-chunk space at the wrap
    point when a paragraph is split across pages).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native_layout.layout import (
    DEFAULT_DEVICE,
    HEADER_ORPHAN_THRESHOLD_LINES,
    ContentPaginator,
    WordWrapLayoutEngine,
)
from native_layout.layout.paginator import estimate_block_lines
from rock_paper_sync.parser import BlockType, ContentBlock, parse_content

_REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = _REPO_ROOT / "tests" / "record_replay" / "fixtures"

PLAIN_PROSE = "\n\n".join(
    f"This is paragraph number {i} with enough words that it wraps onto several "
    "display lines when laid out at the calibrated layout width, giving the "
    "paginator realistic prose to fill pages with instead of trivial "
    "single-line fragments that never exercise word wrapping at all."
    for i in range(1, 16)
)

HEADINGS_AND_PROSE = "\n\n".join(
    f"## Section {i}\n\nSection {i} body text long enough to wrap across "
    "multiple lines of the page so that header placement interacts with "
    "real content instead of empty sections, exercising the orphan rule "
    "at various fill levels of the page as sections accumulate."
    for i in range(1, 13)
)

LISTS = "# Shopping\n\n" + "\n".join(
    f"- List item {i} with a longer clause appended so some items wrap "
    "to a second display line while others remain short"
    for i in range(1, 40)
)


def _engine() -> WordWrapLayoutEngine:
    return WordWrapLayoutEngine.from_geometry(DEFAULT_DEVICE, use_font_metrics=True)


def _render_block_text(block: ContentBlock) -> str:
    """Mirror RemarkableGenerator.blocks_to_text_items rendering of a block."""
    if block.type == BlockType.HORIZONTAL_RULE:
        return ""  # placeholder empty line
    if block.type == BlockType.LIST_ITEM:
        return f"• {block.text}"
    return block.text


def _page_text(page: list[ContentBlock]) -> str:
    return "\n".join(_render_block_text(b) for b in page)


def _doc_cases() -> list[tuple[str, str, bool]]:
    """(id, markdown, allow_paragraph_splitting) cases."""
    comprehensive = (FIXTURE_DIR / "test_comprehensive_strokes.md").read_text()
    return [
        ("plain_prose", PLAIN_PROSE, True),
        ("headings_prose", HEADINGS_AND_PROSE, True),
        ("lists", LISTS, True),
        ("comprehensive_fixture", comprehensive, True),
        # Atomic mode still must satisfy (a) and (c); (b) is only asserted
        # for splitting mode where paragraphs cannot legitimately leave gaps.
        ("comprehensive_fixture_atomic", comprehensive, False),
    ]


@pytest.mark.parametrize(
    "doc_id,markdown,allow_split",
    _doc_cases(),
    ids=[c[0] for c in _doc_cases()],
)
class TestPaginationConsistency:
    def _paginate(self, markdown: str, allow_split: bool):
        engine = _engine()
        blocks = parse_content(markdown)
        paginator = ContentPaginator(
            layout_engine=engine,
            lines_per_page=DEFAULT_DEVICE.lines_per_page,
            allow_paragraph_splitting=allow_split,
        )
        return engine, blocks, paginator.paginate(blocks)

    def test_page_line_sum_matches_engine_count(self, doc_id, markdown, allow_split):
        """(a) block-line accounting == engine's count of the rendered page text."""
        engine, _, pages = self._paginate(markdown, allow_split)
        for i, page in enumerate(pages):
            if not page:
                continue
            block_sum = sum(estimate_block_lines(b, engine) for b in page)
            rendered = _page_text(page)
            engine_count = len(engine.calculate_line_breaks(rendered, engine.text_width))
            assert block_sum == engine_count, (
                f"{doc_id} page {i}: block-line sum {block_sum} != engine count {engine_count}"
            )

    def test_nonfinal_pages_fill_to_capacity(self, doc_id, markdown, allow_split):
        """(b) non-final pages fill above lines_per_page - X.

        X = HEADER_ORPHAN_THRESHOLD_LINES: header orphan prevention is the only
        documented rule allowed to close a page early when paragraph splitting
        is enabled, and it can waste at most X - 1 lines (it fires only when
        fewer than X lines remain). No additional slack is allowed.
        """
        if not allow_split:
            pytest.skip("atomic paragraphs may legitimately leave larger gaps")
        engine, _, pages = self._paginate(markdown, allow_split)
        lpp = DEFAULT_DEVICE.lines_per_page
        x = HEADER_ORPHAN_THRESHOLD_LINES
        for i, page in enumerate(pages[:-1]):
            fill = sum(estimate_block_lines(b, engine) for b in page)
            assert fill > lpp - x, (
                f"{doc_id} non-final page {i} fill {fill} <= {lpp - x} "
                f"(lines_per_page={lpp}, keep-together allowance X={x})"
            )
            assert fill <= lpp, f"{doc_id} page {i} overfilled: {fill} > {lpp}"

    def test_page_concatenation_roundtrips_document(self, doc_id, markdown, allow_split):
        """(c) concatenated page text == parsed document text (normalized)."""
        engine, blocks, pages = self._paginate(markdown, allow_split)
        original = " ".join(" ".join(_render_block_text(b).split()) for b in blocks)
        paged = " ".join(" ".join(_render_block_text(b).split()) for page in pages for b in page)
        assert paged == original, f"{doc_id}: page concatenation lost or altered text"


def test_comprehensive_fixture_page_count_and_fill():
    """The forensics fixture now paginates near capacity: 2 pages, first full."""
    engine = _engine()
    blocks = parse_content((FIXTURE_DIR / "test_comprehensive_strokes.md").read_text())
    pages = ContentPaginator(
        layout_engine=engine,
        lines_per_page=DEFAULT_DEVICE.lines_per_page,
        allow_paragraph_splitting=False,
    ).paginate(blocks)
    fills = [sum(estimate_block_lines(b, engine) for b in p) for p in pages]
    assert len(pages) == 2, f"expected 2 pages, got {len(pages)} with fills {fills}"
    assert fills[0] == DEFAULT_DEVICE.lines_per_page  # 32/32: page 1 exactly full
