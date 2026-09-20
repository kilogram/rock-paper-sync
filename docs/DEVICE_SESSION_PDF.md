# PDF substrate device session runbook

One recording session: push PDF fixture, annotate, capture, re-push
clean. Time budget: 15-20 minutes on-device.

## Preconditions

- Device awake, connected to rmfakecloud at `rm.int.ktg.nyc` (or
  whatever `config.toml` `[cloud] base_url` points at).
- `ssh remarkable-ppm true` succeeds.
- Run from repo root on branch `pdf-substrate`.
- `just check` green.

## Recording

```bash
uv run pytest tests/record_replay/test_pdf_substrate_calibration.py --online -s
```

The test will pause with prompts. Follow each one:

1. **Highlight** -- find "Highlight Target" section, highlight exactly
   "purple monkey dishwasher engine" with the default highlight colour.
2. **Margin note** -- find "Margin Note Target", write a two-word note
   (e.g. "test note") in the left margin beside that paragraph.
3. **Tick checkbox** -- find the "Checklist" section, tick the checkbox
   of "Tick the second item".
4. **Strikethrough** -- find "Strikethrough Target", draw a horizontal
   line through "DELETE THIS PHRASE".
5. **Caret insertion** -- find "Caret Insertion Target", draw a caret
   under the gap before "here." and write one word above the line.
6. **Trailing blank page** -- swipe to the first ruled blank page,
   write two lines of text.
7. **Add a page** -- use the device menu to add a page after the last
   page, write one word on it.
8. **Verify clean re-push** -- after trip 2 syncs, open the document
   and confirm it shows no ink.

Press Enter after each prompt once the action is done on the device.

## If a step fails

- If the document does not appear on the device, check rmfakecloud
  logs: `podman logs rmfakecloud`.
- If annotations do not sync back, wait 10 seconds then press Enter
  (the harness retries 5 times with 5-second delays).
- If trip 2 sync fails, note the error and press Enter to finish.
  The offline analysis will skip trip-2-specific assertions.

## Offline analysis

After recording, testdata lands at
`tests/record_replay/testdata/pdf_substrate/trips/{1,2}/`.

Run the offline suite:

```bash
uv run pytest tests/record_replay/test_pdf_substrate_calibration.py -v
```

This runs:

- `TestPdfSubstrateReplay::test_pdf_render_determinism` -- I10
- `TestPdfSubstrateReplay::test_highlight_transform_calibration` --
  measured scale/offset vs profile
- `TestPdfSubstrateReplay::test_i8_words_under_highlight` -- I8
- `TestPdfSubstrateReplay::test_scene_info_paper_size` -- SceneInfo
- `TestPdfSubstrateReplay::test_cpages_structure` -- cPages redir
- `TestPdfSubstrateReplay::test_trip2_cpages_rebuilt` -- D5 check
- `TestPdfSubstrateReplay::test_write_report` -- writes report.md

Plus `TestCalibrationSynthetic` (always passes, no testdata needed).

The overlay PDF and PNGs land at
`tests/record_replay/testdata/pdf_substrate/trips/1/_diagnostic/`.

## Session 2: D5 re-push verification (10 minutes)

A standalone session to verify D5 (same-UUID re-push with
`discard_ink=True`).  The harness cleanup from session 1 deleted the
trip-1 document from the cloud, so this pushes a fresh document.

```bash
uv run pytest tests/record_replay/test_pdf_substrate_calibration.py \
    -k test_pdf_substrate_repush_online --online -s
```

Steps:

1. The test pushes a fresh fixture as a PDF document.
2. **Open on device** -- confirm it renders correctly.
3. Optionally draw a mark so there is ink to discard.
4. Press Enter -- the test re-pushes with `discard_ink=True`.
5. **Open on device again** -- confirm it shows no ink.
6. Press Enter -- the test captures `.content` and `.metadata` to
   `trips/2/`.

After the session, the offline `test_trip2_cpages_rebuilt` test will
check whether `cPages` was rebuilt with fresh page ids and what
`metadata.version` the device wrote.

## Commit

After a successful offline run, commit the testdata:

```bash
git add tests/record_replay/testdata/pdf_substrate/
git commit -m "data: PDF substrate calibration session"
```
