# Record/Replay Test Recording Guide

This guide explains how to record new test datasets for the record/replay test suite.

## Overview

The test suite is organized around specific annotation features, each with its own test file and testdata:

| Test File | Feature | Fixture | Test ID |
|-----------|---------|---------|---------|
| `test_highlights.py` | Highlight annotations | `test_highlights.md` | `highlights` |
| `test_ocr_handwriting.py` | OCR handwriting recognition | `test_ocr_handwriting.md` | `ocr_handwriting` |
| `test_pen_colors.py` | Multiple pen colors | `test_pen_colors.md` | `pen_colors` |
| `test_pen_widths.py` | Pen width/thickness variation | `test_pen_widths.md` | `pen_widths` |
| `test_pen_tools.py` | Different pen tools | `test_pen_tools.md` | `pen_tools` |

## Prerequisites

### For Online Recording (Real Device)

1. **Device configured in `~/.config/rock-paper-sync/config.toml`**:
   ```bash
   rock-paper-sync init
   # Configure your device and vaults
   ```
   - Device credentials: `~/.config/rock-paper-sync/device-credentials.json`
   - Vault configuration: `~/.config/rock-paper-sync/config.toml`

2. **Test vault configured**: Add a test vault in your config to isolate recording
   ```toml
   [[vaults]]
   name = "TestRecording"
   path = "/path/to/test/vault"
   # ... other vault settings
   ```

3. **Physical device**: Recording requires actual device interaction (can be real device or rmfakecloud at localhost:3000)

### For Offline Replay (No Device)

1. **rmfakecloud running**: Must be accessible at `http://localhost:3000`
   ```bash
   podman run -d -p 3000:3000 ddvk/rmfakecloud:latest
   ```

2. **Test credentials**: Automatically set up from `tests/record_replay/fixtures/rmfakecloud_test_credentials.json`

## Recording a Test

### Step 1: Run the Recording Test

For online recording, run with your actual device credentials:

```bash
# Example: Record highlights (uses your device creds and configured vaults)
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsRecording::test_record_highlights \
    --online
```

⚠️ **Important**: Configure a test vault in your `~/.config/rock-paper-sync/config.toml` before recording. This isolates testdata collection to that vault and prevents polluting your production vaults.

### Step 2: Follow On-Screen Prompts

The test will:
1. Load the fixture markdown document
2. Upload it to the cloud via sync
3. Display user instructions (e.g., "Please annotate on device...")
4. Wait for you to press Enter
5. Perform the annotations on your device
6. Sync to download annotations
7. Capture and save the testdata

### Step 3: Verify Testdata

After recording, verify the testdata was saved:

```bash
# List created testdata
ls -la tests/testdata/collected/[test_id]/

# Check manifest was created
cat tests/testdata/collected/[test_id]/manifest.json
```

Expected structure:
```
tests/testdata/collected/[test_id]/
├── manifest.json              # Test metadata
├── source.md                  # Original markdown
└── phases/
    ├── phase_0_initial/       # Initial vault state
    │   ├── vault_snapshot/
    │   │   └── source.md
    │   └── phase_info.json
    └── phase_1_final/         # After annotation
        ├── vault_snapshot/
        │   └── source.md
        ├── device_state.json
        ├── phase_info.json
        └── rm_files/
            └── [page_uuid].rm
```

## Recording Instructions by Feature

### Highlights (`test_highlights.py`)

```bash
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsRecording::test_record_highlights \
    --online
```

**On Device:**
1. Open the document
2. Select the **Highlight** tool
3. Highlight the text in the document
4. Try multiple colors if possible (Yellow, Green, Pink, Blue)
5. Create overlapping highlights
6. Press Enter to continue

**Expected Results:** Multiple highlight annotations captured across pages

---

### OCR Handwriting (`test_ocr_handwriting.py`)

```bash
uv run pytest tests/record_replay/test_ocr_handwriting.py::TestOCRHandwritingRecording::test_record_ocr_handwriting \
    --device-mode=online --online
```

**On Device:**
1. Select the **Ballpoint Pen** tool
2. Use strokes to write:
   - Section 1: `hello`
   - Section 2: `2025`
   - Section 3: `quick test`
   - Section 4: `Code 42`
   - Section 5: `The quick brown fox`
3. Write clearly for better OCR
4. Press Enter to continue

**Expected Results:** Handwriting strokes captured for OCR processing

---

### Pen Colors (`test_pen_colors.py`)

```bash
uv run pytest tests/record_replay/test_pen_colors.py::TestPenColorsRecording::test_record_pen_colors \
    --device-mode=online --online
```

**On Device:**
1. Open the document
2. Switch between **pen colors**:
   - Black (default)
   - Red
   - Blue
   - Green
   - Yellow
   - Pink/Purple
3. Write the color name in that color
4. Try to use at least 3-4 different colors
5. Press Enter to continue

**Expected Results:** Strokes with multiple color values preserved

---

### Pen Widths (`test_pen_widths.py`)

```bash
uv run pytest tests/record_replay/test_pen_widths.py::TestPenWidthsRecording::test_record_pen_widths \
    --device-mode=online --online
```

**On Device:**
1. Select the **Ballpoint Pen**
2. Draw lines with **varying pressure**:
   - Light pressure (thin)
   - Normal pressure (medium)
   - Heavy pressure (thick)
   - Variable pressure (thin → thick → thin)
3. Draw multiple parallel strokes
4. Press Enter to continue

**Expected Results:** Strokes with varying thickness values preserved

---

### Pen Tools (`test_pen_tools.py`)

```bash
uv run pytest tests/record_replay/test_pen_tools.py::TestPenToolsRecording::test_record_pen_tools \
    --device-mode=online --online
```

**On Device:**
1. Open the Tools menu
2. Select each tool and write with it:
   - Ballpoint
   - Fineliner
   - Marker
   - Pencil
   - Mechanical Pencil
   - Calligraphy
3. Write the tool name with that tool
4. Try to use all 6 available tools
5. Press Enter to continue

**Expected Results:** Strokes with multiple tool types preserved

---

## Replaying Testdata (Offline)

Once testdata is recorded, replay it without a device:

```bash
# Run all offline tests
uv run pytest tests/record_replay/ --device-mode=offline

# Run specific test
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsReplay \
    --device-mode=offline

# Run with testdata validation
uv run pytest tests/record_replay/test_pen_colors.py::TestPenColorsReplay::test_pen_colors_multiple_colors \
    --device-mode=offline -v
```

### Expected Offline Behavior

- Tests automatically skip if testdata not available
- `.rm files` are injected into rmfakecloud
- Sync downloads annotations as if from device
- Assertions validate captured properties (colors, tools, widths, etc.)

## Troubleshooting

### "Testdata not available" Error

**Solution:** Run the recording test first
```bash
# Record
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsRecording \
    --device-mode=online --online

# Then replay
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsReplay \
    --device-mode=offline
```

### Sync Fails During Recording

**Check:**
1. rmfakecloud is running: `curl http://localhost:3000/health`
2. Credentials file exists: `cat tests/record_replay/fixtures/rmfakecloud_test_credentials.json`
3. Device is responsive

### Test Hangs Waiting for Input

**Solution:** Press Enter to continue, or Ctrl+C to abort

## Multi-Phase Testdata

Each test records multiple phases:
- **Phase 0 (initial)**: Vault state before annotations
- **Phase 1 (final)**: Vault state after annotations

This enables:
- Testing vault restoration
- Multi-step workflows
- Incremental sync testing

## CI Integration

Tests automatically adapt to available resources:

```bash
# Offline mode (CI-friendly, no device needed)
uv run pytest tests/record_replay/ --device-mode=offline

# Online mode (requires device or rmfakecloud)
uv run pytest tests/record_replay/ --device-mode=online --online
```

## Adding New Recording Tests

To add a new test:

1. **Create fixture document**: `tests/record_replay/fixtures/test_[name].md`
2. **Create recording test class**: `tests/record_replay/test_[name].py`
   - Inherit from `Test[Name]Recording` (online)
   - Inherit from `Test[Name]Replay` (offline)
3. **Record testdata**: Run with `--device-mode=online --online`
4. **Verify testdata**: Check `tests/testdata/collected/[test_id]/`
5. **Verify replay**: Run with `--device-mode=offline`

## Architecture

```
Recording Flow:
Device → Sync → rmfakecloud → .rm files → OnlineDevice → Testdata Store
                                                          (tests/testdata/collected/)

Replay Flow:
Testdata Store → .rm files → OfflineEmulator → rmfakecloud → Assertions
```

## References

- `tests/record_replay/harness/online.py` - OnlineDevice recording implementation
- `tests/record_replay/harness/offline.py` - OfflineEmulator replay implementation
- `tests/record_replay/conftest.py` - Test fixtures and configuration
