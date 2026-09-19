# Device-replica rendering: architecture evaluation & redesign

Goal: make the 2026-07-05 session's bug classes (stroke stacking, split
glyphs, page inflation, metric drift) *structurally impossible* by giving
every sync a faithful local preview and gating CI on it. Companion to
`docs/LAYOUT_VERIFICATION_PLAN.md` (this refines Phases 4b/6/7 with new
evidence) and `docs/bugs/STROKE_SESSION_2026-07-05_FORENSICS.md`.

## 1. What the device actually is (xochitl inspection, 2026-07-05)

Evidence from the firmware binary (stripped aarch64 ELF; strings + dynamic
symbols):

- **Layout engine:** Qt 6 `QTextDocument`/`QTextLayout` (imports:
  `QTextLayout::beginLayout/createLine`, `QTextDocument::setTextWidth/
  setDocumentMargin/setIndentWidth/setPageSize`, `QTextCursor::
  setBlockFormat`), wrapped in a CRDT `SceneController`. Not a custom engine.
- **Fonts:** proprietary `reMarkableSans-{Regular,Medium,Bold,…}.ttf` and
  `reMarkableSerifSmall-*.ttf` embedded as Qt resources, plus NotoEmoji.
  **Not Noto Sans.** Our engine/renderer measure Noto Sans — this mismatch is
  the probable root of the entire T4 accumulated-x-drift class (+7 px/line)
  and the W2 wrap divergences.
- **Line height:** set programmatically per block via `QTextBlockFormat`
  (QML-exposed `textLineHeight`/`textAnchorLineHeight`). Numeric values are
  compiled immediates — not recoverable from strings; the corpus measurement
  (45.55 px body pitch) remains the ground truth for them.
- **Paragraph styles:** exactly Title, Heading, Body, Bulletpoint,
  NumberedList, Checkbox (+ single "Indented" variants; one indent depth).
  There are only TWO heading tiers on-device — our H1–H6 model maps onto
  {Title, Heading}, which the replica (and generator styling) must mirror.
- **Text width:** user-adjustable (`TextWidthModel`,
  `SceneSetRootDocumentTextWidthAction`); 750/758 are our measured values for
  the default setting, not device constants.

Consequence: a *faithful* local replica is achievable not by imitating pixels
but by **using the same layout algorithm (Qt) + the same font files (pulled
from the device) + corpus-calibrated block formats**. Fidelity by
construction, residuals measured by the corpus.

## 2. Why the current renderer cannot catch these bugs (audit summary)

`tools/rmlib/renderer.py` is a third parallel layout universe (full audit in
the session transcript; key points):

- Hardcodes `BODY_LINE_HEIGHT=57` (the falsified pitch), `HEADING_LINE_HEIGHT
  =87`, `STROKE_BASELINE_OFFSET=20` (vs production 25 — the T5 conflict),
  own font sizes (31/65), Noto Sans from system paths.
- Recomputes char→y with a private cumulative loop and uses the production
  `AnchorResolver` only for CRDT→offset; discards its Y.
- Ignores the negative-Y dual-anchor rule (`AnchorRelativePoint.to_document`).
- Models NO pagination, no H1–H6 (one heading style), no lists, no code
  blocks, no measured paragraph spacing.
- Escaped the D4 literal guard because the guard only scans `src/`.

A renderer with private math can only ever *agree with itself*. Rendering the
trip-2 output through it would have drawn the stroke stack — but nothing
asserted on it, and the renderer's own errors (57 px, baseline 20) would have
drowned the signal.

## 3. Redesign: three layers, each falsifiable

### Layer A — one engine everywhere (Phase 4b, extended)

1. Renderer deletes all private layout math; every char→(x,y) comes from
   `LayoutContext.offset_to_position` / `AnchorResolver`; stroke placement
   uses `AnchorRelativePoint.to_document` (incl. negative-Y rule); pagination
   from the production paginator, rendering one PNG per page.
2. **D3 test:** rendered glyph/stroke positions == engine predictions,
   exactly 0 (same code path).
3. **D4 guard extended to `tools/`** (allowlist only for pure presentation
   constants: colors, stroke widths). 57 stays banned.

### Layer B — same fonts, same algorithm (the fidelity jump)

1. Pull `reMarkableSans-*.ttf` from the device (`scp remarkable-ppm:` from
   the firmware fs; fallback: extract from the binary's qrc) and vendor them
   for tests/tools (license: internal test use only, never shipped).
2. Point `font_metrics` at reMarkableSans. Falsifiable prediction: the 7
   `KNOWN_X_DIVERGENT` corpus sentinels, the 9 `KNOWN_DRIFT_ANCHORS`, and the
   4 `KNOWN_DIVERGENT_HL` strict xfails flip to pass with no new failures.
   If they don't flip, the Phase 6 least-squares fit runs on top.
3. Upgrade `tools/calibration/qt_layout_reference.py` into the **oracle**:
   Qt `QTextDocument` + reMarkableSans + `QTextBlockFormat` fixed line
   heights from the corpus (45.55 body; Title/Heading pitches from the
   Phase 8 heading probe) + `setTextWidth(750)`. Promote W3 to exact
   line-break equality engine-vs-Qt on arbitrary generated text — a local
   substitute for the device on inputs the corpus never saw.

### Layer C — the bench gate (Phase 7, now with teeth)

`uv run python -m rock_paper_sync.bench`:

- `bench render doc.md` → paginated PNGs (< 2 s), `--overlay` line grid /
  wrap points / page breaks / anchor markers with offsets.
- `bench preview --vault <snapshot> --edit <patch>` → runs the REAL pull/
  reanchor/regenerate pipeline offline (no rmfakecloud; call
  `PullSyncEngine._reanchor_annotations` directly on the snapshot) and
  renders before/after. This is the "what will my sync do" button.
- `bench diff a.png b.png|--corpus <fixture>` → heat-map; also dumped by any
  failing visual test.

## 4. How each 2026-07-05 bug class becomes impossible

| Bug class | Structural guard |
|---|---|
| Stroke stacking (5 anchors → 1) | Production rule: a reanchor strategy that maps ≥2 previously-distinct bundles to one offset is a FAILURE (orphan path), enforced in `stroke_handler`; plus offline trip replay test asserting anchor-cardinality preservation per page; plus cluster-preserving resolution (one offset per original anchor line) |
| Split glyph ('t' bar) | Same cluster-preserving rule: sibling bundles of one anchor line get one resolved offset by construction |
| Restack-moves-again | Same; also bench preview renders the stack before any device sees it |
| Page inflation (23/32 fill) | Generator-vs-engine page-count equality test on the corpus + fixture docs; bench overlay shows page-break line |
| Metric drift (T4/W2) | Device's own font files; strict xfails must flip, Qt oracle holds the line thereafter |
| Renderer self-agreement | D3 zero-delta wiring test + D4 guard extended to tools/ |
| Vacuous visual verdicts | already fixed (calibrated pHash, `test_visual_verdict_calibration`) |

## 5. Calibration protocol for the replica

1. Render each corpus fixture locally → downscale to 384×512 → compare to
   the device thumbnail (`rm_files/<doc>/*.png`): pHash distance per fixture,
   thresholds set by the perturbation calibration (one-line shift must fail).
   These are renderer-fidelity gates, not layout gates (P2: numbers stay the
   primary oracle; thumbnails catch what .rm numbers cannot — glyph shapes,
   markers).
2. Numeric gates stay primary: corpus differential (x, paragraph-relative y),
   positional certification (highlight rects, anchor_origin_x).
3. Every constant the replica needs but the corpus hasn't measured (Title/
   Heading pitch, list marker geometry, paragraph spacing B1, baseline T5)
   is already on the Phase 8 probe list — the replica consumes them from
   `profile.json` when they land, and until then carries a strict xfail per
   unmeasured property (no silent guesses).

## 6. Execution order

1. **B1-fonts + A** first (they unblock everything and are pure-local):
   pull fonts, flip metrics, rewire renderer, D3/D4-tools. Expect xfail
   flips as the acceptance test.
2. **C bench** (render + preview + diff), reusing A/B.
3. Stroke-handler cluster-preserving fix + fail-loudly rule, certified by an
   offline replay of the recorded trips 1→2 (the stacking session becomes
   the regression test — it must produce ≥ the trip-1 anchor cardinality or
   orphans, never a stack).
4. Phase 8 device session (fixtures 08–13 + heading/list/spacing probes +
   re-record comprehensive_strokes with a ≤50-char fixture).
