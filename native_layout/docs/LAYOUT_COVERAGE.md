# Layout construct coverage: device-measured vs possible

What the generator can emit (parser `BlockType` + inline `FormatStyle`) versus
what is certified against device-pulled `.rm` data. "Device-measured" means a
signed positional ground truth exists in-repo (calibration `profile.json`
glyph rects, golden highlight rects, or device-written TreeNodeBlock anchors)
and a default-CI test asserts against it.

Verification layers (all offline, plain `uv run pytest`):

| Layer | Test | Oracle |
|---|---|---|
| Engine invariants (E1–E6) | `tests/layout/test_engine_invariants.py` | property-based |
| Corpus differential (D1/D2, x + paragraph-relative y) | `tests/layout/test_corpus_differential.py` | 41-sentinel profile.json, firmware 20260310084634 |
| Geometry provenance | `tests/layout/test_geometry_profile_consistency.py` | profile.json, by construction |
| Literal ban (D4) | `tests/layout/test_layout_literal_guard.py` | grep; 57 banned forever |
| Positional certification (highlights x/pitch, stroke anchor x, anchor identity) | `tests/record_replay/test_positional_certification.py` | device golden `.rm` (reanchor scenarios) |
| Visual verdict calibration | `tests/record_replay/test_visual_verdict_calibration.py` | rendered device capture, perturbation-calibrated pHash |

## Covered by device-measured ground truth

| Construct | Source | Notes |
|---|---|---|
| Body paragraphs, word wrap | corpus 01 (ZEBRA01–10) | 7 sentinels x-divergent (T4, strict xfail) |
| Char-granularity longword break | corpus 01 | W2 residual open |
| Headings H1–H6 | corpus 02 (ZEBRA11–17) | per-level line pitch open (T3, Phase 8) |
| Nested lists, ordered + unordered, 3 levels | corpus 03 (ZEBRA18–26) | marker indent residual (B3) |
| Fenced code blocks | corpus 04 (ZEBRA27–30) | |
| Inline code spans | corpus 07 | |
| Paragraph spacing / blank-line collapse | corpus 05 (ZEBRA31–35) | inter-paragraph spacing not modelled offline (B1) |
| Multipage / page break | corpus 06 (ZEBRA36–40) | P1g/P2g pinned |
| Highlights (rect x, adjacent-line pitch) | corpus + highlight_reanchor goldens | width model open (16–22 px pad band) |
| Strokes (anchor_origin_x, anchor identity across reconciliation) | stroke_reanchor phase3/golden | T5 baseline offset open (20 vs 25, Phase 8) |
| Pen stroke baseline probe | corpus 07 (T5BASE) | anchor not captured; re-record with anchor capture |

## Possible but NOT device-measured (Phase 8 fixture list)

| Construct | Status |
|---|---|
| Blockquote | no device extract at all |
| Italic / bold inline geometry | bold exists visually (`Bold_Heading_Bullet_Normal.rm`) but no positional sentinel |
| Link (`text (url)` transform) | untested against device |
| Strikethrough | no device extract |
| Horizontal rule | present in merge-scenario docs, no measured layout footprint |
| Heading wrap (H1–H6 multi-line) | fixture 09 planned |
| Wrap-boundary bracketing of 750/758 | fixture 10 planned |

These map to the Phase 8 fixture additions in `docs/LAYOUT_VERIFICATION_PLAN.md`
(08–13 + margin-stroke probes). Until then, any sync of documents relying on
the constructs in the second table has *engine-modelled* layout only — the
regression net covers everything in the first table.
