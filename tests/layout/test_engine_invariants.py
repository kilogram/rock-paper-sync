"""Property-based invariant tests for ``WordWrapLayoutEngine`` (spec E1-E6).

These are pure, device-independent property tests. They encode the engine
invariants from ``docs/LAYOUT_SPEC.md`` section E and are the permanent local
guard rail described in ``docs/LAYOUT_TESTBENCH_PLAN.md`` Phase 1:

    E1. Round trip       - position_to_offset(offset_to_position(i)) == i
    E2. Monotonicity     - i < j => position of i precedes position of j
    E3. Line-width bound - no laid-out line exceeds layout width (bar long words)
    E4. Concat stability - appending a paragraph never re-flows earlier ones
    E5. Highlight tiling - one rect per spanned line, height == line height
    E6. Determinism      - same input => byte-identical layout output

They must reproduce failures locally in well under a second and run in core CI
forever. No device is involved.

The engine is exercised in three configurations so a regression in any width
model is caught: fixed average-width, geometry-derived fixed width, and
proportional Noto Sans font metrics (falls back to fixed width silently if the
font is unavailable, which keeps the suite green in minimal environments).
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from rock_paper_sync.layout import DEFAULT_DEVICE, WordWrapLayoutEngine

# ---------------------------------------------------------------------------
# Engine fixtures / helpers
# ---------------------------------------------------------------------------

# The engines under test. Each entry is (id, factory) so failures name the mode.
_ENGINE_FACTORIES = [
    ("fixed-default", lambda: WordWrapLayoutEngine()),
    (
        "geometry-fixed",
        lambda: WordWrapLayoutEngine.from_geometry(DEFAULT_DEVICE, use_font_metrics=False),
    ),
    (
        "geometry-font-metrics",
        lambda: WordWrapLayoutEngine.from_geometry(DEFAULT_DEVICE, use_font_metrics=True),
    ),
]


@pytest.fixture(params=_ENGINE_FACTORIES, ids=[e[0] for e in _ENGINE_FACTORIES])
def engine(request) -> WordWrapLayoutEngine:
    """A layout engine in each supported width configuration."""
    return request.param[1]()


def _engine_width(engine: WordWrapLayoutEngine) -> float:
    """The wrap width this engine was configured with."""
    return engine.text_width


ORIGIN = (-375.0, 234.0)

# Fixed edge cases required by the plan (empty, single char, all-spaces,
# word longer than a line, explicit newlines, unicode).
EDGE_CASE_TEXTS = [
    "",
    "a",
    " ",
    "     ",
    "\n",
    "\n\n\n",
    "word",
    "the quick brown fox jumps over the lazy dog",
    "iiiii lllll iiiii lllll iiiii lllll iiiii lllll",  # narrow glyphs
    "mmmmm wwwww mmmmm wwwww mmmmm wwwww mmmmm wwwww",  # wide glyphs
    "a" * 500,  # single word far wider than any line
    "supercalifragilisticexpialidocious " * 5,
    "line one\nline two\nline three",
    "trailing spaces here      \nnext line",
    "café résumé naïve Zürich Ångström déjà",  # accented unicode
    "1234567890 " * 10,  # numerals
    "!@#$%^&*() ,.;:'\"?-- punctuation heavy!!!",
    "word\n\nword\n\n\nword",  # blank-line gaps as explicit newlines
]

# Characters that are safe for generated text: positive-advance, no combining
# marks, no control characters other than the space and newline we handle
# explicitly. This keeps monotonicity/round-trip strict (zero-width glyphs
# would legitimately break "strictly smaller x").
_TEXT_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " \n"  # whitespace we treat structurally
    ".,;:!?-'\"()"
    "iilllmmmwwwWW"  # bias toward mixed narrow/wide widths
    "áéíóúñçüßÅ"  # a few positive-advance accented letters
)

# Sizes kept modest: several properties are O(n^2) per example (they position
# every offset, each recomputing line breaks), so large inputs would blow the
# fast-feedback budget (P8) without adding meaningful coverage over wrapping.
text_strategy = st.text(alphabet=_TEXT_ALPHABET, min_size=0, max_size=160)

# Non-whitespace-only text for tests that need actual glyphs to position on.
nonspace_text_strategy = text_strategy.filter(lambda s: any(c not in " \n" for c in s))


def line_spans(engine: WordWrapLayoutEngine, text: str) -> list[tuple[int, int]]:
    """Return (start, end) char offsets for each laid-out line."""
    breaks = engine.calculate_line_breaks(text, _engine_width(engine))
    spans = []
    for i, start in enumerate(breaks):
        end = breaks[i + 1] if i + 1 < len(breaks) else len(text)
        spans.append((start, end))
    return spans


# ---------------------------------------------------------------------------
# E1. Round trip
# ---------------------------------------------------------------------------


def _assert_round_trip(engine: WordWrapLayoutEngine, text: str) -> None:
    width = _engine_width(engine)
    for offset in range(len(text) + 1):
        x, y = engine.offset_to_position(offset, text, ORIGIN, width)
        recovered = engine.position_to_offset(x, y, text, ORIGIN, width)
        # "within same-line resolution": the recovered offset must land on the
        # same line as the original and reproduce the same pixel position.
        rx, ry = engine.offset_to_position(recovered, text, ORIGIN, width)
        assert ry == y, (
            f"round-trip changed line: offset={offset!r} -> ({x},{y}) -> "
            f"{recovered} at y={ry} (text={text!r})"
        )
        assert rx == pytest.approx(x, abs=0.5), (
            f"round-trip changed x: offset={offset} x={x} recovered_x={rx} (text={text!r})"
        )


@pytest.mark.parametrize("text", EDGE_CASE_TEXTS)
def test_e1_round_trip_edge_cases(engine, text):
    _assert_round_trip(engine, text)


@settings(max_examples=100, deadline=None)
@given(text=text_strategy)
def test_e1_round_trip_generated(text):
    engine = WordWrapLayoutEngine.from_geometry(DEFAULT_DEVICE, use_font_metrics=True)
    _assert_round_trip(engine, text)


# ---------------------------------------------------------------------------
# E2. Monotonicity
# ---------------------------------------------------------------------------


def _assert_monotonic(engine: WordWrapLayoutEngine, text: str) -> None:
    width = _engine_width(engine)
    positions = [engine.offset_to_position(i, text, ORIGIN, width) for i in range(len(text) + 1)]
    for i in range(len(positions) - 1):
        (x0, y0), (x1, y1) = positions[i], positions[i + 1]
        # Reading order: strictly later means a greater y, or same y with a
        # strictly greater x.
        assert (y1 > y0) or (y1 == y0 and x1 > x0), (
            f"non-monotonic at offset {i}->{i + 1}: ({x0},{y0}) then ({x1},{y1}) "
            f"(text={text!r})"
        )


@pytest.mark.parametrize("text", EDGE_CASE_TEXTS)
def test_e2_monotonicity_edge_cases(engine, text):
    _assert_monotonic(engine, text)


@settings(max_examples=100, deadline=None)
@given(text=nonspace_text_strategy)
def test_e2_monotonicity_generated(text):
    engine = WordWrapLayoutEngine.from_geometry(DEFAULT_DEVICE, use_font_metrics=True)
    _assert_monotonic(engine, text)


# ---------------------------------------------------------------------------
# E3. Line-width bound
# ---------------------------------------------------------------------------


def _assert_line_width_bound(engine: WordWrapLayoutEngine, text: str) -> None:
    width = _engine_width(engine)
    for start, end in line_spans(engine, text):
        line = text[start:end].rstrip(" \n")  # trailing whitespace has no visible width
        if not line:
            continue
        line_width = engine._get_text_width(line)
        is_single_word = " " not in line
        if is_single_word and line_width > width:
            # Allowed exception (W2): an unbreakable word wider than the line.
            continue
        assert line_width <= width + 0.5, (
            f"line exceeds layout width {width}: {line_width} for {line!r} (text={text!r})"
        )


@pytest.mark.parametrize("text", EDGE_CASE_TEXTS)
def test_e3_line_width_bound_edge_cases(engine, text):
    _assert_line_width_bound(engine, text)


@settings(max_examples=100, deadline=None)
@given(text=text_strategy)
def test_e3_line_width_bound_generated(text):
    engine = WordWrapLayoutEngine.from_geometry(DEFAULT_DEVICE, use_font_metrics=True)
    _assert_line_width_bound(engine, text)


# ---------------------------------------------------------------------------
# E4. Concatenation stability
# ---------------------------------------------------------------------------


def _assert_concat_stable(engine: WordWrapLayoutEngine, first: str, second: str) -> None:
    width = _engine_width(engine)
    first_breaks = engine.calculate_line_breaks(first, width)
    # Append as a distinct paragraph (paragraph boundary == blank line).
    combined = first + "\n\n" + second
    combined_breaks = engine.calculate_line_breaks(combined, width)
    # Every break belonging to the first paragraph must be unchanged: the
    # combined breaks start with exactly the first paragraph's breaks.
    assert combined_breaks[: len(first_breaks)] == first_breaks, (
        f"appending re-flowed the first paragraph:\n"
        f"  first={first_breaks}\n  combined={combined_breaks[: len(first_breaks) + 2]}\n"
        f"  first_text={first!r} second_text={second!r}"
    )


@settings(max_examples=100, deadline=None)
@given(first=text_strategy, second=text_strategy)
def test_e4_concatenation_stability(first, second):
    engine = WordWrapLayoutEngine.from_geometry(DEFAULT_DEVICE, use_font_metrics=True)
    _assert_concat_stable(engine, first, second)


@pytest.mark.parametrize("first", EDGE_CASE_TEXTS)
def test_e4_concatenation_stability_edge_cases(engine, first):
    _assert_concat_stable(engine, first, "an appended paragraph of trailing text")


# ---------------------------------------------------------------------------
# E5. Highlight tiling
# ---------------------------------------------------------------------------


def _assert_highlight_tiling(
    engine: WordWrapLayoutEngine, text: str, start_offset: int, end_offset: int
) -> None:
    width = _engine_width(engine)
    rects = engine.calculate_highlight_rectangles(
        start_offset, end_offset, text, ORIGIN, width
    )

    spans = line_spans(engine, text)
    # Expected: one rect for each line whose glyph range intersects the
    # highlight span (empty intersections and whitespace-only overlaps produce
    # zero-width contributions but the engine still emits a rect only when
    # hl_start < hl_end).
    expected_lines = []
    for idx, (ls, le) in enumerate(spans):
        hl_start = max(start_offset, ls)
        hl_end = min(end_offset, le)
        if hl_start < hl_end:
            expected_lines.append((idx, ls, hl_start, hl_end))

    assert len(rects) == len(expected_lines), (
        f"expected {len(expected_lines)} rects, got {len(rects)} "
        f"for span [{start_offset},{end_offset}) in {text!r}"
    )

    seen_y = set()
    for (rx, ry, rw, rh), (idx, ls, hl_start, hl_end) in zip(rects, expected_lines):
        # One rect per line: heights equal the line height.
        assert rh == engine.line_height
        # Rects are placed on distinct, increasing lines.
        assert ry == pytest.approx(ORIGIN[1] + idx * engine.line_height, abs=1e-6)
        assert ry not in seen_y
        seen_y.add(ry)
        # The rect exactly covers the highlighted glyph advances on its line.
        expected_x = ORIGIN[0] + engine._get_text_width(text[ls:hl_start])
        expected_w = engine._get_text_width(text[hl_start:hl_end])
        assert rx == pytest.approx(expected_x, abs=1e-6)
        assert rw == pytest.approx(expected_w, abs=1e-6)
    # Rects are ordered top-to-bottom (contiguous tiling).
    ys = [r[1] for r in rects]
    assert ys == sorted(ys)


@pytest.mark.parametrize("text", [t for t in EDGE_CASE_TEXTS if t])
def test_e5_highlight_tiling_edge_cases(engine, text):
    # Highlight the whole document, and a middle slice.
    _assert_highlight_tiling(engine, text, 0, len(text))
    if len(text) >= 4:
        _assert_highlight_tiling(engine, text, len(text) // 4, 3 * len(text) // 4)


@settings(max_examples=100, deadline=None)
@given(data=st.data(), text=nonspace_text_strategy)
def test_e5_highlight_tiling_generated(data, text):
    engine = WordWrapLayoutEngine.from_geometry(DEFAULT_DEVICE, use_font_metrics=True)
    n = len(text)
    start = data.draw(st.integers(min_value=0, max_value=n))
    end = data.draw(st.integers(min_value=start, max_value=n))
    assume(start < end)
    _assert_highlight_tiling(engine, text, start, end)


# ---------------------------------------------------------------------------
# E6. Determinism
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(text=text_strategy)
def test_e6_determinism(text):
    width = DEFAULT_DEVICE.effective_layout_width

    def compute():
        engine = WordWrapLayoutEngine.from_geometry(DEFAULT_DEVICE, use_font_metrics=True)
        breaks = engine.calculate_line_breaks(text, width)
        positions = [
            engine.offset_to_position(i, text, ORIGIN, width) for i in range(len(text) + 1)
        ]
        rects = engine.calculate_highlight_rectangles(0, len(text), text, ORIGIN, width)
        return breaks, positions, rects

    first = compute()
    for _ in range(3):
        assert compute() == first, f"non-deterministic layout for {text!r}"


@pytest.mark.parametrize("text", EDGE_CASE_TEXTS)
def test_e6_determinism_edge_cases(engine, text):
    width = _engine_width(engine)
    a = engine.calculate_line_breaks(text, width)
    b = engine.calculate_line_breaks(text, width)
    assert a == b
