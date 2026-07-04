# Layout Test Bench — Implementation Plan

Concrete plan to make layout iteration fully local, per
`docs/LAYOUT_PRINCIPLES.md` and `docs/LAYOUT_SPEC.md`. Ordered so that each
phase delivers standalone value; one device session total (Phase 2).

## Phase 1 — Engine invariant tests (no device needed)

Pure property tests of `WordWrapLayoutEngine` for spec items E1–E6.

- [ ] `tests/layout/test_engine_invariants.py`: round-trip (E1),
      monotonicity (E2), line-width bound (E3), concatenation stability (E4),
      highlight tiling (E5), determinism (E6). Use hypothesis-style generated
      text (mixed widths, unicode, long words) plus fixed edge cases
      (empty paragraph, single char, all-spaces, word > line width).
- [ ] Fix any engine bugs surfaced; these tests run in core CI forever.

**Exit criteria:** E1–E6 green; failures reproduce locally in <1 s.

## Phase 2 — Ground-truth corpus (one scripted device session)

- [x] Author calibration markdown suite under
      `tests/record_replay/testdata/calibration/paper_pro_move/src/`:
      1. wrapped paragraphs (normal prose, narrow `iii…`, wide `mmm…`,
         numerals, punctuation-heavy, one word > line width);
      2. headings H1–H6 each followed by body text (T3, B2);
      3. nested bullet + ordered lists, 3 levels (B3);
      4. code blocks (B4);
      5. blank-line spacing ladder (B1);
      6. multi-page document with content straddling the page break (P1g);
      7. sentinel words (unique, greppable, `ZEBRA01`…`ZEBRA40`)
         distributed across all of the above — these get highlighted
         on-device.
      → `src/01_wrapped_paragraphs.md`…`06_multipage.md`; `src/README.md`
      holds the sentinel→spec-item map.
- [x] `tools/calibration/record_corpus.py`: sync suite to device via existing
      cloud sync (production `sync` CLI); print an operator checklist
      ("highlight every ZEBRAnn token"); pull resulting `.rm` files via
      existing SSH capture (`tools/analysis/device_capture.py` helpers); stamp
      firmware/xochitl version into `profile.json`.
- [x] Extend `tools/calibration/extract_profile.py` to emit per-highlight
      records: sentinel id → char range → device rect(s) (matched by exact
      `GlyphRange.text`), plus derived measurements (line height per block
      type, wrap width, spacing values for B1–B5, baseline offset via T5
      probe). Offline logic covered by `tests/layout/test_corpus_extract.py`.
- [x] T5 probe: a fixture line of underscores with an on-device handwritten
      descender stroke, to settle the 20-vs-25 baseline contradiction
      (`src/07_t5_probe.md`).
- [x] **Run the session once** (device required); commit corpus + `profile.json`.
      Recorded on firmware `20260310084634`; 41/41 sentinels captured. T5BASE
      raw values (highlight top/height + stroke bounds) are stored but the
      20-vs-25 baseline resolution is deferred to Phase 3 per C4 (strokes are
      anchor-relative). See "Running the device session" below.

**Exit criteria:** corpus checked in; `profile.json` contains measured values
(or explicit nulls) for every OPEN/ASSERTED spec item it can address.

### Running the device session

Everything except the device round-trip is built and tested. To record:

```bash
# 1. Push corpus, print the operator checklist, wait, then pull + stamp:
uv run python tools/calibration/record_corpus.py --device-host <ssh-host>
#    (or split it: --push-only now, --pull-only after highlighting)

# 2. Highlight every ZEBRAnn token + draw the T5 descender (checklist guides you).

# 3. Derive measurements into profile.json:
uv run python tools/calibration/extract_profile.py \
    --device paper_pro_move \
    --input tests/record_replay/testdata/calibration/paper_pro_move \
    --output tests/record_replay/testdata/calibration/paper_pro_move/profile.json
```

## Phase 3 — Differential test suite (offline, CI)

- [ ] `tests/layout/test_corpus_differential.py`: D1 (rect match within
      2 px), D2 (exact line breaks). Parametrized per sentinel; failure
      message reports predicted vs device values and the delta expressed in
      line-height multiples.
- [ ] Update `DeviceGeometry` / spec statuses: flip ASSERTED→VERIFIED with
      test names; resolve T5; fill B1–B5 values from `profile.json`.
- [ ] Retire superseded prose from `RENDERER_COORDINATE_MODEL.md` /
      `RMSCENE_FINDINGS.md` into `docs/archive/` per P6.

**Exit criteria:** zero ASSERTED entries touching text/wrap/highlight
positioning; corpus tests in default `uv run pytest` run.

## Phase 4 — Single engine, enforced

- [ ] Refactor `tools/rmlib/renderer.py` to consume `WordWrapLayoutEngine`
      and `DeviceGeometry` for all char→(x, y) math; delete its private
      wrap/char-to-y logic and local constants.
- [ ] Remove fallback literals elsewhere (e.g.
      `highlight_handler.py` `avg_char_width = 15.0`) — take values from
      `LayoutContext`/geometry.
- [ ] D3 wiring test: renderer glyph positions == engine predictions exactly.
- [ ] D4 guard test: grep-based check that layout literals appear only in
      `src/rock_paper_sync/layout/` (small allowlist file for justified
      exceptions).
- [ ] Re-run record/replay golden comparisons; re-approve goldens if renderer
      output legitimately shifted (document why in commit message).

**Exit criteria:** one implementation of layout math; D3/D4 green.

## Phase 5 — Qt oracle promotion

- [ ] Fit Qt font pixel size (use `QFont.setPixelSize`, not points) by
      least-squares against corpus glyph rectangles; store fitted value in
      `tools/calibration/qt_layout_reference.py` with provenance.
- [ ] Promote `tests/calibration/test_qt_reference.py` from "ratios within
      10%" to **exact line-break equality** against the corpus (W3).
- [ ] If exact equality holds: add a differential test of our engine vs Qt on
      a *generated* text set far larger than the corpus (the local oracle for
      never-synced inputs). If it does not hold, document the residual in the
      spec as OPEN with measured bounds — do not loosen tolerances silently
      (P7).

**Exit criteria:** W3 resolved one way or the other, in the spec.

## Phase 6 — Local bench CLI

- [ ] `uv run python -m rock_paper_sync.bench <doc.md> [--out dir]`:
      parse → generate `.rm` → render PNG via unified renderer, in <2 s (P8).
- [ ] `--overlay`: draw predicted line boxes, wrap points, block boundaries,
      page-break line, annotation anchors, with offsets labeled.
- [ ] `--diff <golden.png|--qt>`: heat-map diff against a device golden or a
      Qt-oracle render.
- [ ] Short usage section in `tests/README.md` + pointer from `CLAUDE.md`.

**Exit criteria:** editing generator/layout code and seeing the effect is a
single local command; no device involved.

## Ongoing rules (post-plan)

- New layout feature ⇒ new spec invariant + corpus fixture (added at next
  refresh) + differential test. No constant lands without provenance (P4).
- Firmware update ⇒ rerun Phase 2 script; failing invariants enumerate
  exactly what changed.

## Effort/order notes

Phases 1, 3, 4 are pure local engineering. Phase 2 is the only device
session and gates Phase 3/5 — schedule it once the fixture suite and
checklist are ready so the session is a one-shot. Phase 6 can start any time
after Phase 4 (it needs the unified renderer to be trustworthy).
