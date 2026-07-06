# Device session runbook (Phase 8)

One scripted session, batched instruments. Everything below is staged and
committed; the operator only pushes, draws/highlights per checklist, and
pulls. Time budget ≈ 60–75 min on-device.

## Preconditions

- Device awake, on wifi, reachable as `remarkable-ppm` (`ssh remarkable-ppm true`).
- rmfakecloud reachable (the record/replay flow used `http://marvin:3000` last
  session).
- Confirm firmware version on device; the pulls get stamped with it. If it
  changed from `20260310084634`, that is FINE — the differential suite will
  enumerate exactly which invariants moved.
- Run everything from the repo root on this branch (all instrumentation and
  the re-authored ≤50-char fixtures are committed here).

## Part A — Calibration corpus (new fixtures 08–13)

```bash
uv run python tools/calibration/record_corpus.py            # push + checklist + pull
# or --push-only now, --pull-only later
```

The checklist covers all fixtures; the NEW on-device work is:

1. **08_t5_anchor_probe**: highlight `ZEBRA41`; pen-down ON the underscores,
   drag straight DOWN ~1 cm (start point is the measurement). Then on the
   MARGIN line: one short vertical stroke in the left margin, one on the word
   `MIDDLE`, one in the right margin.
2. **09_heading_multiline**: highlight `ZEBRA42`–`ZEBRA53` (two per heading
   level, first and second line of each wrapped heading).
3. **10_wrap_boundary_bisection**: highlight `ZEBRA54`–`ZEBRA58` only.
4. **11_longword**: highlight `ZEBRA59`, then the FIRST line of the
   `MMMM…` word, then (separately) its SECOND line.
5. **12_list_marker_probe**: highlight `ZEBRA60`–`ZEBRA65` (first token after
   each list marker).
6. **13_multipage_break**: highlight `ZEBRA66`–`ZEBRA69` (both pages).
7. Old fixtures 01–07: re-highlight all 41 original sentinels ONLY if this is
   a new firmware; on unchanged firmware they may be skipped (profile keeps
   the existing records).

Then:

```bash
uv run python tools/calibration/extract_profile.py \
  --device paper_pro_move \
  --input tests/record_replay/testdata/calibration/paper_pro_move \
  --output tests/record_replay/testdata/calibration/paper_pro_move/profile.json
uv run pytest tests/layout -q
```

`extract_profile` now also writes `stroke_anchors` (TreeNodeBlock anchor +
native bounds + production-rule absolute positions per doc) — the
instrumentation 07 lacked.

## Part B — Annotation scenario re-record (current firmware goldens)

```bash
# rmfakecloud up, device reachable
uv run pytest tests/record_replay/test_comprehensive_strokes.py --online -s
uv run pytest tests/record_replay/test_comprehensive_highlights.py --online -s
uv run pytest tests/record_replay/test_cross_page_reanchor.py --online -s
```

Notes for the operator:

- The fixtures are re-authored: no line wraps on device now; gaps are shorter.
- comprehensive_strokes: trips 1–4 must ALL complete (last time trip 3+ was
  never captured; the golden ended up strokeless). At each "Press Enter"
  prompt, verify on the device FIRST: strokes must sit at DISTINCT positions
  (the stacking bug is fixed; if you ever see a pile of strokes on one line,
  abort and save the screen — that is a finding, not an inconvenience).
- comprehensive_highlights: same flow as last time.

Afterwards, offline certification against the fresh pulls:

```bash
uv run pytest tests/record_replay -q
```

Expected/allowed outcomes (P7 — predicted, not eyeballed):

- `test_positional_certification`: OLD_FIRMWARE_ANCHOR_X_INSET (−2.6) was
  measured on the 2025-12 captures. Fresh current-firmware anchors from the
  corpus already suggest ≈ −3.4…−4.1. If the re-recorded scenario anchors
  agree on a new constant, re-pin the inset with the new measurement and
  provenance (and if it equals the 3.4 glyph pad, promote it into
  DeviceGeometry with a name).
- Strict xfails from the old firmware (golden pitch etc.) will XPASS →
  delete them per their own instructions.

## Open questions this session must answer

| Question | Instrument | Current evidence |
|---|---|---|
| T5 baseline offset (20 vs 25) | 08 stroke start + ZEBRA41 rect | old probe's anchored stroke gives abs top = line_top + 6.55 px, suggesting positive-native-y may be BASELINE-relative (overshoot ~6.5) — 08's controlled stroke decides |
| anchor_origin_x inset | 08 margin strokes + all anchors | old goldens −2.6 uniform; current corpus −3.4…−4.1 (n=4) |
| Wrap width interval (W1) | 10 bisection | engine uses 758; corpus lower bound ~747 |
| Per-level heading pitch + tier map (T3/B2) | 09 pairs | device has only Title/Heading tiers |
| Longword break chars (W2) | 11 in-word highlights | ZEBRA02 line-assignment residual |
| Marker indents (B3) | 12 | single indent depth on device |
| Page-break placement (P1g/P2g) | 13 | model-only |

## After the session

1. Commit corpus pulls + regenerated profile.json + transcript.
2. Re-run `uv run pytest tests -q`; resolve every xfail the new data flips
   (delete, re-pin with measurement, or promote to DeviceGeometry — never
   loosen a tolerance).
3. Update docs/LAYOUT_COVERAGE.md rows that moved from OPEN to measured.
4. Blockquote/italic/link/strikethrough/HR remain unmeasured (no fixture yet
   — they need generator support decisions first; tracked in coverage doc).
