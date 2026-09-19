# Native Layout Engine

The native typed-text layout engine for reMarkable devices. This code predicts
xochitl's internal Qt text layout to position strokes and annotations on
typed-text notebooks.

**Status:** Isolated from the production `rock_paper_sync` package (Phase 3a
of `docs/PDF_SUBSTRATE_PLAN.md`). Production now uses the PDF substrate
(WeasyPrint renderer). This code is preserved for a potential future
native-notebook project.

## Structure

```
native_layout/
  layout/          # Device geometry, word-wrap engine, paginator
  font_metrics.py  # reMarkableSans font measurement (fonttools)
  fonts/device/    # Vendored reMarkableSans TTFs (proprietary, test-only)
  bench/           # Native render/diff CLI (parse -> generate -> RmRenderer)
  tools/
    rmlib/         # Device-side .rm renderer (PIL-based)
    calibration/   # Qt reference, profile extraction, corpus recording
  tests/           # Layout engine tests, calibration, positional certification
  testdata/        # Calibration corpus, pagination goldens
  docs/            # Layout principles, specs, verification plans
```

## Running tests

```bash
# From the repo root:
uv run pytest native_layout/

# With verbose output:
uv run pytest native_layout/ -v
```

Some tests require the record/replay device harness and are automatically
skipped when run outside that context. Device-interactive tests must be run
from `tests/record_replay/` with `--online -s`.

## Dependencies on `rock_paper_sync`

The following modules are imported from the production package. A future
`git subtree split` would need to vendor or stub these:

- `rock_paper_sync.parser` -- markdown AST (`ContentBlock`, `BlockType`, `parse_content`)
- `rock_paper_sync.coordinates` -- `AnchorResolver`, `PAGE_CENTER_X`, `is_root_layer`
- `rock_paper_sync.rm_file_extractor` -- `RmFileExtractor`
- `rock_paper_sync.generator` -- `RemarkableGenerator` (used by bench and pagination tests)
- `rock_paper_sync.config` -- `LayoutConfig`, `load_config`
- `rock_paper_sync.converter` -- `SyncEngine` (calibration capture only)
- `rock_paper_sync.state` -- `StateManager` (calibration capture only)
- `rock_paper_sync.rm_cloud_client` -- `RmCloudClient` (calibration capture only)

## Splitting into its own repo

```bash
git subtree split --prefix=native_layout -b native-layout-standalone
# Then in a new repo:
git pull /path/to/rock-paper-sync native-layout-standalone
```
