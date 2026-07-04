"""Offline tests for the Phase 2 calibration-corpus extractor.

These exercise the device-independent half of ``tools/calibration/extract_profile.py``:
source-markdown sentinel parsing, highlight→sentinel correlation, and the
derived measurements. No device or recorded ``.rm`` files are needed — device
highlights are fabricated — so this runs in default CI (P1: iterate locally).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from calibration.extract_profile import (  # noqa: E402
    GlyphHighlight,
    HighlightRect,
    build_sentinel_records,
    derive_measurements,
    parse_sentinel_sources,
)

CORPUS = REPO_ROOT / "tests" / "record_replay" / "testdata" / "calibration" / "paper_pro_move"
SRC = CORPUS / "src"


@pytest.fixture(scope="module")
def sources():
    return parse_sentinel_sources(SRC)


def test_all_sentinels_present(sources):
    """The corpus advertises ZEBRA01..ZEBRA40 plus the T5BASE probe."""
    zebra = {s for s in sources if s.startswith("ZEBRA")}
    assert zebra == {f"ZEBRA{i:02d}" for i in range(1, 41)}
    assert "T5BASE" in sources


def test_block_classification(sources):
    """Sentinels are classified by the block they live in."""
    # Headings doc: ZEBRA11..ZEBRA16 are H1..H6 respectively.
    for lvl, name in enumerate([f"ZEBRA{i}" for i in range(11, 17)], start=1):
        s = sources[name]
        assert s.block_type == "heading", name
        assert s.heading_level == lvl, name
    # Lists doc: nested bullets/ordered reach three levels (0,1,2).
    assert sources["ZEBRA18"].block_type == "list"
    assert sources["ZEBRA20"].list_level == 2
    # Code doc: sentinels inside the fence are code.
    assert sources["ZEBRA28"].block_type == "code"
    # Prose paragraphs are body.
    assert sources["ZEBRA01"].block_type == "body"


def test_char_offsets_are_within_plain_line(sources):
    """char_offset points at the sentinel inside the stripped line text."""
    for name, s in sources.items():
        if s.char_offset is not None:
            assert s.plain_line[s.char_offset:s.char_offset + len(name)] == name


def _fabricate_highlights(sources) -> list[GlyphHighlight]:
    """Plausible device highlights: incrementing Y, taller heading rects."""
    hs = []
    y = 234.0
    for name, s in sorted(sources.items()):
        h = 87.0 if s.block_type == "heading" else 57.0
        x = -375.0 + (s.list_level or 0) * 30.0
        hs.append(GlyphHighlight(text=name, rects=[HighlightRect(x=x, y=y, width=120.0, height=h)], doc=s.doc))
        y += h
    return hs


def test_correlation_matches_all_when_complete(sources):
    hs = _fabricate_highlights(sources)
    records, warnings = build_sentinel_records(sources, hs)
    assert len(records) == len(sources)
    assert warnings == []


def test_missing_highlight_is_reported(sources):
    hs = [h for h in _fabricate_highlights(sources) if h.text != "ZEBRA05"]
    records, warnings = build_sentinel_records(sources, hs)
    assert len(records) == len(sources) - 1
    assert any("ZEBRA05" in w and "MISSING" in w for w in warnings)


def test_derived_measurements(sources):
    hs = _fabricate_highlights(sources)
    records, _ = build_sentinel_records(sources, hs)
    m = derive_measurements(records, CORPUS)

    assert m["line_height_px"]["body"] == 57.0
    assert m["line_height_px"]["heading"] == 87.0
    # Per-level heading heights present for all six levels.
    assert set(m["heading_line_height_px"]) == {f"h{i}" for i in range(1, 7)}
    # List indentation increases with nesting level.
    indents = m["list_indent_x_by_level"]
    assert indents["level0"] < indents["level1"] < indents["level2"]
    # T5 has no stroke in the fabricated data → explicit null with reason.
    assert m["t5_baseline_offset"]["value"] is None
