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

**T2.** Body line height is **57.0 px** in document coordinates. 68 px is the
264/226-scaled thumbnail value and must never appear in coordinate math. —
`ASSERTED` (2025-12-29)

**T3.** Heading line height is 87 px (≈1.53× body). Per-heading-level values
for H1–H6 are not individually calibrated. — `OPEN` (corpus must measure each
level)

**T4.** Body font is Noto Sans at typographic 10.0 pt ⇒ 31.4 px at 226 DPI.
Character advance widths follow the Noto Sans font metrics
(`font_metrics.py`); 15.0 px is an *average* permitted only as documented
fallback inside `layout/`. — `ASSERTED` (2025-12-12)

**T5.** Baseline offset (line top → text baseline): `DeviceGeometry` says
**25.0**; `tools/rmlib/renderer.py` / `RENDERER_COORDINATE_MODEL.md` say
**20**. These cannot both be right. — `OPEN` (contradiction; corpus test must
decide; see P4 corollary)

## W. Word wrapping

**W1.** The device wraps using the effective layout width
`layout_text_width = 758.0` (≈8 px wider than the stored `text_width` of
750). — `ASSERTED` (2025-12-08)

**W2.** Wrapping is greedy word wrap on whitespace with proportional Noto
Sans advances; a word longer than the line width is broken at character
granularity. — `ASSERTED` / partially `OPEN` (long-word breaking behavior
unverified)

**W3.** (Oracle expectation) Qt `QTextLayout` with Noto Sans at the correct
**pixel** size reproduces device line breaks exactly for corpus inputs. The
one free parameter (effective pixel size) is fitted once from corpus glyph
rectangles. — `OPEN` (currently only width *ratios* match within 10%;
`tests/calibration/test_qt_reference.py`)

**W4.** Line-break positions depend only on (paragraph text, layout width,
font metrics) — never on annotations, page number, or surrounding blocks. —
invariant of our engine; must match device behavior on corpus.

## B. Block layout (structural)

Values to be measured by the corpus (`tools/calibration/extract_profile.py`
skeleton exists); all `OPEN` until then:

**B1.** Vertical spacing between paragraphs (blank-line gap).
**B2.** Vertical spacing before/after headings, per level.
**B3.** List item spacing and per-level indentation (bullets and ordered).
**B4.** Code block line height, indentation, and spacing.
**B5.** First rendered line starts at `pos_y` (234.0) — i.e. top margin is
part of the text frame, not an extra offset.

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

**D1.** For every device-recorded highlight in the corpus, the engine's
predicted rectangle matches within ε = 2.0 px in x, y, width (height per
T2/T3).

**D2.** For every corpus paragraph, engine line-break offsets equal
device-observed line breaks exactly (derived from highlight y-jumps).

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
