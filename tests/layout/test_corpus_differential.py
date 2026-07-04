"""Phase 3 differential tests: engine predictions vs device ground truth.

These compare ``WordWrapLayoutEngine`` predictions against the measured
highlight rectangles in the Phase 2 calibration corpus
(``tests/record_replay/testdata/calibration/paper_pro_move/profile.json``),
implementing spec items D1 (rect match) and D2 (exact line breaks) from
``docs/LAYOUT_SPEC.md``.

They run offline in default CI: the device round-trip already happened once
(Phase 2); this module only reads the checked-in ``profile.json`` and the
source markdown. Per principle P7 (``docs/LAYOUT_PRINCIPLES.md``) the aspirational
tolerances stay at their spec values and *known* divergences are marked
``xfail(strict=True)`` with the measured delta in the reason, rather than being
hidden by loosened tolerances.

Findings pinned here (see ``docs/LAYOUT_SPEC.md`` for status wording):

* Body visual line pitch is **45.55 px**, not the 57.0 px in
  ``DeviceGeometry.line_height`` (T2). All same-paragraph adjacent-line deltas
  in the corpus are integer multiples of 45.55 to <0.05 px.
* Device highlight boxes carry a constant ~3.4 px left pad, a ~3.4 px top pad
  (first line top at y≈230.6 vs frame ``pos_y`` 234.0), and are 44.4 px tall
  (≈ the real line pitch, not the engine glyph box).
* Our Noto Sans advances diverge from the device: ``iii`` runs too wide
  (predicted x drifts ~+20 px per line of narrow glyphs) and ``mmm`` too narrow
  (the device wraps ``mmm`` earlier than our engine — a D2 line-break miss).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rock_paper_sync.layout.device import DEFAULT_DEVICE as GEOMETRY
from rock_paper_sync.layout.engine import WordWrapLayoutEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "tests" / "record_replay" / "testdata" / "calibration" / "paper_pro_move"
PROFILE = CORPUS / "profile.json"
SRC = CORPUS / "src"

# --- Measured ground-truth constants (provenance: corpus firmware 20260310084634) ---
# Body visual line pitch, from same-paragraph adjacent-line highlight y-deltas.
DEVICE_BODY_LINE_HEIGHT = 45.55
# Device highlight box padding relative to the glyph run it covers.
HL_LEFT_PAD = 3.4  # box left edge sits this far left of the glyph origin
HL_HEIGHT = 44.4  # constant box height across every block type
# X of a glyph run that starts a visual line (frame pos_x -375.0 minus the pad).
DEVICE_LINE_START_X = -378.4
# D1 tolerance (spec D1).
EPS_PX = 2.0

# Sentinels whose predictions the corpus falsifies today. Each maps to the
# measured delta so the xfail reason carries the number, not just a label.
KNOWN_X_DIVERGENT = {
    # mmm paragraph: device wraps ZEBRA05 to a new line; engine keeps it mid-line.
    "ZEBRA05": "device wraps 'mmm' earlier (metric too narrow); Δx≈100 px, Δline=1",
    "ZEBRA06": "accumulated 'mmm' metric drift; Δx≈208 px",
    # iii paragraphs: narrow-glyph advance overestimated, x drifts right.
    "ZEBRA03": "'iii' advance too wide; Δx≈+20 px",
    "ZEBRA04": "'iii' advance too wide; Δx≈+21 px",
    "ZEBRA02": "mixed-prose metric drift over 4 wrapped lines; Δx≈+17 px",
    "ZEBRA09": "punctuation advance drift; Δx≈+13 px",
    "ZEBRA10": "punctuation advance drift; Δx≈+9 px",
}


def _load_profile() -> dict:
    return json.loads(PROFILE.read_text())


def _sentinel_rects() -> dict[str, dict]:
    return {s["sentinel"]: s for s in _load_profile()["sentinels"]}


def _reconstruct_body_paragraphs(doc_stem: str) -> list[str]:
    """Rebuild body paragraphs the way mistune renders them.

    Blank-line-separated blocks; soft line breaks inside a paragraph become a
    single space. Headings and fenced code are skipped (this helper is only
    used for the body-prose differential doc).
    """
    text = (SRC / f"{doc_stem}.md").read_text()
    paragraphs: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith("#") or block.startswith("```"):
            continue
        paragraphs.append(" ".join(line.strip() for line in block.splitlines()))
    return paragraphs


def _engine() -> WordWrapLayoutEngine:
    return WordWrapLayoutEngine.from_geometry(GEOMETRY, use_font_metrics=True)


def _line_index(engine: WordWrapLayoutEngine, text: str, offset: int) -> int:
    breaks = engine.calculate_line_breaks(text, engine.text_width)
    return sum(1 for b in breaks if b <= offset) - 1


# Doc-01 body prose is the richest differential surface (W1, W2, T4, D1, D2).
DOC01 = "01_wrapped_paragraphs"
DOC01_TOKENS = [f"ZEBRA{i:02d}" for i in range(1, 11)]


def _doc01_predictions():
    """Yield (token, para_text, offset, engine_x, engine_line, device_rect)."""
    rects = _sentinel_rects()
    engine = _engine()
    origin = GEOMETRY.origin
    for para in _reconstruct_body_paragraphs(DOC01):
        for token in DOC01_TOKENS:
            offset = para.find(token)
            if offset < 0:
                continue
            x, _ = engine.offset_to_position(offset, para, origin, engine.text_width)
            line = _line_index(engine, para, offset)
            yield token, para, offset, x, line, rects[token]["rect"]


# =============================================================================
# Ground-truth self-consistency (documents the measured corpus reality)
# =============================================================================


def test_body_line_pitch_is_measured_value():
    """Same-paragraph adjacent-line deltas are integer multiples of 45.55 px.

    Ground-truth invariant of the corpus itself (not the engine): every body
    sentinel pair that our engine places on different lines has a device
    y-delta that is a clean multiple of DEVICE_BODY_LINE_HEIGHT. This is the
    evidence behind the T2 revision (57.0 -> 45.55).
    """
    rects = _sentinel_rects()
    engine = _engine()
    checked = 0
    for para in _reconstruct_body_paragraphs(DOC01):
        toks = [(t, para.find(t)) for t in DOC01_TOKENS if t in para]
        for (t1, o1), (t2, o2) in zip(toks, toks[1:]):
            dline = _line_index(engine, para, o2) - _line_index(engine, para, o1)
            if dline <= 0:
                continue
            dy = rects[t2]["rect"]["y"] - rects[t1]["rect"]["y"]
            multiple = dy / DEVICE_BODY_LINE_HEIGHT
            assert abs(multiple - round(multiple)) < 0.03, (
                f"{t1}->{t2}: device Δy={dy:.2f} is not a clean multiple of "
                f"{DEVICE_BODY_LINE_HEIGHT} (got {multiple:.3f} lines)"
            )
            checked += 1
    assert checked >= 3, "expected several same-paragraph line-delta samples"


def test_highlight_box_height_is_constant():
    """Every device highlight box is HL_HEIGHT tall, independent of block type."""
    heights = {round(s["rect"]["height"], 1) for s in _load_profile()["sentinels"]}
    assert heights == {HL_HEIGHT}, heights


@pytest.mark.xfail(
    strict=True,
    reason="T2: engine line_height is 57.0 but the corpus measures 45.55 px "
    "(Δ=11.45 px/line). Resolving this is a production-affecting refit; "
    "tracked as a follow-up, not a silent constant change.",
)
def test_engine_line_height_matches_device():
    assert abs(GEOMETRY.line_height - DEVICE_BODY_LINE_HEIGHT) < EPS_PX


# =============================================================================
# D2 - line breaks
# =============================================================================


def test_d2_relative_line_deltas():
    """D2: engine line-count between adjacent same-paragraph sentinels matches
    the device (device delta derived from y-jumps / measured line pitch)."""
    rects = _sentinel_rects()
    engine = _engine()
    for para in _reconstruct_body_paragraphs(DOC01):
        toks = [(t, para.find(t)) for t in DOC01_TOKENS if t in para]
        for (t1, o1), (t2, o2) in zip(toks, toks[1:]):
            eng_delta = _line_index(engine, para, o2) - _line_index(engine, para, o1)
            dev_delta = round(
                (rects[t2]["rect"]["y"] - rects[t1]["rect"]["y"]) / DEVICE_BODY_LINE_HEIGHT
            )
            assert eng_delta == dev_delta, (
                f"{t1}->{t2}: engine Δline={eng_delta}, device Δline={dev_delta} "
                f"(device Δy={rects[t2]['rect']['y'] - rects[t1]['rect']['y']:.2f} px)"
            )


@pytest.mark.parametrize(
    "token",
    [t for t, *_ in _doc01_predictions() if abs(_sentinel_rects()[t]["rect"]["x"] - DEVICE_LINE_START_X) < EPS_PX],
)
def test_d2_line_start_alignment(token, request):
    """D2: a sentinel the device placed at a visual line start (device
    x == DEVICE_LINE_START_X) must be predicted at a line start too (engine
    x == frame origin). Catches wrap-point disagreements directly."""
    if token in KNOWN_X_DIVERGENT:
        request.node.add_marker(pytest.mark.xfail(strict=True, reason=KNOWN_X_DIVERGENT[token]))
    origin = GEOMETRY.origin
    for tok, para, offset, x, _line, _rect in _doc01_predictions():
        if tok != token:
            continue
        assert abs(x - origin[0]) < EPS_PX, (
            f"{token}: device has it at a line start but engine predicts x={x:.1f} "
            f"(origin {origin[0]}) -> engine did not break the line here"
        )


# =============================================================================
# D1 - rectangle x within tolerance
# =============================================================================


@pytest.mark.parametrize("token", [t for t, *_ in _doc01_predictions()])
def test_d1_rect_x(token, request):
    """D1: engine glyph x, mapped through the highlight-box left pad, matches
    the device highlight x within 2 px. Font-metric divergences (iii/mmm/
    punctuation) are xfailed with their measured delta."""
    if token in KNOWN_X_DIVERGENT:
        request.node.add_marker(pytest.mark.xfail(strict=True, reason=KNOWN_X_DIVERGENT[token]))
    for tok, _para, _offset, x, _line, rect in _doc01_predictions():
        if tok != token:
            continue
        predicted_box_x = x - HL_LEFT_PAD
        delta = predicted_box_x - rect["x"]
        assert abs(delta) < EPS_PX, (
            f"{token}: predicted box x={predicted_box_x:.1f}, device x={rect['x']:.1f}, "
            f"Δ={delta:+.1f} px ({delta / DEVICE_BODY_LINE_HEIGHT:+.2f} line-heights)"
        )
