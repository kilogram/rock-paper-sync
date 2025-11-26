# Golden File Testing Framework

This framework automatically captures and compares markdown outputs from device tests (both online and offline) against "golden" reference files.

## Overview

**Golden files** are reference outputs stored in `tests/device_bench/fixtures/goldens/`. They serve as the expected output for device tests. When a test runs:

1. **First run (no golden exists)**: Test passes, shows the actual output, and saves it as `.actual` file
2. **Output matches golden**: Test passes silently ✓
3. **Output differs from golden**: Test fails, shows diff, saves actual output as `.actual` file

## Usage in Tests

Use the `golden_comparison` fixture in any device test:

```python
def test_some_device_workflow(workspace, golden_comparison):
    """Test that produces markdown output."""

    # ... run your test ...

    # Compare output
    result = golden_comparison.compare(workspace.test_doc, "test_id")
    golden_comparison.assert_matches(result)
```

## Approving Outputs

When a test output doesn't match (or doesn't exist yet), the test shows:

```
No golden file: test_id.md

To capture this output as golden, run:
  cp tests/device_bench/fixtures/goldens/test_id.actual \
     tests/device_bench/fixtures/goldens/test_id.md
```

Run that command to approve the output as the new golden file.

## Workflow

### First Time: Capture Output

```bash
# Run test - will show output and create .actual file
uv run pytest tests/device_bench/test_offline_replay.py::TestGoldenFileComparison -v -s

# Review the output shown in console
# If correct, approve it:
cp tests/device_bench/fixtures/goldens/ocr_legacy_example.actual \
   tests/device_bench/fixtures/goldens/ocr_legacy_example.md

# Run again - should pass
uv run pytest tests/device_bench/test_offline_replay.py::TestGoldenFileComparison -v
```

### Later: Verify Against Golden

```bash
# Run test - should pass if output matches
uv run pytest tests/device_bench/test_offline_replay.py::TestGoldenFileComparison -v

# If output differs, test shows unified diff:
# ======================================================================
# OUTPUT MISMATCH: ocr_legacy_example.md
# ======================================================================
#
# --- golden/ocr_legacy_example.md
# +++ actual
# @@ -1,5 +1,6 @@
#  # OCR Test Document
#
# ... (diff continues)
```

## Examples

### Example 1: Simple Markdown Output Test

```python
@pytest.mark.offline
def test_ocr_output(workspace, golden_comparison):
    """Test OCR output matches expected format."""

    # Simulate or run OCR workflow
    workspace.test_doc.write_text("# My Doc\n\nSome content with OCR")

    # Compare
    result = golden_comparison.compare(workspace.test_doc, "ocr_test_1")
    golden_comparison.assert_matches(result)
```

### Example 2: Annotation Extraction Test

```python
@pytest.mark.offline
def test_annotation_extraction(workspace, testdata_store, golden_comparison):
    """Test that annotations are properly extracted."""

    artifacts = testdata_store.load_artifacts("ocr_handwriting_legacy")

    # Extract and process annotations
    output = reconstruct_markdown_with_annotations(artifacts)

    # Write and compare
    test_file = workspace.workspace_dir / "annotated.md"
    test_file.write_text(output)

    result = golden_comparison.compare(test_file, "annotation_extraction")
    golden_comparison.assert_matches(result)
```

## File Structure

```
tests/device_bench/
├── fixtures/
│   └── goldens/                    # Golden files directory
│       ├── ocr_legacy_example.md   # Golden (approved)
│       ├── ocr_legacy_example.actual
│       ├── test_1.md
│       ├── test_1.actual
│       └── ...
├── test_offline_replay.py          # Example tests
└── GOLDEN_FILES.md                 # This file
```

## Continuous Integration

In CI, the framework:
- ✓ Passes when output matches existing golden files
- ✓ Fails with diff when output differs
- ✓ Passes on first run (creates .actual file for review)

This allows tests to be run in CI and catch regressions, while supporting manual golden file approval.

## Approving Regressions

If a test fails because output changed:

1. Review the diff shown in test output
2. If change is intentional, approve it:
   ```bash
   cp tests/device_bench/fixtures/goldens/{test_id}.actual \
      tests/device_bench/fixtures/goldens/{test_id}.md
   ```
3. Commit the updated golden file
4. Re-run tests to verify

## Best Practices

- **Use meaningful test IDs**: Choose IDs that describe what's being tested (e.g., `ocr_legacy_annotations`, `annotation_roundtrip_output`)
- **Test full workflows**: Capture the final markdown output after complete sync cycles
- **Review diffs carefully**: Always inspect diffs before approving new goldens
- **Commit goldens**: Check in golden `.md` files to git; ignore `.actual` files
