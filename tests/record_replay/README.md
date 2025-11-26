# Record/Replay Test Framework

This guide explains the record/replay test system for validating annotation and OCR features on reMarkable devices.

## Overview

Tests are organized by feature, each with:
- **Test file**: `test_[feature].py` (e.g., `test_highlights.py`)
- **Fixture document**: `fixtures/test_[feature].md` (e.g., `fixtures/test_highlights.md`)
- **Testdata**: `testdata/[feature]/` (e.g., `testdata/highlights/`)

| Feature | Test | Fixture | Testdata |
|---------|------|---------|----------|
| Highlights | `test_highlights.py` | `test_highlights.md` | `testdata/highlights/` |
| OCR Handwriting | `test_ocr_handwriting.py` | `test_ocr_handwriting.md` | `testdata/ocr_handwriting/` |
| Pen Colors | `test_pen_colors.py` | `test_pen_colors.md` | `testdata/pen_colors/` |
| Pen Widths | `test_pen_widths.py` | `test_pen_widths.md` | `testdata/pen_widths/` |
| Pen Tools | `test_pen_tools.py` | `test_pen_tools.md` | `testdata/pen_tools/` |

## Quick Start

### Running Tests (Offline Mode - No Device Needed)

```bash
# Run all offline tests with pre-recorded testdata
uv run pytest tests/record_replay/ --device-mode=offline

# Run specific test
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsReplay --device-mode=offline

# List available testdata artifacts
uv run pytest tests/record_replay/ --list-tests
```

### Recording Tests (Online Mode - Real Device)

```bash
# Record highlights with your device
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsRecording::test_record_highlights \
    --device-mode=online --online -s

# After recording, replay offline
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsReplay --device-mode=offline
```

## Recording Instructions

### Prerequisites

**Offline Recording (rmfakecloud)**:
1. Start rmfakecloud: `podman run -d -p 3000:3000 ddvk/rmfakecloud:latest`
2. Credentials: `tests/fixtures/rmfakecloud.json`

**Online Recording (Real Device)**:
1. Configure device: `uv run rock-paper-sync init`
2. Device credentials: `~/.config/rock-paper-sync/device-credentials.json`
3. Test vault in `~/.config/rock-paper-sync/config.toml` (to avoid polluting production vaults)

### Recording Highlights

```bash
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsRecording::test_record_highlights \
    --device-mode=online --online -s
```

**On Device**:
1. Open document
2. Select Highlight tool
3. Highlight text (try multiple colors: Yellow, Green, Pink, Blue)
4. Create overlapping highlights
5. Press Enter to continue

### Recording OCR Handwriting

```bash
uv run pytest tests/record_replay/test_ocr_handwriting.py::TestOCRHandwritingRecording::test_record_ocr_handwriting \
    --device-mode=online --online -s
```

**On Device**:
1. Select Ballpoint Pen tool
2. Write in the provided gaps:
   - Section 1: `hello`
   - Section 2: `2025`
   - Section 3: `quick test`
   - Section 4: `Code 42`
   - Section 5: `The quick brown fox`
3. Write clearly (improves OCR accuracy)
4. Press Enter to continue

### Recording Pen Colors

```bash
uv run pytest tests/record_replay/test_pen_colors.py::TestPenColorsRecording::test_record_pen_colors \
    --device-mode=online --online -s
```

**On Device**:
1. Switch between pen colors (Black, Red, Blue, Green, Yellow, Pink)
2. Write the color name in that color
3. Use at least 3-4 different colors
4. Press Enter to continue

### Recording Pen Widths

```bash
uv run pytest tests/record_replay/test_pen_widths.py::TestPenWidthsRecording::test_record_pen_widths \
    --device-mode=online --online -s
```

**On Device**:
1. Select Ballpoint Pen
2. Draw lines with varying pressure:
   - Light pressure (thin)
   - Normal pressure (medium)
   - Heavy pressure (thick)
   - Variable pressure (thin → thick → thin)
3. Draw multiple parallel strokes
4. Press Enter to continue

### Recording Pen Tools

```bash
uv run pytest tests/record_replay/test_pen_tools.py::TestPenToolsRecording::test_record_pen_tools \
    --device-mode=online --online -s
```

**On Device**:
1. Select each tool and write with it:
   - Ballpoint
   - Fineliner
   - Marker
   - Pencil
   - Mechanical Pencil
   - Calligraphy
2. Write the tool name with that tool
3. Try to use all 6 tools
4. Press Enter to continue

## Testdata Structure

After recording, testdata is stored at `tests/record_replay/testdata/[feature]/`:

```
testdata/[feature]/
├── manifest.json              # Test metadata
├── source.md                  # Original markdown
└── phases/
    ├── phase_0_initial/       # Initial vault state
    │   ├── vault_snapshot/
    │   │   └── source.md
    │   └── phase_info.json
    └── phase_1_final/         # After annotations
        ├── vault_snapshot/
        │   └── source.md
        ├── device_state.json
        ├── phase_info.json
        └── rm_files/
            └── [page_uuid].rm
```

## Expected Results

After syncing annotations/OCR, you should see markers in the markdown:

**Highlights** (preserve color, position, overlaps):
```markdown
The <!-- ANNOTATED: uuid=abc123 -->highlighted<!-- /ANNOTATED --> text
```

**OCR** (preserve recognized text, confidence, original content):
```markdown
<!-- OCR: uuid=abc123 confidence=0.95 -->
hello
<!-- /OCR -->
```

## Troubleshooting

### "Testdata not available"
Run the recording test first with `--device-mode=online --online -s`

### "Sync fails"
Check:
1. rmfakecloud health: `curl http://localhost:3000/health`
2. Credentials exist: `tests/fixtures/rmfakecloud.json`
3. Device is responsive

### "Test hangs waiting for input"
Press Enter to continue, or Ctrl+C to abort

### "Re-uploaded" errors
Indicates hash instability - OCR/annotation markers should not trigger re-uploads.
Check the sync logs at `workspace/logs/sync.log`

## CI Integration

Tests adapt to available resources:

```bash
# CI: Offline mode (no device needed)
uv run pytest tests/record_replay/ --device-mode=offline

# Dev: Online mode (real device)
uv run pytest tests/record_replay/ --device-mode=online --online -s
```

## Adding New Tests

1. Create fixture: `tests/record_replay/fixtures/test_[name].md`
2. Create test file: `tests/record_replay/test_[name].py`
3. Record testdata: `--device-mode=online --online -s`
4. Verify testdata: Check `tests/record_replay/testdata/[test_id]/`
5. Verify replay: `--device-mode=offline`

## Architecture

```
Recording (Online):
Device → Sync → rmfakecloud → .rm files → OnlineDevice → Testdata (tests/record_replay/testdata/)

Replay (Offline):
Testdata → .rm files → OfflineEmulator → rmfakecloud → Test Assertions
```

## References

- `tests/record_replay/harness/online.py` - OnlineDevice recording
- `tests/record_replay/harness/offline.py` - OfflineEmulator replay
- `tests/record_replay/conftest.py` - Test fixtures
- `tests/record_replay/harness/testdata.py` - Testdata loading/storage
