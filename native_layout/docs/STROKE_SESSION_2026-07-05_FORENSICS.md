# Forensic report: comprehensive_strokes device session (2026-07-05)

Operator-observed symptoms on device, mapped to evidence from the recorded
testdata (`tests/record_replay/testdata/comprehensive_strokes/trips/`) and to
production code paths. Geometry: `line_height=45.55`, `lines_per_page=32`,
wrap width 750/758. Pages: `...6288`(p0), `...6320`(p1), `...6352`(p2),
`...6384`(p3).

**Recording caveat:** `manifest.json` has `"trips_recorded": [1, 2]` — trip 3
was never captured (the run aborted / was not saved), and `trips/golden/` is a
clean separate document with zero strokes. Symptoms attributed to trip 3
(restack position, surviving "delete", the actual 'stable' split) are
reconstructed by mechanism, not directly measured.

## 1. Four pages instead of two; underscore lines wrap; Section 7 spills

Two independent causes, both confirmed:

**a) The fixture is authored wider than the device text area.** At measured
Noto Sans widths only ~50–52 chars fit in 750 px. The fixture's ruled gaps are
`"Write here: " + 49–57 underscores` ≈ 830–880 px and the Lorem-ipsum prose
lines are 68–77 chars ≈ 990–1180 px — every one wraps to 2 display lines, on
the device and in the engine alike (they agree). The document roughly doubles:
91 display lines total. The "2 pages" expectation in the fixture was never
achievable at 750 px.

**b) The generator underfills pages.** Measured display-line demand per
emitted page: p0=23, p1=23, p2=25, p3=20 — against a 32-line capacity. 91
lines fit in 3 pages; the generator emitted 4, closing every page at ~23
lines. Suspect: `RemarkableGenerator.paginate_content()` /
`estimate_block_lines()` (structural padding per header/HR + section
keep-together), `generator.py:702/:748`.

## 2. Strokes stacked at one position after trip 2 (headline bug)

Distinct anchor character-offsets among stroke-bearing TreeNodeBlocks
collapse between trips:

| page | trip 1 anchors (char offsets) | trip 2 anchors |
|---|---|---|
| p0 | 5 distinct {219, 413, 416, 480, 601} | **1**: all → 267 |
| p1 | 5 distinct {104, 163, 263, 587, 595} | 2: {152 ×4, 163 ×1} |
| p3 | 4 distinct {392, 673, 675, 677} | 2: {392, 721 ×3} |

Page 0 in trip 2: all five nodes share `anchor CrdtId(1,283)` → char 267
(inside the surviving paragraph *"Use normal width, default color (black)."*),
i.e. one shared `anchorY=598.4` where trip 1 had five distinct anchor Ys
(552.8 … 1053.9). Native coords and `anchor_origin_x` are preserved, so the
five stroke groups render piled on one line — exactly the observed stack. The
collapse target differs per page (267/152/721), which is why each page's stack
sits somewhere else, and why a re-push after the next edit moves the stack
again (symptom: "after trip 3 they overlap in a different place").

**Root cause:** `StrokeHandler._calculate_anchor_offset()`
(`stroke_handler.py:285`) resolves each bundle **independently**. The trip-2
edit rewrote the anchored paragraphs (`"Write here: ___"` → `"MODIFIED GAP:
___"`, changed underscore counts, deleted Section 6, reflowed), so Strategy 1
(verbatim `AnchorContext.text_content` match) fails for every gap stroke, and
each bundle falls through to the same coarse fallback (Strategy 3 Y-hint
`position_to_offset` at `:353` / Strategy 4 paragraph-index at `:365`) — many
distinct strokes → one shared offset, which `CrdtService.reanchor_bundle()`
(`crdt_service.py:253`) then faithfully applies. Contributing: underscore-gap
paragraphs are near-identical, so even Strategy 1's `find_all_occurrences`
disambiguation (`:322`) degenerates to `occurrences[0]`. The anchors that
survived correctly (p1→163, p3→392) are exactly the ones whose anchor text
was unique.

## 3. Restack at a different place after trip 3

Not recorded (see caveat). Fully predicted by §2: re-running the same
fallback against trip-3 text yields a new shared offset. Needs re-record to
be falsifiable.

## 4. "delete" still present

**Contradicted by the recorded data.** Trip 1 `.rm` p1 contains
`'...Write "delete me" in t...'`; in trip 2 both the vault markdown and all
four RootTextBlocks contain no "delete"/"Section 6" — the deletion propagated
correctly in what was recorded. The observed symptom must belong to the
uncaptured trip 3. Unobserved, not exonerated: re-record.

## 5. Split 't' in handwritten "stable"

In the recorded trips the 'stable' strokes (p3, chars 673/675/677) collapsed
*uniformly* to char 721 (+45.55 px = exactly one line), so they stayed
together. But the split **mechanism is proven on p1**: node 866 resolved to
char 163 while its four siblings collapsed to 152 — sibling strokes of one
visual glyph can resolve to different anchors and separate. Same root cause
as §2: per-bundle resolution with no constraint that strokes sharing an
original anchor line stay together.

## Fix directions (systemic, in priority order)

1. **Cluster-preserving reanchoring**: group bundles by their original shared
   anchor line, resolve ONE offset per cluster, apply to all members —
   removes the stacking (§2), the restack (§3), and the split-glyph (§5)
   failure classes at once.
2. **Fail loudly instead of collapsing**: if a fallback strategy would map ≥2
   previously-distinct bundles onto one offset, treat it as reanchor failure
   (orphan path) rather than silently stacking.
3. **Pagination audit** (`paginate_content`, `estimate_block_lines`): pages
   must fill toward `lines_per_page`; underscore runs must cost what the
   device charges (wrapped), not a modelled single line — then add a
   generator-vs-engine page-count consistency test.
4. **Re-author the fixture** to ≤50-char lines (rules included) so its page
   expectations are device-achievable, and **re-record trips 1–3 + golden**
   (the current golden has zero strokes and is unusable as annotation ground
   truth).

## Evidence provenance

All numbers extracted from the trip `.rm` files via `rmscene` +
`RmFileExtractor` (anchor `part2` → char offset via `crdt_to_char`,
absolute Y via `LayoutContext.offset_to_position` + native + 70.55 offset).
