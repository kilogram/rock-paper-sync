# Layout Specification: Invariants and Expectations

Falsifiable statements about how text and annotations are laid out on the
reMarkable Paper Pro Move (document coordinate system). Governed by
`docs/LAYOUT_PRINCIPLES.md`.

**Status legend**

- `VERIFIED(corpus)` — checked by a differential test against the device
  ground-truth corpus (test name given).
- `ASSERTED` — believed from past calibration but not yet pinned by a corpus
  test. Every ASSERTED entry is a work item in the plan; the goal state is
  zero ASSERTED entries.
- `OPEN` — known unknown or known contradiction.

Provenance below refers to Paper Pro Move calibration sessions 2025-11-30,
2025-12-08, 2025-12-12, 2025-12-29 (see `docs/RMSCENE_FINDINGS.md`,
`docs/RENDERER_COORDINATE_MODEL.md` for narrative). Firmware version at
recording time MUST be stamped into the corpus (`profile.json`).

---

## C. Coordinate system

**C1.** All `.rm` document coordinates use the reMarkable 2 document space:
1404 × 1872 px @ 226 DPI, regardless of physical device. — `ASSERTED`
(validated 2025-12-12)

**C2.** X origin is the horizontal page center; text X coordinates are
center-relative (text frame at `pos_x = -text_width/2 = -375.0`). Y origin is
page top, positive downward. — `ASSERTED`

**C3.** Highlight rectangles (`SceneGlyphItemBlock`) store **absolute** page
coordinates (x center-relative, y absolute); no transformation is applied by
the device. — `ASSERTED` (this is what makes them usable as ground truth; see
P2)

**C4.** Stroke points (`SceneLineItemBlock`) are **anchor-relative**:
`page = anchor + native + (0, baseline_offset)`. — `ASSERTED`

## T. Text frame and typography

**T1.** Default text frame: `pos_x = -375.0`, `pos_y = 234.0`,
`width = 750.0` (from `RootTextBlock`; always read from the file, never
assume). — `ASSERTED`

**T2.** Body visual line pitch is **45.55 px** in document coordinates —
`VERIFIED(corpus)` (`test_corpus_differential::test_body_line_pitch_is_measured_value`,
firmware 20260310084634). Every same-paragraph adjacent-line highlight y-delta
in the corpus is an integer multiple of 45.55 to <0.05 px, and the highlight box
height is 44.4 px (≈ pitch). This **falsifies the prior 57.0 px value** carried
in `DeviceGeometry.line_height` (Δ=11.45 px/line;
`test_engine_line_height_matches_device` is `xfail(strict)`). Changing the
constant is a production-affecting refit (pagination + annotation anchoring are
calibrated around 57 and round-trip symmetrically today) — tracked as a Phase 4
follow-up, not a silent edit. 68 px is the 264/226-scaled thumbnail value and
must never appear in coordinate math.

**T3.** Heading blocks pitch at **139.1 px** (heading + trailing body + gaps;
`03`/`02` corpus). The highlight box height is a constant 44.4 px for **every**
block type, so the per-level *glyph* height for H1–H6 is not distinguishable
from highlight rectangles alone — the corpus cannot measure per-level line
height with the current instrumentation. — `OPEN` (needs a probe that captures
line-top-to-line-top within a multi-line heading, or `RootTextBlock` glyph
metrics)

**T4.** Body font is Noto Sans at typographic 10.0 pt ⇒ 31.4 px at 226 DPI.
Character advance widths follow the Noto Sans font metrics
(`font_metrics.py`); 15.0 px is an *average* permitted only as documented
fallback inside `layout/`. — `OPEN` (our advances **diverge from the device**:
corpus shows `iii` runs too wide, ~+20 px x-drift per line of narrow glyphs,
and `mmm` too narrow — the device wraps `mmm` a line earlier than our engine.
Measured per-token deltas in `test_corpus_differential::test_d1_rect_x`
(`xfail(strict)`). The free pixel-size fit is Phase 5's W3 job.)

**T5.** Baseline offset (line top → text baseline): `DeviceGeometry` says
**25.0**; `tools/rmlib/renderer.py` / `RENDERER_COORDINATE_MODEL.md` say
**20**. — `OPEN` (still unresolved). The T5 probe recorded the descender
stroke's native y-bounds (6.55–39.8) and the underscore highlight top (230.6)
but **not** the stroke's anchor; strokes are anchor-relative (C4), so native
bounds cannot be converted to a page baseline without the absolute anchor. The
probe is insufficient — re-record with anchor capture (corpus refresh) before
resolving. Corpus-measured highlight top pad is ~3.4 px (first line top 230.6
vs frame `pos_y` 234.0).

## W. Word wrapping

**W1.** The device wraps using an effective layout width near
`layout_text_width = 758.0`. Corpus implies a lower bound of ~747 px (nearest
line-start sentinel to the right margin). — `ASSERTED` (2025-12-08; corpus
consistent but does not tighten the value — no sentinel sits exactly on the
wrap boundary)

**W2.** Wrapping is greedy word wrap on whitespace with proportional Noto
Sans advances; a word longer than the line width is broken at character
granularity. — partially `OPEN`. Wrap *structure* matches on prose, numerals,
and `iii` (`test_corpus_differential::test_d2_relative_line_deltas`,
`test_d2_line_start_alignment`), but **fails on `mmm`**: the device breaks a
line earlier than our engine because our `m` advance is too narrow (see T4).
Long-word character-granularity breaking (ZEBRA-longword fixture) remains
unverified.

**W3.** (Oracle expectation) Qt `QTextLayout` with Noto Sans at the correct
**pixel** size reproduces device line breaks exactly for corpus inputs. The
one free parameter (effective pixel size) is fitted once from corpus glyph
rectangles. — `OPEN` (currently only width *ratios* match within 10%;
`tests/calibration/test_qt_reference.py`)

**W4.** Line-break positions depend only on (paragraph text, layout width,
font metrics) — never on annotations, page number, or surrounding blocks. —
invariant of our engine; must match device behavior on corpus.

## B. Block layout (structural)

Measured from the corpus (`profile.json`, firmware 20260310084634). Values are
highlight-box coordinates; add the ~3.4 px box left/top pad to recover glyph
positions.

**B1.** Paragraph pitch (single-line paragraphs, blank-line separated) is
**115.1 px** (spacing-ladder doc; deltas constant to 0.01 px ⇒ extra blank
lines collapse). — `VERIFIED(corpus)` (`profile.json:paragraph_gap_px`)

**B2.** Heading block pitch (heading + trailing body + surrounding gaps) is
**139.1 px** per level (H2→H5, constant). Per-level heading *line* height is
not separable from highlights (see T3). — partially `OPEN`

**B3.** List items pitch at **69.55 px**; a blank line between list groups adds
one extra 69.55 gap. Per-level indentation is **not** observed — every
sentinel across nesting levels 0–2 sits at x=-359.2 (the device does not indent
the highlighted token text, only the bullet marker). — `VERIFIED(corpus)`
(`profile.json:list_indent_x_by_level`, `paragraph_gap_px`); indentation of the
marker itself is `OPEN` (no marker sentinel).

**B4.** Code block lines pitch at **69.55 px** (same as list items). First code
line x=-378.4 (line start); subsequent lines carry source leading whitespace
(ZEBRA29 -348.2, ZEBRA30 -291.9). — `VERIFIED(corpus)` for pitch; indentation
detail `OPEN`.

**B5.** First rendered line starts at `pos_y` (234.0): the first-line highlight
top is 230.6 = 234.0 − 3.4 (box top pad), so the glyph top is at the frame
`pos_y` with no extra offset. — `VERIFIED(corpus)`
(`test_corpus_differential`; first-line sentinels ZEBRA11/ZEBRA16/T5BASE)

## G. Pagination

**P1g.** A page break occurs when the next block's bottom would exceed
`page_height − bottom_margin` (1872 − 100). — `ASSERTED` (generator model;
device-side behavior for our generated multi-page docs must be corpus-pinned)

**P2g.** Content below y ≈ 1443 is off-screen on Paper Pro Move's viewport
but still valid document content; pagination decisions use full page height,
not viewport height. — `ASSERTED` (2025-12-12)

## E. Engine invariants (device-independent; property-testable)

**E1. Round trip.** For every char offset `i` in text:
`position_to_offset(offset_to_position(i)) == i` (within same-line
resolution).

**E2. Monotonicity.** `i < j` ⇒ position of `i` is earlier in reading order
(same line and smaller x, or smaller y).

**E3. Line-width bound.** No laid-out line's advance width exceeds
`layout_text_width`, except single unbreakable words (per W2).

**E4. Concatenation stability.** Appending a paragraph never changes the
layout of preceding paragraphs (given same width).

**E5. Highlight tiling.** Highlight rectangles for a char range exactly cover
the glyph advances of that range: contiguous per line, one rect per line,
height = line height.

**E6. Determinism.** Same input ⇒ byte-identical layout output. No ambient
state (locale, environment) may influence layout.

## D. Differential expectations (corpus tests)

Highlight-box model (corpus-measured, needed to compare an engine glyph rect
to a device highlight rect): box left edge = glyph x − **3.4 px** (left pad),
box top = glyph top − ~3.4 px, box height = **44.4 px** (constant), box width =
glyph advance + ~16–22 px (per-token, not yet modelled tightly).

**D1.** For every device-recorded highlight, the engine's predicted rectangle
(mapped through the box left pad) matches within ε = 2.0 px in x. — partially
`VERIFIED(corpus)` (`test_corpus_differential::test_d1_rect_x`): holds for
line-start glyphs (ZEBRA01/07/08); **fails** for narrow-glyph and mmm runs by
the T4/W2 metric divergence (each failing token `xfail(strict)` with its
measured Δx). Absolute y and width are not yet asserted — y needs the T2 refit
and full block stacking; width needs a highlight-box width model.

**D2.** Engine line breaks equal device-observed line breaks. — partially
`VERIFIED(corpus)` (`test_corpus_differential::test_d2_relative_line_deltas`,
`test_d2_line_start_alignment`): prose, numerals, and `iii` match; `mmm` fails
(ZEBRA05 line-start mismatch, `xfail(strict)`).

**D3.** Renderer output uses the production engine (P3): given identical
inputs, renderer glyph positions equal engine predictions exactly (0 px —
same code path, this is a wiring test, not a tolerance test).

**D4.** Magic-number guard: layout literals (57, 68, 758, 750, 234, −375,
15.0, 20/25, 87, 100) do not appear in `src/` or `tools/` outside
`src/rock_paper_sync/layout/` (allowlisted exceptions documented inline).

## Corpus contract

- Location: `tests/record_replay/testdata/calibration/<device>/`
- Contents: source markdown, device `.rm` files with on-device highlights
  over sentinel words, extracted `profile.json` (firmware, xochitl version,
  recording date, extracted measurements), capture script transcript.
- Refresh triggers: firmware update, new device model, or any `OPEN` item
  needing new instrumentation.
- The corpus is append-mostly; re-records replace files with a note in
  `profile.json` explaining why (P7).
