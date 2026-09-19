"""Shared helpers for building synthetic .rm files in tests.

Moved from ``tests/record_replay/test_pdf_substrate_calibration.py``
so both the calibration tests and the pull integration test can use them.
"""

from __future__ import annotations

import io
import uuid as uuid_mod

import rmscene
from rmscene import (
    AuthorIdsBlock,
    CrdtId,
    CrdtSequenceItem,
    LwwValue,
    MigrationInfoBlock,
    PageInfoBlock,
    SceneGlyphItemBlock,
    SceneGroupItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)
from rmscene.scene_items import GlyphRange, Group, Line, Pen, PenColor, Point, Rectangle


def build_synthetic_rm(
    glyph_text: str,
    device_rects: list[Rectangle],
) -> bytes:
    """Build a minimal valid .rm file with one GlyphRange highlight.

    Args:
        glyph_text: Highlight text.
        device_rects: Rectangles in device coordinates.

    Returns:
        .rm file bytes.
    """
    author_uuid = uuid_mod.UUID("00000000-0000-4000-8000-000000000001")

    blocks = [
        AuthorIdsBlock(author_uuids={1: author_uuid}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
        SceneTreeBlock(
            tree_id=CrdtId(0, 11),
            node_id=CrdtId(0, 0),
            is_update=True,
            parent_id=CrdtId(0, 1),
        ),
        TreeNodeBlock(Group(node_id=CrdtId(0, 1))),
        TreeNodeBlock(
            Group(
                node_id=CrdtId(0, 11),
                label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"),
            )
        ),
        SceneGroupItemBlock(
            parent_id=CrdtId(0, 1),
            item=CrdtSequenceItem(
                item_id=CrdtId(0, 13),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=CrdtId(0, 11),
            ),
        ),
        SceneGlyphItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, 20),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=GlyphRange(
                    start=0,
                    length=len(glyph_text),
                    text=glyph_text,
                    color=PenColor.HIGHLIGHT,
                    rectangles=device_rects,
                ),
            ),
        ),
    ]

    buf = io.BytesIO()
    rmscene.write_blocks(buf, blocks)
    return buf.getvalue()


def build_synthetic_rm_with_stroke(
    glyph_text: str | None,
    device_rects: list[Rectangle] | None,
    stroke_line: Line,
    *,
    extra_stroke_lines: list[Line] | None = None,
) -> bytes:
    """Build a minimal .rm with optional highlight AND stroke(s).

    Args:
        glyph_text: Highlight text, or None to skip the highlight.
        device_rects: Highlight rectangles, or None to skip.
        stroke_line: A ``Line`` instance for the first stroke.
        extra_stroke_lines: Additional stroke lines (optional).

    Returns:
        .rm file bytes.
    """
    author_uuid = uuid_mod.UUID("00000000-0000-4000-8000-000000000001")

    blocks: list[object] = [
        AuthorIdsBlock(author_uuids={1: author_uuid}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
        SceneTreeBlock(
            tree_id=CrdtId(0, 11),
            node_id=CrdtId(0, 0),
            is_update=True,
            parent_id=CrdtId(0, 1),
        ),
        TreeNodeBlock(Group(node_id=CrdtId(0, 1))),
        TreeNodeBlock(
            Group(
                node_id=CrdtId(0, 11),
                label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"),
            )
        ),
        SceneGroupItemBlock(
            parent_id=CrdtId(0, 1),
            item=CrdtSequenceItem(
                item_id=CrdtId(0, 13),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=CrdtId(0, 11),
            ),
        ),
    ]

    if glyph_text is not None and device_rects is not None:
        blocks.append(
            SceneGlyphItemBlock(
                parent_id=CrdtId(0, 11),
                item=CrdtSequenceItem(
                    item_id=CrdtId(1, 20),
                    left_id=CrdtId(0, 0),
                    right_id=CrdtId(0, 0),
                    deleted_length=0,
                    value=GlyphRange(
                        start=0,
                        length=len(glyph_text),
                        text=glyph_text,
                        color=PenColor.HIGHLIGHT,
                        rectangles=device_rects,
                    ),
                ),
            )
        )

    # Add the primary stroke
    blocks.append(
        SceneLineItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, 30),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=stroke_line,
            ),
        )
    )

    # Add extra strokes with unique ids
    for i, extra in enumerate(extra_stroke_lines or []):
        blocks.append(
            SceneLineItemBlock(
                parent_id=CrdtId(0, 11),
                item=CrdtSequenceItem(
                    item_id=CrdtId(1, 40 + i),
                    left_id=CrdtId(0, 0),
                    right_id=CrdtId(0, 0),
                    deleted_length=0,
                    value=extra,
                ),
            )
        )

    buf = io.BytesIO()
    rmscene.write_blocks(buf, blocks)
    return buf.getvalue()


def build_stroke_only_rm(stroke_line: Line, stroke_id: int = 30) -> bytes:
    """Build a minimal .rm file with just one stroke (no highlight).

    Args:
        stroke_line: The stroke ``Line``.
        stroke_id: CrdtId part2 for the stroke's item_id.

    Returns:
        .rm file bytes.
    """
    author_uuid = uuid_mod.UUID("00000000-0000-4000-8000-000000000001")

    blocks: list[object] = [
        AuthorIdsBlock(author_uuids={1: author_uuid}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
        SceneTreeBlock(
            tree_id=CrdtId(0, 11),
            node_id=CrdtId(0, 0),
            is_update=True,
            parent_id=CrdtId(0, 1),
        ),
        TreeNodeBlock(Group(node_id=CrdtId(0, 1))),
        TreeNodeBlock(
            Group(
                node_id=CrdtId(0, 11),
                label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"),
            )
        ),
        SceneGroupItemBlock(
            parent_id=CrdtId(0, 1),
            item=CrdtSequenceItem(
                item_id=CrdtId(0, 13),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=CrdtId(0, 11),
            ),
        ),
        SceneLineItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, stroke_id),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=stroke_line,
            ),
        ),
    ]

    buf = io.BytesIO()
    rmscene.write_blocks(buf, blocks)
    return buf.getvalue()


def build_two_highlights_rm(
    glyph_text: str,
    rects_a: list[Rectangle],
    rects_b: list[Rectangle],
    *,
    item_id_a: int = 20,
    item_id_b: int = 21,
) -> bytes:
    """Build a minimal .rm with two GlyphRange highlights over the same phrase.

    Each highlight gets a distinct ``item_id`` (CrdtId part2) so they
    produce separate ledger rows.

    Args:
        glyph_text: Highlight text (same for both).
        rects_a: Rectangles for the first highlight.
        rects_b: Rectangles for the second highlight.
        item_id_a: CrdtId part2 for highlight A (default 20).
        item_id_b: CrdtId part2 for highlight B (default 21).

    Returns:
        .rm file bytes.
    """
    author_uuid = uuid_mod.UUID("00000000-0000-4000-8000-000000000001")

    blocks: list[object] = [
        AuthorIdsBlock(author_uuids={1: author_uuid}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
        SceneTreeBlock(
            tree_id=CrdtId(0, 11),
            node_id=CrdtId(0, 0),
            is_update=True,
            parent_id=CrdtId(0, 1),
        ),
        TreeNodeBlock(Group(node_id=CrdtId(0, 1))),
        TreeNodeBlock(
            Group(
                node_id=CrdtId(0, 11),
                label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"),
            )
        ),
        SceneGroupItemBlock(
            parent_id=CrdtId(0, 1),
            item=CrdtSequenceItem(
                item_id=CrdtId(0, 13),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=CrdtId(0, 11),
            ),
        ),
        SceneGlyphItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, item_id_a),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=GlyphRange(
                    start=0,
                    length=len(glyph_text),
                    text=glyph_text,
                    color=PenColor.HIGHLIGHT,
                    rectangles=rects_a,
                ),
            ),
        ),
        SceneGlyphItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, item_id_b),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=GlyphRange(
                    start=0,
                    length=len(glyph_text),
                    text=glyph_text,
                    color=PenColor.HIGHLIGHT,
                    rectangles=rects_b,
                ),
            ),
        ),
    ]

    buf = io.BytesIO()
    rmscene.write_blocks(buf, blocks)
    return buf.getvalue()


def make_line(
    points: list[tuple[float, float]],
    *,
    color: PenColor = PenColor.BLACK,
    pen: Pen = Pen.FINELINER_1,
) -> Line:
    """Create a ``Line`` from a list of (x, y) tuples.

    All points get default pressure/width/speed/direction.
    """
    return Line(
        color=color,
        tool=pen,
        points=[Point(x=x, y=y, speed=0, direction=0, width=2, pressure=100) for x, y in points],
        thickness_scale=1.0,
        starting_length=0.0,
        move_id=None,
    )
