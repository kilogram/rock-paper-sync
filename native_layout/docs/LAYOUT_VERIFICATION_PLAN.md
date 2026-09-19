# Device-Grounded Layout & Annotation Verification — System Plan

Extends `docs/LAYOUT_TESTBENCH_PLAN.md` (Phases 1–3 complete) into a full
verification system covering calibration, calibration verification,
relocation certification, local rendering/image comparison, and firmware
recertification. Governed by `docs/LAYOUT_PRINCIPLES.md` (P1–P8) and
`docs/LAYOUT_SPEC.md`.

**Baseline (verified in-repo, 2026-07-04):**

- Phases 1–3 done: E1–E6 property tests, 41/41-sentinel corpus on firmware
  `20260310084634` (`tests/record_replay/testdata/calibration/paper_pro_move/`,
  per-sentinel highlight rects + thumbnails in `profile.json`), offline
  differential suite (`tests/layout/test_corpus_differential.py`) — all green
  in plain `uv run pytest` (15 strict-xfail instances: 1 static T2
  line-height, 7 sentinels × 2 tests for T4/W2 metric divergence).
- One shared engine already exists and is consumed by generator *and*
  annotation reanchoring (`src/rock_paper_sync/layout/`); the renderer
  (`tools/rmlib/renderer.py`) still carries private math — Phase 4 of the
  testbench plan (D3/D4) is the remaining unification work.
- Corpus falsified `DeviceGeometry.line_height`: measured body pitch is
  **45.55 px**, engine carries **57.0** (`layout/device.py:269`). Refit
  deferred; tracked by strict xfail.
- Relocation goldens: `stroke_reanchor` and `highlight_reanchor`
  `phase_4_golden_native/` contain **real device-captured `.rm`** (2025-12-06,
  pre-current-firmware). *Correction to prior belief:* these were not deleted;
  the deleted goldens are `cross_page_reanchor/trips/golden/` `.rm` files
  (commit `eeea85c`). The live cross-page reanchor test is device-only and
  text-match-only.
- `tests/record_replay/harness/visual_comparison.py`: pHash (hash_size=16)
  exists but the cluster-match path hardcodes `hash_distance=0` — clustered
  stroke comparison is position-only and the visual check is vacuous there.
  No SSIM/pixel-diff.

---

## 1. Architecture

```
                ┌──────────────────────────────────────────────┐
                │  DEVICE (rare, scripted, one session/firmware)│
                │  tools/calibration/record_corpus.py           │
                └──────────────┬───────────────────────────────┘
                               │ pulled .rm + thumbnails + transcript
                               ▼
  ┌────────────────────────────────────────────────────────────────┐
  │ GROUND TRUTH  tests/record_replay/testdata/calibration/<dev>/  │
  │  profile.json (firmware-stamped, per-sentinel rects, anchors)  │
  │  relocation goldens (phase_4_golden_native, golden trips)      │
  └───────┬───────────────────────┬───────────────────────┬────────┘
          │                       │                       │
          ▼                       ▼                       ▼
  Calibration verification  Relocation certification  Visual goldens
  tests/layout/             tests/record_replay/      harness/
  test_corpus_differential  test_{stroke,highlight,   visual_comparison
  test_corpus_extract        cross_page}_reanchor     + bench --diff
          │                       │                       │
          └───────────────────────┴───────────────────────┘
                               ▲ all consume
  ┌────────────────────────────┴───────────────────────────────────┐
  │ ONE ENGINE  src/rock_paper_sync/layout/                        │
  │  DeviceGeometry (constants ← profile.json provenance)          │
  │  WordWrapLayoutEngine · ContentPaginator · font_metrics        │
  │  consumed by: generator, annotation handlers, renderer, bench  │
  └────────────────────────────────────────────────────────────────┘
```

Component boundaries:

- **`src/rock_paper_sync/layout/`** — the only home of layout math and
  constants (P3, D4). Add `DeviceGeometry.from_profile(profile.json)` +
  a consistency test asserting every `DeviceGeometry` field equals (or is
  explicitly derived from) a `profile.json` measurement — constants get
  provenance *by construction*, not by comment.
- **`tools/calibration/`** — device-session tooling only: `record_corpus.py`
  (push/checklist/pull/stamp), `extract_profile.py` (offline extraction; to be
  extended with TreeNodeBlock anchor capture), `qt_layout_reference.py`
  (oracle). Never imported by production code.
- **`tools/rmlib/renderer.py`** — presentation only; all char→(x,y) math
  delegated to the engine (Phase 4). After that, a local render is a faithful
  preview by construction (P3 corollary).
- **`src/rock_paper_sync/bench/`** (new) — CLI: md → .rm → PNG with
  `--overlay` / `--diff`. Consumes engine + renderer; owns no math.
- **`tests/layout/`** — device-free CI: engine invariants (E), differential
  suite (D), extraction unit tests, magic-number guard (D4), renderer wiring
  (D3).
- **`tests/record_replay/`** — scenario-level certification: relocation
  goldens, visual comparison. Offline replay in CI; online mode only during
  scripted sessions.

## 2. Phased execution order

Continues the testbench plan numbering. Dependency spine:
**refit (4a) → unified renderer (4b) → certification harness (5) → font fit
(6) → bench (7) → device session (8) → recertification protocol (9)**.
Everything except Phase 8 is device-free.

### Phase 4a — Line-height refit (production-affecting; do first)

The whole relocation system round-trips symmetrically on 57.0 today, so this
must land *before* relocation certification hardens positions — otherwise we
certify positions we know are wrong by Δ=11.45 px/line.

- Change `DeviceGeometry.line_height` 57.0 → 45.55 (and dependent values:
  `text_baseline_y`, pagination capacity, any `rm_text_block_line_height`
  coupling — audit each field's consumers).
- Flip `test_engine_line_height_matches_device` from strict-xfail to pass.
- Re-run all record/replay offline suites; re-approve goldens whose shift is
  exactly explained by the pitch change (document the arithmetic in the
  commit message — P7: a golden re-approval must be *predicted*, not
  eyeballed).
- Add D1-absolute-y assertions to `test_corpus_differential.py` (currently x
  only; y was blocked on this refit). Falsifiable check: every corpus
  sentinel's predicted y matches device rect y (through the 3.4 px box-top
  pad) within ε = 2.0 px, parametrized per sentinel, divergences
  strict-xfail with measured Δ in line-height multiples.
- Retire the 57 px narrative (`RENDERER_COORDINATE_MODEL.md`,
  `RMSCENE_FINDINGS.md` sections) into `docs/archive/` now that the live
  constant agrees.
- **Baseline offset:** T5 is still open (20 vs 25). The refit does not
  resolve it; keep both values quarantined behind one named constant in
  `layout/` with an `OPEN` spec pointer, and add a strict xfail pinning
  whichever the engine uses against the (future) T5 measurement so Phase 8
  fails loudly on the right test.

**Exit:** differential suite asserts x *and* y; T2 xfail retired; goldens
re-approved with predicted deltas; `uv run pytest` green.

### Phase 4b — Single engine, enforced (testbench Phase 4, unchanged scope)

- Renderer consumes `WordWrapLayoutEngine`/`DeviceGeometry`; delete its
  private wrap/char-to-y logic.
- **D3 wiring test:** renderer glyph positions == engine predictions, 0 px
  (same code path — equality, not tolerance).
- **D4 guard test:** grep-based check that layout literals (45.55, 57, 68,
  758, 750, 234, −375, 15.0, 20/25, 87, 100) appear only under
  `src/rock_paper_sync/layout/`, with a small inline-documented allowlist.
  Include 57 in the banned list *forever* — it is the known-wrong attractor.
- Remove `highlight_handler.py` `avg_char_width = 15.0` fallback; take from
  `LayoutContext`.
- Add `DeviceGeometry.from_profile()` + geometry↔profile consistency test
  (see §1).

**Exit:** one implementation of layout math; D3/D4 green in CI.

### Phase 5 — Relocation certification harness (remediate verification decay)

Goal: highlights and strokes are verified to land at the right **coordinates**
after markdown edits — not counts, not anchor text, not engine-vs-itself.

- **Positional golden comparison** (new, in `harness/golden_comparison.py` or
  a sibling `positional_comparison.py`):
  - For highlights: extract `SceneGlyphItemBlock` rects from our regenerated
    `.rm` and from the device golden `.rm`; assert per-highlight (x, y,
    width) within ε derived from corpus measurement noise (start at 2.0 px
    for x/y, matching D1; width waits on the box-width model — assert it
    loosely with the measured 16–22 px pad band and mark `OPEN`).
  - For strokes: compute **absolute** positions on both sides
    (`anchor_y(part2 via engine) + native_y + baseline_offset`,
    `anchor_origin_x + native_x`) and compare those — never raw native
    coordinates, which are meaningless across differing anchors. Also assert
    the anchor's *text identity*: the characters at `part2` in the new text
    equal those at the old anchor (the stroke-29 failure mode in
    `docs/STROKE_ANCHORING.md`).
- **Apply it to existing goldens:** upgrade `stroke_reanchor`,
  `highlight_reanchor`, `three_way_merge`, `conflicting_edit` offline replays
  from count/text assertions to positional assertions against their
  `phase_4_golden_native` / golden-trip data. These goldens are on the
  2025-12-06 firmware — see the caveat below.
- **Cross-page reanchor:** the golden trips were deleted in `eeea85c` after
  generator changes. Restore from git history *only* as a diagnostic
  reference; the certified golden must be **re-recorded in Phase 8** (the
  generator output they certified no longer exists). Until then the offline
  cross-page test asserts engine-predicted positions (labelled clearly as
  model-consistency, not device-certified) and the live test gains positional
  assertions so the Phase 8 session captures what it needs.
- **Fix `visual_comparison.py`:** the cluster-match path must compute the real
  pHash distance per matched cluster region instead of hardcoding 0. Keep
  position matching as the *pairing* step, pHash as the *verdict* step.
- **Firmware caveat (falsifiable, not hand-waved):** old-firmware goldens are
  valid for *relocation logic* (anchor arithmetic) but embed the 57-era
  layout. After Phase 4a, expected positions for these goldens are the device
  golden values — which were produced by the device itself, so they remain
  ground truth for absolute positions; what changes is our *predicted*
  positions. Where our post-refit output legitimately differs from a golden
  (because the golden's document was generated by the 57-era engine), encode
  the difference as a strict xfail with the predicted delta and schedule the
  re-record in Phase 8. No tolerance loosening.

**Exit:** every relocation scenario in default CI asserts coordinates against
device-recorded data or carries a named strict xfail pointing at Phase 8.

### Phase 6 — Font-metric fit & Qt oracle (testbench Phase 5, resolves T4/W2/W3)

- Least-squares fit of effective pixel size (and, if needed, per-glyph-class
  corrections) against the 41-sentinel corpus glyph rects; store fitted value
  with provenance in `layout/font_metrics.py` (production) and mirror in
  `tools/calibration/qt_layout_reference.py` (oracle).
- Success = the 7 `KNOWN_X_DIVERGENT` sentinels (iii/mmm family) flip from
  strict-xfail to pass with no new failures; W2 `mmm` wrap matches.
- Promote `tests/calibration/test_qt_reference.py` to exact line-break
  equality (W3); if it holds, add engine-vs-Qt differential on generated text
  far larger than the corpus (local oracle for never-synced inputs). If not,
  record the residual as `OPEN` with bounds.

Ordered after Phase 5 deliberately: certification harness work doesn't depend
on the fit, and landing the fit later means the certification suite catches
any regression it introduces.

### Phase 7 — Bench CLI (testbench Phase 6, unchanged scope)

`uv run python -m rock_paper_sync.bench <doc.md>`: parse → generate → render
PNG in <2 s (P8); `--overlay` draws predicted line boxes, wrap points, block
boundaries, page-break line, annotation anchors with labeled offsets;
`--diff <golden.png|--qt>` renders a heat-map/side-by-side diff so a failing
visual test shows *where and by how much*. The diff renderer is also what
failing record/replay visual tests dump to their artifacts dir.

### Phase 8 — The one device session (next firmware, or scheduled)

The current firmware already had its session; every open instrument batches
into the next one. Fully scripted via `record_corpus.py` (extend, don't
fork); one operator checklist; push-only / pull-only split supported.

**Instrumentation changes (all offline-side, build before the session):**

1. Extend `extract_profile.py` to parse **TreeNodeBlock anchors**
   (`anchor_id.part2`, `anchor_origin_x`) and stroke native bounds from
   pulled `.rm` — this is the missing piece that left T5 unresolved. No
   on-device change needed.
2. Extend the sentinel schema in `profile.json` with stroke records:
   sentinel id → anchor offset → anchor text → native bounds → derived
   absolute position.
3. Extend `record_corpus.py` checklist generation to cover the new fixtures
   and the relocation-edit steps below.

**Fixture list (corpus additions under `calibration/<device>/src/`):**

- `08_t5_anchor_probe.md` — underscore line + descender stroke, re-recorded
  **with anchor capture** → resolves T5 (20 vs 25) numerically.
- `09_heading_multiline.md` — H1–H6 each long enough to wrap to 2+ lines;
  adjacent-line highlights within one heading give per-level line pitch →
  closes T3/B2.
- `10_wrap_boundary_bisection.md` — paragraphs engineered (using the Phase 6
  fitted metrics) so a sentinel sits within ~1 px of the predicted wrap
  boundary, several widths bracketing 750/758 → tightens W1 from
  "lower bound ~747" to an interval.
- `11_longword.md` — word > line width (ZEBRA-longword) → verifies
  character-granularity breaking (W2 residual).
- `12_list_marker_probe.md` — highlightable tokens *as* first characters
  after markers at levels 0–2 → marker indentation (B3 residual).
- `13_multipage_break.md` — content straddling the break with sentinels on
  both sides → pins P1g/P2g device-side.
- Strokes over several sentinels at left margin / inline / right margin →
  data for the `anchor_origin_x` open question.

**Relocation goldens (same session):**

- Re-record `cross_page_reanchor` golden trips and refresh
  `stroke_reanchor`/`highlight_reanchor`/`three_way_merge`/`conflicting_edit`
  `phase_4_golden_native` on current firmware + post-refit generator: sync
  doc → operator annotates per checklist → pull → apply scripted markdown
  edit → sync → let the *device* reconcile → pull golden. Each step scripted;
  operator only draws/highlights.

**Operator steps (checklist skeleton):** (1) confirm firmware on device and
that it matches `--expected-firmware` else abort; (2) run
`record_corpus.py --push-only`; (3) on device: highlight every listed
sentinel, draw listed strokes (T5 descender, margin strokes), annotate
relocation docs; (4) `--pull-only`; (5) script applies edits and re-syncs;
(6) operator confirms device reconciliation visible; (7) final pull;
(8) `extract_profile.py` runs, prints per-invariant PASS/CHANGED/NEW table;
(9) commit corpus + profile + transcript.

**Exit:** T5, T3/B2, W1, W2-longword, B3-marker, P1g resolved or bounded in
the spec; relocation goldens current-firmware; zero `ASSERTED` entries that
this session could address.

### Phase 9 — Recertification protocol (document + tooling)

- `docs/RECERTIFICATION.md`: trigger (firmware update / new device model) →
  run the Phase 8 script against a new
  `calibration/<device>/` (or versioned `profile.json` per firmware) → run
  `uv run pytest tests/layout tests/record_replay` offline.
- The differential suite is *already* the change-enumerator (P6): each spec
  invariant is one parametrized test, so a firmware change fails exactly the
  invariants that moved, with measured deltas. Add a small report tool
  (`tools/calibration/profile_diff.py`) that diffs two `profile.json` files
  and prints per-invariant old/new/Δ — the human-readable session summary.
- Profiles are firmware-versioned and append-mostly; `DeviceGeometry`
  binds to one profile explicitly. Strict xfails from a superseded firmware
  are retired only by re-measurement, never edited.

## 3. Falsifiable checks by phase (summary)

| Phase | Check | Oracle |
|---|---|---|
| 4a | per-sentinel predicted y == device rect y ± 2.0 px | corpus rects |
| 4a | golden re-approval deltas == predicted pitch arithmetic | arithmetic in commit |
| 4b | D3: renderer pos == engine pos, exactly 0 | same code path |
| 4b | D4: layout literals only in `layout/` | grep guard |
| 4b | DeviceGeometry fields == profile.json values | profile |
| 5 | highlight rects (x,y) == golden rects ± 2.0 px | device golden .rm |
| 5 | stroke absolute pos == golden absolute pos; anchor text identity holds | device golden .rm |
| 5 | cluster pHash distance actually computed and ≤ threshold | fixed harness |
| 6 | 7 divergent sentinels flip xfail→pass; Qt breaks == device breaks | corpus |
| 8 | T5 baseline = one number; T3 per-level pitch; W1 interval | new probes |
| 9 | firmware change ⇒ failing set == changed invariants, enumerated | differential suite |

## 4. Existing assets: reuse / restore / replace

**Reuse as-is:** corpus + `profile.json` (41/41); engine property tests;
differential suite (extend with y); record/replay harness modules;
`stroke_reanchor`/`highlight_reanchor` `phase_4_golden_native` `.rm`
(real device data — reuse for Phase 5, refresh in Phase 8);
`record_corpus.py`/`extract_profile.py` (extend); `RmRenderer` (rewire);
device thumbnails already in `rm_files/` (secondary goldens).

**Restore (diagnostic only):** deleted `cross_page_reanchor` golden trips
from git history pre-`eeea85c` — reference for the anchor-arithmetic shape,
not a certified golden.

**Replace:** `visual_comparison.py` cluster verdict (hash_distance=0
short-circuit); count/text-only relocation assertions → positional;
renderer private layout math → engine calls; deprecated `rmc`-based
`rm_to_png_bytes` path (delete once bench lands); 57.0 line height.

## 5. Open decisions (recommendations)

1. **Golden source: pulled `.rm` numeric data vs device thumbnails.**
   *Recommend pulled `.rm`* as the certified source (P2: numbers over
   pixels — highlight rects, RootTextBlock, TreeNodeBlock anchors are
   device-signed). Thumbnails remain a secondary golden only for properties
   with no `.rm` trace (actual glyph rendering, marker drawing) — keep them
   in the corpus, compare via bench `--diff`, but never gate CI on
   thumbnail-vs-local-render alone (renderer ≠ xochitl rasterizer; that
   comparison can only be a bounded similarity check, and its threshold is a
   renderer-fidelity statement, not a layout statement).
2. **pHash vs SSIM, and thresholds.** *Recommend keeping pHash-16* (already a
   dependency, already wired) and justifying thresholds empirically instead
   of picking numbers: a small calibration test perturbs a rendered page by
   known deltas (one line pitch, one word shift, one stroke removed) and
   asserts the perturbed distance exceeds the pass threshold while
   re-renders of identical input sit well under it. If pHash can't separate
   a one-line shift from noise at hash_size=16, escalate to region-cropped
   pHash (crop per cluster/block first — the harness already crops), then
   SSIM only if that fails. Thresholds live in one constants module with the
   calibration test as provenance (P4 applied to image comparison).
3. **Refit before certification (sequencing).** *Recommend Phase 4a first*,
   as ordered above. Certifying relocation coordinates on a known-wrong 57 px
   pitch would bake Δ=11.45/line errors into fresh assertions and force a
   second pass.
4. **Old-firmware relocation goldens: keep or re-record?** *Both*: keep as
   ground truth for Phase 5 (device output is authoritative regardless of
   which era generated the input doc), strict-xfail the cases the refit
   legitimately changes, re-record everything in the Phase 8 session.
5. **Per-firmware profiles.** *Recommend* `calibration/<device>/<firmware>/`
   directories (current layout becomes the first firmware dir) so
   recertification is additive and `profile_diff.py` has two files to diff.
   Migration is mechanical; do it in Phase 9, not before.
6. **Where D3/D4/bench land.** D3/D4 in `tests/layout/` (device-free, default
   CI); bench as `src/rock_paper_sync/bench/` with a `python -m` entry point
   (it imports production layout + tools renderer; keeping it in `src/` keeps
   it under ruff/type-checking and D4's guard).

## 6. Assumptions (not verified from the repo)

- The next firmware update is acceptable as the trigger for the Phase 8
  session (no urgent need for a second session on `20260310084634`). If T5 or
  the relocation re-record becomes blocking earlier, the same batched script
  can run once on current firmware instead — still one session per firmware.
- Device highlight box geometry (3.4 px pads, 44.4 px height) is
  firmware-stable enough that Phase 5 tolerances derived from the current
  corpus remain valid until recertification catches any drift.
- Operator time per session is the scarce resource; on-device steps in the
  checklist above (~50 highlights, ~10 strokes, relocation annotations) fit
  in one sitting. If not, the checklist supports `--push-only`/`--pull-only`
  splitting across a day without invalidating the firmware stamp.
