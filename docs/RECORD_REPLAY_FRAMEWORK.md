# Record/Replay Test Framework

## Overview

The record/replay framework provides infrastructure for device-based testing of rock-paper-sync with real reMarkable devices. It supports two modes:

- **Online Mode**: Interactive tests with a physical reMarkable device
- **Offline Mode**: Automated replay using pre-recorded test artifacts

## Architecture

### Components

```
harness/
├── base.py              # DeviceTestCase, device_test decorator
├── logging.py           # Bench logger, backward compat for bench.py
├── output.py            # Colors, print utilities
├── command.py           # Command execution (run_cmd, run_sync)
├── protocol.py          # DeviceInteractionManager abstract class
├── online.py            # OnlineDevice (real device interaction)
├── offline.py           # OfflineEmulator (testdata replay)
├── vault_online.py      # Vault interaction with real device
├── vault_offline.py     # Vault interaction with testdata
├── workspace.py         # WorkspaceManager (test workspace setup)
├── testdata.py          # TestdataStore (artifact management)
├── golden_comparison.py # Golden file comparison
├── prompts.py           # User interaction prompts
└── ocr_integration.py   # OCR mocking and recording

scenarios/
├── ocr_tests.py         # Interactive OCR tests
└── ocr_replay_tests.py  # OCR tests with mocking
```

### Execution Flow

```
TestClass (extends DeviceTestCase)
    ↓
run() method
    ├─ setup() [optional]
    ├─ execute() [required - your test logic]
    └─ teardown() [optional]
    ↓
managed_run() context manager
    ├─ Timing measurement
    ├─ Setup/teardown calls
    ├─ Exception handling
    └─ Result recording
    ↓
DeviceTestResult (stored for later analysis)
```

## Creating a New Test

### 1. Basic Test Structure

```python
from pathlib import Path
from ..harness.base import DeviceTestCase, device_test

class MyNewTest(DeviceTestCase):
    """Description of what this test does."""

    name = "my-test"  # Unique test ID
    description = "What the test verifies"
    requires_ocr = False  # Set True if OCR needed

    @device_test(cleanup_on_success=True)
    def execute(self) -> bool:
        """Execute the test logic.

        Returns:
            True if test passed, False otherwise
        """
        # Your test code here
        ret, out, err = self.sync("Sync documents")
        return ret == 0
```

### 2. Available Methods

**Sync Operations:**
```python
# Run sync and get output
ret, out, err = self.sync("Description of sync")

# Get current document content
content = self.workspace.get_document_content()
```

**User Interaction (online mode only):**
```python
from ..harness.prompts import user_prompt

# Prompt user for action
if not user_prompt("Write annotations", [
    "Step 1: Do something",
    "Step 2: Do something else",
]):
    return False  # User cancelled
```

**Logging:**
```python
# Log observation (cyan text)
self.bench.observe("Something happened")

# Log error (red text)
self.bench.error("Error occurred")

# Print formatted headers
self.bench.header("TEST SECTION")
self.bench.subheader("Subsection")
```

**Access Workspace:**
```python
# Configuration
config = self.workspace.config
vault_dir = self.workspace.vault_dir
test_doc = self.workspace.test_doc

# Device folder name
device_folder = self.workspace.device_folder

# State manager
state_manager = self.workspace.state_manager
```

### 3. Test Lifecycle

```
Test Creation
    ↓
__init__(workspace, bench)  # Set up test context
    ↓
setup()  # Optional: prepare test environment
    ↓
execute()  # Required: run test logic
    ├─ sync() calls
    ├─ Assertions
    └─ Return True/False
    ↓
teardown()  # Optional: cleanup and verification
    ↓
DeviceTestResult recorded with:
    ├─ name, success, duration
    ├─ observations (all bench.observe calls)
    ├─ errors (all bench.error calls)
    └─ skipped status
```

## OCR Integration

### Quick Start

Add `OCRIntegrationMixin` to automatically mock the OCR service:

```python
from ..harness.ocr_integration import OCRIntegrationMixin

class MyOCRTest(OCRIntegrationMixin, DeviceTestCase):
    name = "ocr-test"
    requires_ocr = True
    ocr_record_results = True

    # Map annotation UUIDs to expected OCR text
    ocr_expected_texts = {
        "annotation-uuid-1": "hello world",
        "annotation-uuid-2": "2025",
    }

    @device_test(requires_ocr=True)
    def execute(self) -> bool:
        self.sync("Sync with OCR")
        # OCR mocking is automatic
        # Teardown verifies results and records to JSON
        return True
```

### How It Works

**Setup Phase:**
- Patches `rock_paper_sync.ocr.factory.create_ocr_service` with mock
- Configures mock to return texts from `ocr_expected_texts`
- Initializes recording data structures

**Execution Phase:**
- When OCR processor calls `recognize_batch()`, mock intercepts
- Returns mocked text for each annotation UUID
- Records all requests and responses (UUID, image hash, paragraph index, text, confidence)

**Teardown Phase:**
- Verifies all expected texts appear in vault output
- Saves OCR recording to `tests/testdata/record_replay/ocr_results/{test_name}.json`
- Restores real OCR service

### Configuration

```python
class MyOCRTest(OCRIntegrationMixin, DeviceTestCase):
    requires_ocr = True              # Enable OCR mocking
    ocr_expected_texts = {...}      # Required: map UUID → expected text
    ocr_record_results = True        # Optional: save recording (default True)
```

### Finding Annotation UUIDs

**From OCR recording (easiest):**
```bash
# Run test once, then extract UUIDs from result:
jq '.requests[].annotation_uuid' tests/testdata/record_replay/ocr_results/test.json
```

**From .rm file annotations:**
```python
from rock_paper_sync.annotations import read_annotations
annotations = read_annotations(rm_file)
for ann in annotations:
    print(f"{ann.uuid}: {ann.type}")
```

### Advanced Patterns

**Access recording during test:**
```python
@device_test(requires_ocr=True)
def execute(self) -> bool:
    self.sync("Sync with OCR")
    recording = self.get_ocr_recording()
    self.bench.observe(f"Processed {len(recording.results)} annotations")
    return True
```

**Custom OCR service behavior:**
```python
def _setup_ocr_service(self):
    super()._setup_ocr_service()

    def custom_recognize(request):
        text = self.ocr_expected_texts.get(request.annotation_uuid, "default")
        from rock_paper_sync.ocr.protocol import OCRResult
        return OCRResult(
            annotation_uuid=request.annotation_uuid,
            text=text,
            confidence=0.95,
            model_version="custom-v1",
            bounding_box=request.bounding_box,
            context=request.context,
            processing_time_ms=75,
        )

    from unittest.mock import MagicMock
    self._ocr_service_mock.recognize_batch = MagicMock(
        side_effect=lambda reqs: [custom_recognize(r) for r in reqs]
    )
```

**Custom verification:**
```python
def _verify_ocr_results(self) -> None:
    super()._verify_ocr_results()  # Standard verification

    # Custom checks
    recording = self.get_ocr_recording()
    low_confidence = [r for r in recording.results if r.confidence < 0.90]
    if low_confidence:
        self.bench.warn(f"Low confidence results: {len(low_confidence)}")
```

**Conditional verification:**
```python
def teardown(self):
    recording = self.get_ocr_recording()
    if len(recording.results) > 0:
        super().teardown()  # Verify
    else:
        self.bench.observe("No OCR results - skipping verification")
        if hasattr(self, '_ocr_patch'):
            self._ocr_patch.stop()
```

### Recording Format

Saved to: `tests/testdata/record_replay/ocr_results/{test_name}.json`

```json
{
  "test_id": "ocr-test",
  "test_name": "ocr-test",
  "requests": [
    {
      "annotation_uuid": "uuid-1",
      "image_hash": "abc12345",
      "paragraph_index": 0,
      "timestamp": "2025-11-25T10:30:45.123456"
    }
  ],
  "results": [
    {
      "annotation_uuid": "uuid-1",
      "text": "hello world",
      "confidence": 0.95,
      "model_version": "mocked-test-v1",
      "timestamp": "2025-11-25T10:30:45.234567"
    }
  ],
  "created_at": "2025-11-25T10:30:45.345678"
}
```

**Analyze recordings:**
```bash
# Count annotations processed
jq '.results | length' tests/testdata/record_replay/ocr_results/*.json

# Extract recognized texts
jq '.results[].text' tests/testdata/record_replay/ocr_results/*.json

# Compare recordings
diff <(jq '.results | sort_by(.annotation_uuid)' v1.json) \
     <(jq '.results | sort_by(.annotation_uuid)' v2.json)
```

## Using Local OCR Service

Tests can use a real minimal OCR service instead of mocking:

```python
def test_ocr_with_real_service(ocr_service):
    """Use actual OCR service for integration testing."""
    from rock_paper_sync.ocr.protocol import OCRRequest, BoundingBox, ParagraphContext
    from PIL import Image
    import io

    # Create test image
    img = Image.new('RGB', (100, 100), color='white')
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')

    # Process with OCR
    request = OCRRequest(
        image=img_bytes.getvalue(),
        annotation_uuid="test-1",
        bounding_box=BoundingBox(x=0, y=0, width=100, height=100),
        context=ParagraphContext(
            document_id="doc-1",
            page_number=1,
            paragraph_index=0,
            paragraph_text="test"
        ),
    )

    result = ocr_service.recognize(request)
    assert result.text is not None
```

**The `ocr_service` fixture:**
- Auto-starts minimal OCR container via podman-compose
- Returns deterministic dummy results (lightweight, no GPU needed)
- Cleans up after test
- Fast startup (~5 seconds per test)

**When to use:**
- Integration tests needing real OCR behavior
- Tests verifying OCR result processing
- When mocking is insufficient

**When to use OCRIntegrationMixin instead:**
- Record/replay device tests
- Unit tests of OCR integration
- When you need to verify specific expected texts

See `docker/ocr-minimal/README.md` for container details.

## Running Tests

### Online Mode (Real Device)

```bash
# Run single test
uv run pytest tests/record_replay/scenarios/ocr_tests.py::OCRRecognitionTest --device-mode=online

# Run all device tests
uv run pytest tests/record_replay --device-mode=online

# With verbose output
uv run pytest tests/record_replay --device-mode=online -v -s
```

**Requirements:**
- Physical reMarkable device
- Device connected to rmfakecloud
- User available for prompts (manual actions)

### Offline Mode (Replay)

```bash
# Run single test with testdata
uv run pytest tests/record_replay/scenarios/ocr_tests.py::OCRRecognitionTest --device-mode=offline

# Run all offline tests
uv run pytest tests/record_replay --device-mode=offline

# Don't cleanup workspace on failure
uv run pytest tests/record_replay --device-mode=offline --no-cleanup
```

**Requirements:**
- None! Fully automated
- Uses rmfakecloud container
- Pre-recorded testdata artifacts

### Configuration

Tests read config from: `tests/record_replay/fixtures/config.toml`

```toml
[sync]
vaults = [
    { name = "test-vault", path = "/tmp/vault", remarkable_folder = "Test" }
]
state_database = "/tmp/state.db"
debounce_seconds = 1

[cloud]
base_url = "http://localhost:3000"  # rmfakecloud URL

[ocr]
enabled = true
provider = "runpods"
```

## Test Data Management

### Testdata Store

Manages artifacts for replay tests:

```python
from ..harness.testdata import TestdataStore

store = TestdataStore(fixtures_dir)

# Save during online test
store.save_artifacts(
    test_id="annotation-roundtrip",
    doc_uuid="doc-123",
    page_uuids=["page-1"],
    rm_files={"page-1": rm_file_bytes},
    source_markdown="# Document\n...",
    description="Test with user annotations"
)

# Load during offline test
artifacts = store.load_artifacts("annotation-roundtrip")
```

### Golden File Comparison

Compare outputs against reference files:

```python
from ..harness.golden_comparison import GoldenComparison

golden = GoldenComparison("test-id", goldens_dir)

# Compare output
result = golden.compare(output_file)

if result.matches:
    print("✓ Output matches golden")
else:
    print(f"✗ Differences: {result.diff_lines}")
    # First run: creates .actual file
    # Subsequent runs: compares to .golden file
```

## Example Tests

### Simple Sync Test

```python
class SimpleSyncTest(DeviceTestCase):
    name = "simple-sync"
    description = "Basic document sync"

    @device_test()
    def execute(self) -> bool:
        ret, out, err = self.sync("Sync documents")
        if ret != 0:
            self.bench.error(f"Sync failed: {err}")
            return False

        self.bench.observe("Sync completed successfully")
        return True
```

### Test with Verification

```python
class AnnotationRoundtripTest(DeviceTestCase):
    name = "annotation-roundtrip"
    description = "Verify annotations survive sync roundtrip"

    def setup(self):
        """Prepare test document."""
        doc = self.workspace.test_doc
        doc.write_text("# Test\n\nAnnotated paragraph.")

    @device_test()
    def execute(self) -> bool:
        # Initial sync
        if self.sync("Initial sync")[0] != 0:
            return False

        # Simulate annotation
        content = self.workspace.get_document_content()
        if "Annotated" not in content:
            self.bench.error("Content lost in sync")
            return False

        self.bench.observe("Content preserved")
        return True
```

### Test with OCR

```python
class OCRRecognitionTest(OCRIntegrationMixin, DeviceTestCase):
    name = "ocr-recognition"
    requires_ocr = True

    ocr_expected_texts = {
        "handwriting-1": "hello world",
        "handwriting-2": "2025",
    }

    @device_test(requires_ocr=True)
    def execute(self) -> bool:
        ret, _, _ = self.sync("Initial sync")
        if ret != 0:
            return False

        # In online mode, user writes text
        # In offline mode, testdata provides annotations

        ret, _, _ = self.sync("Sync with OCR")
        if ret != 0:
            return False

        content = self.workspace.get_document_content()

        # Mixin's teardown() verifies OCR texts appear
        return "<!-- RPS:ANNOTATED" in content or "<!-- RPS:OCR -->" in content
```

## Best Practices

### 1. Test Naming
- Use descriptive test names: `annotation-roundtrip`, not `test1`
- Prefix with feature: `ocr-recognition`, `vault-multi-sync`
- Make name unique within scenarios directory

### 2. Error Handling
```python
ret, out, err = self.sync("Description")
if ret != 0:
    self.bench.error(f"Sync failed: {err}")
    return False  # Fail the test
```

### 3. Logging
```python
# Observations are captured and saved
self.bench.observe(f"Found {count} documents")

# Errors are recorded
if not condition:
    self.bench.error("Condition not met")
    return False
```

### 4. Online vs Offline

Handle both modes gracefully:

```python
@device_test()
def execute(self) -> bool:
    # This code runs in both modes
    ret, _, _ = self.sync("Sync")

    # For online-only steps:
    if self.workspace.is_online:
        if not user_prompt("Write annotations", [...]):
            return False

    # Rest of test works in both modes
    return ret == 0
```

### 5. Cleanup

```python
class MyTest(DeviceTestCase):
    cleanup_on_success = True   # Remove workspace on success
    cleanup_on_failure = False  # Keep workspace on failure (for debugging)

    def teardown(self):
        # Optional cleanup logic
        self.bench.observe("Test cleanup")
```

## Debugging

### Keep Failed Workspace

```bash
# Don't cleanup failed test workspace
uv run pytest tests/record_replay/... --device-mode=offline --no-cleanup
```

### Verbose Output

```bash
# Show all bench logging
uv run pytest tests/record_replay/... -v -s
```

### Check OCR Recordings

```bash
# View OCR request/response recording
cat tests/testdata/record_replay/ocr_results/ocr-test.json | jq .
```

### Inspect Workspace State

```bash
# After failed test with --no-cleanup
ls -la /tmp/rock-paper-sync-test/
cat /tmp/rock-paper-sync-test/test.md  # Document content
```

## Troubleshooting

### Test Skipped
- Check fixtures and dependencies exist
- Verify required config is set
- Check `@device_test()` decorator is used

### Sync Fails
- Check rmfakecloud is running: `docker ps | grep rmfakecloud`
- Verify config points to correct endpoint (http://localhost:3000)
- Check vault path exists and is writable

### User Prompt Ignored
- Only shown in online mode
- Offline mode skips prompts automatically
- Check `user_prompt()` is called before sync

### OCR Mocking Not Working
- ✓ Set `requires_ocr = True` on test class
- ✓ Set `ocr_expected_texts = {uuid: text, ...}` (required)
- ✓ Use mixin first in inheritance: `class Test(OCRIntegrationMixin, DeviceTestCase)`
- ✓ Add `@device_test(requires_ocr=True)` to execute method
- ✓ If overriding `_setup_ocr_service()`, call `super()._setup_ocr_service()`

### OCR Expected Text Not Found
- Check actual text produced: `jq '.results[].text' tests/testdata/record_replay/ocr_results/test.json`
- View full vault output: `cat /tmp/rock-paper-sync-test/test.md` (with `--no-cleanup`)
- Verify `ocr_expected_texts` dict keys match actual annotation UUIDs
- Extract UUIDs from recording: `jq '.requests[].annotation_uuid' tests/testdata/record_replay/ocr_results/test.json`

### OCR Recording Not Saved
- ✓ `ocr_record_results = True` (default)
- ✓ Test actually runs (check for errors in output)
- ✓ Workspace exists and is writable
- ✓ teardown() completes (no exceptions thrown)

## Files Reference

| File | Purpose |
|------|---------|
| `harness/base.py` | DeviceTestCase, test lifecycle |
| `harness/workspace.py` | Workspace management |
| `harness/logging.py` | Bench logger |
| `harness/ocr_integration.py` | OCR mocking and recording |
| `testdata.py` | Test artifact storage |
| `golden_comparison.py` | Output comparison |
| `scenarios/ocr_tests.py` | Example OCR tests |
| `fixtures/config.toml` | Test configuration |
| `fixtures/testdata/` | Pre-recorded artifacts |
