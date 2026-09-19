# Layout Principles

Guiding principles for all work on document layout (pagination, word wrap,
annotation positioning, rendering). These exist because the project has
repeatedly burned time iterating in circles: guessing a layout constant,
syncing to the device, eyeballing the result, and committing a half-confirmed
fix. The commit history (line height 35px → 50px → 68px → 57px; repeated
stroke-Y fixes) is the evidence.

Every layout change must be able to answer: **which principle does this
follow, and which test proves it?**

## P1. The device is a data source, not a test loop

Device round trips happen only to **record ground truth** — scripted, batched,
and rarely (per device model / firmware version). All iteration happens
locally against recorded ground truth. If you find yourself syncing to the
device to check whether a code change "looks right," stop: the ground-truth
corpus is missing a case. Add the case to the corpus instead.

## P2. Ground truth is machine-readable, not eyeballed

Prefer oracles that yield **numbers**, not pixels:

- Highlights made on-device store absolute glyph rectangles
  (`SceneGlyphItemBlock`). A highlight over a known word is a device-signed
  statement of exactly where those characters are. This is the primary
  calibration instrument.
- Device-native `.rm` files (`RootTextBlock.pos_x/pos_y/width`) are
  authoritative for text-frame parameters.
- Thumbnails / PNG comparison are a last resort, used only for properties
  that leave no trace in the `.rm` file.

A failure should read "predicted y=348, device says y=405, delta = exactly
one line" — never "it looks half a line off."

## P3. One layout engine

`rock_paper_sync.layout.WordWrapLayoutEngine` (+ `DeviceGeometry`) is the
**only** implementation of wrapping, line positioning, and character
measurement. The generator, annotation handlers, renderer
(`tools/rmlib/renderer.py`), and any bench tooling consume it. No component
may reimplement char-to-line mapping or carry its own fallback constants.
A guard test enforces that layout magic numbers appear only in
`src/rock_paper_sync/layout/`.

Corollary: because the renderer shares the engine, a local render is a
faithful preview *by construction*. Any residual disagreement with the device
is a model error (fix the engine + spec), never a drift error.

## P4. Every constant has provenance and a test

A layout constant may exist only if it has:

1. an entry in `docs/LAYOUT_SPEC.md` with device model, firmware, and
   calibration date;
2. a differential test that re-derives or verifies it from the ground-truth
   corpus.

A constant that two places disagree on (e.g. baseline offset 20 vs 25) is a
bug in itself, independent of which value is right.

## P5. Hypotheses are falsified in batches, locally

When the model is wrong, don't fix one symptom and re-sync. Formulate the
hypothesis ("the device wraps at layout_text_width=758, not text_width=750"),
express it as a spec invariant, and validate it against the *entire* corpus
in one local test run. One corpus refresh can falsify dozens of hypotheses;
one device eyeball falsifies one.

## P6. The spec is falsifiable or it is deleted

`docs/LAYOUT_SPEC.md` contains numbered invariants, each cross-referenced to
the test that checks it. Prose that cannot fail a test is history, and
belongs in `docs/archive/`. When device firmware changes, the corpus refresh
must fail loudly on exactly the invariants that broke.

## P7. Fail toward the corpus

When local prediction and device ground truth disagree, the corpus wins until
proven mis-recorded. Never "fix" a test by loosening a tolerance; either the
model is wrong (fix engine + spec) or the recording is wrong (re-record and
document why).

## P8. Fast feedback is a feature requirement

The local bench (render + overlay of predicted line boxes, wrap points,
anchors, page breaks) must run in under a couple of seconds on a single
markdown file. If iteration is slow, people fall back to guess-and-sync —
speed is what makes the discipline stick.

## Related documents

- `docs/LAYOUT_SPEC.md` — the invariants themselves
- `docs/LAYOUT_TESTBENCH_PLAN.md` — implementation plan
- `docs/RENDERER_COORDINATE_MODEL.md`, `docs/RMSCENE_FINDINGS.md` —
  historical calibration narrative (to be superseded by the spec)
