# Device Test Bench

On-device testing for rock-paper-sync annotation and OCR features.

## Overview

This test bench validates the complete annotation sync workflow and OCR integration on a real reMarkable Paper Pro device. Each test is self-contained and end-to-end.

## Prerequisites

1. **reMarkable Device**
   - Connected and accessible
   - Cloud sync enabled

2. **Local Cloud Server**
   - Running at `http://localhost:3000`
   - See main README for setup instructions

3. **OCR Configuration** (for OCR tests only)
   - OCR is automatically enabled in the generated test config
   - **Required**: Set environment variables for Runpods:
     ```bash
     export RUNPODS_API_KEY="your-key"
     export RPS_RUNPODS_ENDPOINT_ID="your-endpoint-id"
     ```
   - Or create `secrets.env` in project root with these values

## Test Categories

### Annotation Tests

Tests for the marker-based annotation sync system:

- **annotation-roundtrip**: Sync clean doc → user annotates → verify markers appear
- **no-hash-loop**: Verify annotation markers don't cause infinite sync loops
- **content-edit**: Edit marked content → verify document re-syncs correctly

### OCR Tests

Tests for handwriting recognition and correction:

- **ocr-recognition**: Write text by hand → OCR → verify recognized text appears
- **ocr-correction**: OCR recognizes text → user corrects → verify correction stored
- **ocr-stability**: Verify OCR markers don't cause infinite sync loops

## Usage

### Run All Tests

```bash
cd tests/device_bench
uv run python bench.py --cleanup
```

The `--cleanup` flag automatically removes test state after completion.

### Run Specific Test

```bash
# Annotation test
uv run python bench.py --test annotation-roundtrip --cleanup

# OCR test
uv run python bench.py --test ocr-recognition --cleanup
```

### Manual State Management

```bash
# Setup workspace
uv run python bench.py --setup

# Reset state between tests
uv run python bench.py --reset

# Run test without cleanup
uv run python bench.py --test ocr-recognition
```

## Test Workflow

Each test follows this pattern:

1. **Setup**: Create test workspace and config
2. **Initial Sync**: Upload document to device
3. **User Action**: Prompt user to perform action on device
4. **Verification**: Download changes and verify results
5. **Cleanup**: Remove test artifacts

## Test Documents

### Annotation Baseline (`fixtures/baseline.md`)

Standard document for annotation testing with:
- Multiple paragraphs
- Target text for highlighting
- Various formatting styles

### OCR Baseline (`fixtures/ocr_baseline.md`)

Document designed for OCR testing with:
- **Gaps for handwriting**: Multiple newlines create space for handwritten text
- **Specific prompts**: Clear instructions on what to write
- **Test cases**: Simple words, numbers, short phrases, mixed content

Example gap structure:
```markdown
## Test 1: Simple Words

Write the word "hello" in the gap below:



The gap above should contain your handwriting.
```

The empty lines create space on the reMarkable where you can write by hand.

## Expected Results

### Annotation Tests

After annotation sync, you should see markers like:
```markdown
**Important**: This text should be <!-- ANNOTATED: uuid=abc123 hash=def456 -->annotated<!-- /ANNOTATED --> correctly.
```

### OCR Tests

After OCR processing, you should see markers like:
```markdown
## Test 1: Simple Words

Write the word "hello" in the gap below:

<!-- OCR: uuid=abc123 confidence=0.95 model=base hash=def456 original_hash=ghi789 -->
hello
<!-- /OCR -->

The gap above should contain your handwriting.
```

## Verification

Tests verify:
- ✓ Markers appear in correct locations
- ✓ Hash stability (no re-upload loops)
- ✓ Content edits trigger re-sync
- ✓ OCR recognizes handwritten text
- ✓ OCR corrections are stored
- ✓ Pattern matching (e.g., "hello", "2025")

## Troubleshooting

### "No markers found"

- Ensure you added annotations on device
- Wait for cloud sync to complete
- Check device folder matches config (`DeviceBench`)

### "No OCR markers found"

- Verify OCR is enabled in config
- Check OCR provider credentials
- Use highlighter to mark gaps where you wrote
- Review logs at `workspace/logs/sync.log`

### "Command failed"

- Verify cloud server is running at `http://localhost:3000`
- Check device connectivity
- Review error messages in output

### "Re-uploaded" errors

- Indicates hash instability bug
- OCR/annotation markers should not cause re-uploads
- File a bug report with test logs

## Test Logs

Results are saved to `workspace/logs/`:
- `{test-name}_{timestamp}.json` - Detailed test results
- `sync.log` - Sync operation logs

## Development

### Adding New Tests

1. Create test function in `bench.py`:
   ```python
   def test_new_feature():
       bench = Bench()
       bench.start_time = time.time()
       bench.header("TEST: New Feature")
       # ... test logic ...
       bench.save_result("new-feature", True)
       return True
   ```

2. Add to `TESTS` dict:
   ```python
   TESTS = {
       # ...
       'new-feature': test_new_feature,
   }
   ```

3. Update docstring and `run_suite()` description

### Creating Test Fixtures

Add fixture files to `fixtures/`:
- Use `.md` extension
- Include clear instructions for manual steps
- Use gaps (multiple newlines) for handwriting areas
- Consider UTF-8 whitespace for special spacing needs

## Best Practices

- Always use `--cleanup` for repeatable tests
- Wait for device cloud sync between steps
- Use highlighter to mark handwritten areas for OCR
- Write clearly and follow test instructions
- Run tests individually when debugging
- Check logs for detailed error information
