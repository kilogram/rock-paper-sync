"""Positional certification of highlight & stroke anchoring vs device-pulled .rm.

LAYOUT_VERIFICATION_PLAN.md Phase 5. Every assertion here compares an
engine/production prediction against a number the DEVICE wrote into a pulled
.rm file — never engine-vs-itself, never counts, never anchor text alone.
Runs fully offline in default CI (no rmfakecloud).

Ground truth files (real device captures, 2025-12-06 firmware era):
- highlight_reanchor/phases/*: SceneGlyphItemBlock rects for highlights the
  device laid out after markdown edits and reanchoring.
- stroke_reanchor/phases/*: TreeNodeBlock anchors (anchor_id.part2,
  anchor_origin_x) the device computed for handwritten strokes.

What these captures certify:
- X: engine char->x must match device highlight box x (minus the 3.4 px pad)
  and device-written stroke anchor_origin_x. Known divergences are the T4
  accumulated glyph-width drift (grows with distance into the line, up to
  ~7.4 px here) and one W2 wrap divergence; each is pinned as a strict xfail
  with its measured delta so the Phase 6 font-metric fit flips them.
- Y: adjacent-line highlight rects within one paragraph are 44.4-45.6 px
  apart in these old captures — confirming the Phase 4a 45.55 px line pitch
  on independent documents. (Cross-paragraph y-gaps include inter-paragraph
  spacing the engine does not model offline — spec B1, Phase 8 probe — and
  are deliberately not asserted.)
- Anchor identity: anchors shared between the phase-3 and golden captures
  must agree on anchor_origin_x and anchored text (the stroke-29 silent
  re-bind failure mode).
"""

import math
from bisect import bisect_right
from pathlib import Path

import pytest
from rmscene.scene_stream import SceneGlyphItemBlock, TreeNodeBlock

from rock_paper_sync.layout.device import DEFAULT_DEVICE
from rock_paper_sync.rm_file_extractor import RmFileExtractor

TESTDATA = Path(__file__).parent / "testdata"

HL_PHASE3 = (
    TESTDATA
    / "highlight_reanchor/phases/phase_3_post_modification/rm_files"
    / "f14298cf-4ad5-4eba-bb1a-a3e76b605bf4.rm"
)
HL_GOLDEN = (
    TESTDATA
    / "highlight_reanchor/phases/phase_4_golden_native/rm_files"
    / "6d927560-27a0-4f96-a450-1f2217bc5293.rm"
)
ST_PHASE3 = (
    TESTDATA
    / "stroke_reanchor/phases/phase_3_post_modification/rm_files"
    / "83b3eb97-d399-4807-aab5-e854c2ae5a3a.rm"
)
ST_GOLDEN = (
    TESTDATA
    / "stroke_reanchor/phases/phase_4_golden_native/rm_files"
    / "2384b9eb-be9f-4038-a864-5acd632cf3ce.rm"
)

EPS_PX = 2.0  # same tolerance as the corpus differential suite (D1)
HL_LEFT_PAD = 3.4  # device highlight box extends 3.4 px left of the glyph
END_OF_DOC = 281474976710655

# Measured engine-vs-device x divergences, pinned as strict xfails.
# T4: accumulated per-glyph width error, grows with distance into the line;
# resolved by the Phase 6 font-metric fit — when that lands these start
# passing and MUST be removed. W2: word-wrap boundary divergence (device and
# engine break the line at different words), resolved with T4/W2 fit.
KNOWN_DIVERGENT_HL = {
    # (phase dir, highlight text) -> (measured dx, spec item)
    ("phase_3_post_modification", "target"): (-5.0, "T4"),
    ("phase_3_post_modification", "bottom"): (+62.7, "W2 wrap divergence"),
    ("phase_3_post_modification", "cross line"): (+9.5, "T4"),
    ("phase_4_golden_native", "cross"): (+9.7, "T4"),
}
KNOWN_DRIFT_ANCHORS = {
    # part2 -> measured dx (identical in phase-3 and golden captures)
    163: +4.3,
    166: +5.6,
    228: +2.8,
    229: +3.0,
    233: +4.3,
    235: +5.0,
    236: +6.9,
    237: +7.0,
    238: +7.4,
}


def _extractor(path: Path) -> RmFileExtractor:
    return RmFileExtractor.from_path(path)


def _highlights(ex: RmFileExtractor) -> list:
    return [b for b in ex.blocks if isinstance(b, SceneGlyphItemBlock)]


def _anchored_tree_nodes(ex: RmFileExtractor) -> list:
    out = []
    for b in ex.blocks:
        if isinstance(b, TreeNodeBlock):
            aid = getattr(b.group, "anchor_id", None)
            if aid is not None and aid.value is not None:
                out.append(b)
    return out


def _hl_offset(ex: RmFileExtractor, glyph) -> int | None:
    """Character offset of a highlight: prefer the stored start, else locate
    the highlight text in the document (device-created highlights omit start
    and are only usable when their text is unique in the document)."""
    v = glyph.item.value
    if getattr(v, "start", None) is not None:
        return v.start
    text = getattr(v, "text", None)
    if not text or ex.text_content.count(text) != 1:
        return None
    return ex.text_content.index(text)


def _line_index(ctx, offset: int) -> int:
    return bisect_right(list(ctx.line_breaks), offset)


# =============================================================================
# Highlights: device rect x == engine-predicted glyph x - pad
# =============================================================================


def _hl_cases():
    for path in (HL_PHASE3, HL_GOLDEN):
        ex = _extractor(path)
        ctx = ex.get_layout_context(DEFAULT_DEVICE)
        for g in _highlights(ex):
            v = g.item.value
            off = _hl_offset(ex, g)
            if off is None:
                continue
            yield path.parent.parent.name, v.text, off, ctx, v.rectangles


HL_CASES = list(_hl_cases())
assert len(HL_CASES) >= 5, "certification is vacuous: highlight testdata missing"


@pytest.mark.parametrize(
    "phase,text,off,ctx,rects",
    HL_CASES,
    ids=[f"{p}-{t}" for p, t, *_ in HL_CASES],
)
def test_highlight_rect_x_matches_engine(phase, text, off, ctx, rects, request):
    """Device highlight box x == engine char x - 3.4 px pad (first rect)."""
    if (phase, text) in KNOWN_DIVERGENT_HL:
        dx, spec = KNOWN_DIVERGENT_HL[(phase, text)]
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=f"{spec}: measured Δx={dx:+.1f} px (Phase 6 metric fit resolves)",
            )
        )
    x, _ = ctx.offset_to_position(off)
    predicted = x - HL_LEFT_PAD
    delta = predicted - rects[0].x
    assert abs(delta) < EPS_PX, (
        f"{phase} {text!r}: predicted box x={predicted:.1f}, "
        f"device x={rects[0].x:.1f}, Δ={delta:+.1f} px"
    )


def test_adjacent_line_highlight_pitch_matches_engine():
    """Device y-delta between highlight rects on ADJACENT LINES of the same
    paragraph == engine line pitch (45.55). Independent confirmation of the
    Phase 4a refit on documents outside the calibration corpus: these device
    captures show 44.4 / 45.6 px — a 57.0 pitch would miss by >11 px.

    Sources: the multi-rect 'cross line' highlight (phase 3) whose two rects
    sit on consecutive lines, and the golden 'cross' + 'line' highlights on
    consecutive lines.
    """
    checked = 0

    ex3 = _extractor(HL_PHASE3)
    for g in _highlights(ex3):
        v = g.item.value
        if len(v.rectangles) >= 2:
            dy = v.rectangles[1].y - v.rectangles[0].y
            delta = dy - DEFAULT_DEVICE.line_height
            assert abs(delta) < EPS_PX, (
                f"multi-rect highlight {v.text!r}: device adjacent-line Δy={dy:.1f}, "
                f"engine pitch={DEFAULT_DEVICE.line_height}, Δ={delta:+.1f} px"
            )
            checked += 1

    exg = _extractor(HL_GOLDEN)
    ctxg = exg.get_layout_context(DEFAULT_DEVICE)
    by_text = {g.item.value.text: g.item.value for g in _highlights(exg)}
    if "cross" in by_text and "line" in by_text:
        off_cross = exg.text_content.index("cross line")
        line_gap = _line_index(ctxg, off_cross + len("cross ")) - _line_index(ctxg, off_cross)
        dy = by_text["line"].rectangles[0].y - by_text["cross"].rectangles[0].y
        delta = dy - line_gap * DEFAULT_DEVICE.line_height
        assert abs(delta) < EPS_PX, (
            f"golden cross->line: device Δy={dy:.1f}, engine {line_gap} line(s) "
            f"* {DEFAULT_DEVICE.line_height} px, Δ={delta:+.1f} px"
        )
        checked += 1

    assert checked >= 2, "certification is vacuous: adjacent-line highlight pairs missing"


# =============================================================================
# Strokes: device-written anchor_origin_x == engine-predicted char x
# =============================================================================


def _stroke_cases():
    seen = set()
    for path in (ST_PHASE3, ST_GOLDEN):
        ex = _extractor(path)
        ctx = ex.get_layout_context(DEFAULT_DEVICE)
        for tn in _anchored_tree_nodes(ex):
            aid = tn.group.anchor_id.value
            if aid.part2 == END_OF_DOC:
                continue
            off = ex.crdt_to_char.get(aid)
            if off is None:
                continue
            key = (path.parent.parent.name, aid.part2)
            if key in seen:  # duplicate anchors (two nodes, same char) add nothing
                continue
            seen.add(key)
            yield *key, off, ctx, tn.group.anchor_origin_x.value


ST_CASES = list(_stroke_cases())
assert len(ST_CASES) >= 10, "certification is vacuous: stroke anchor testdata missing"


@pytest.mark.parametrize(
    "phase,part2,off,ctx,device_ox",
    ST_CASES,
    ids=[f"{p}-a{p2}" for p, p2, *_ in ST_CASES],
)
def test_stroke_anchor_origin_x_matches_engine(phase, part2, off, ctx, device_ox, request):
    """The device computed anchor_origin_x for each stroke's anchor character;
    the engine must predict the same x. This is the production stroke-placement
    x input (AnchorResolver), certified against a device-signed value."""
    if part2 in KNOWN_DRIFT_ANCHORS:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=f"T4 metric drift: measured Δx={KNOWN_DRIFT_ANCHORS[part2]:+.1f} px "
                "(accumulated glyph-width error; Phase 6 font fit resolves)",
            )
        )
    x, _ = ctx.offset_to_position(off)
    delta = x - device_ox
    assert abs(delta) < EPS_PX, (
        f"{phase} anchor part2={part2} off={off}: engine x={x:.1f}, "
        f"device anchor_origin_x={device_ox:.1f}, Δ={delta:+.1f} px"
    )


def test_stroke_anchor_identity_stable_across_device_reconciliations():
    """Anchors present in both phase-3 and golden captures must agree on
    anchor_origin_x AND on the anchored text (the stroke-29 failure mode:
    an anchor that silently re-binds to different characters)."""
    ex3, exg = _extractor(ST_PHASE3), _extractor(ST_GOLDEN)

    def anchor_map(ex):
        m = {}
        for tn in _anchored_tree_nodes(ex):
            aid = tn.group.anchor_id.value
            if aid.part2 == END_OF_DOC:
                continue
            off = ex.crdt_to_char.get(aid)
            word = ex.text_content[off : off + 8] if off is not None else None
            m[aid.part2] = (tn.group.anchor_origin_x.value, word)
        return m

    m3, mg = anchor_map(ex3), anchor_map(exg)
    shared = sorted(set(m3) & set(mg))
    assert len(shared) >= 3, f"certification is vacuous: only {len(shared)} shared anchors"
    for part2 in shared:
        ox3, w3 = m3[part2]
        oxg, wg = mg[part2]
        assert math.isclose(ox3, oxg, abs_tol=EPS_PX), (
            f"anchor part2={part2}: origin_x drifted {ox3:.1f} -> {oxg:.1f} "
            "across device reconciliations"
        )
        assert w3 == wg, (
            f"anchor part2={part2}: anchored text changed {w3!r} -> {wg!r} "
            "(anchor re-bound to different characters)"
        )
