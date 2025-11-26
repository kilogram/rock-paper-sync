# OCR System Architecture

## Overview

The OCR system enables automatic recognition of handwritten text in reMarkable annotations. It consists of two main components:

1. **Client** (`src/rock_paper_sync/ocr/`) - Sync integration, annotation processing, clustering
2. **Server** (`docker/ocr/`) - TrOCR inference service with optional LoRA fine-tuning

The system is fully integrated into the sync workflow and runs automatically when OCR is enabled in configuration.

## Architecture Diagram

```
┌──────────────────────────────────────────────────────┐
│  rock-paper-sync (Client)                            │
│                                                      │
│  ┌─────────────────────────────────────────────────┐ │
│  │ Converter (Main sync loop)                       │ │
│  │  └─ OCRProcessor                                 │ │
│  │     ├─ Extract annotations from .rm files        │ │
│  │     ├─ Paragraph mapper (spatial clustering)     │ │
│  │     ├─ Create OCR requests                       │ │
│  │     └─ Insert results into markdown              │ │
│  └─────────────────────────────────────────────────┘ │
│                      │                               │
│                      │ HTTP/Runpods                  │
│                      ▼                               │
│  ┌─────────────────────────────────────────────────┐ │
│  │ Service Factory                                  │ │
│  │  ├─ create_ocr_service(config)                  │ │
│  │  └─ Returns: RunpodsOCRService                   │ │
│  └─────────────────────────────────────────────────┘ │
│                      │                               │
│                      ▼                               │
│  ┌─────────────────────────────────────────────────┐ │
│  │ OCR Markers                                      │ │
│  │  ├─ add_ocr_markers() - Insert annotations       │ │
│  │  ├─ strip_ocr_markers() - Remove from markdown   │ │
│  │  └─ extract_paragraph_index_mapping()            │ │
│  └─────────────────────────────────────────────────┘ │
│                                                      │
└──────────────────────────────────────────────────────┘
                       │
                       │ OCR API
                       ▼
┌──────────────────────────────────────────────────────┐
│  OCR Service (Container)                             │
│                                                      │
│  ┌─────────────────────────────────────────────────┐ │
│  │ FastAPI Application                              │ │
│  │  ├─ POST /run - Batch async OCR inference        │ │
│  │  ├─ POST /runsync - Model info                   │ │
│  │  └─ GET /health - Health check                   │ │
│  └─────────────────────────────────────────────────┘ │
│                      │                               │
│                      ▼                               │
│  ┌─────────────────────────────────────────────────┐ │
│  │ TrOCR Inference Engine                           │ │
│  │  ├─ Base: microsoft/trocr-base-handwritten       │ │
│  │  ├─ Optional: LoRA fine-tuning                   │ │
│  │  └─ Beam search (beam_size=5)                    │ │
│  └─────────────────────────────────────────────────┘ │
│                      │                               │
│                      ▼                               │
│  ┌─────────────────────────────────────────────────┐ │
│  │ Training Pipeline (Optional)                     │ │
│  │  ├─ Collect corrections from users               │ │
│  │  ├─ Fine-tune with LoRA                          │ │
│  │  └─ Push to Runpods                              │ │
│  └─────────────────────────────────────────────────┘ │
│                                                      │
└──────────────────────────────────────────────────────┘
```

## Core Components

### 1. OCRProcessor (`src/rock_paper_sync/ocr/integration.py`)

The main integration point between sync and OCR. Responsible for:
- Extracting annotations from .rm files
- Mapping annotations to source paragraphs (spatial clustering)
- Creating OCR requests
- Inserting recognized text back into markdown
- Managing OCR markers

**Key Methods:**
- `process()` - Main sync integration
- `_extract_annotations_and_images()` - Get annotations from .rm files
- `_map_annotations_to_paragraphs()` - Spatial/textual matching
- `_send_for_recognition()` - Call OCR service
- `_insert_ocr_results()` - Add recognized text to markdown

### 2. Service Protocol (`src/rock_paper_sync/ocr/protocol.py`)

Defines the interface for OCR services:

```python
class OCRServiceProtocol(Protocol):
    """Interface that all OCR service implementations must follow."""

    def recognize_batch(self, requests: list[OCRRequest]) -> list[OCRResult]:
        """Process multiple OCR requests."""

    def get_model_info(self) -> ModelInfo:
        """Get information about current model."""
```

**Data Types:**
- `OCRRequest` - Input: image bytes, annotation UUID, bounding box, paragraph context
- `OCRResult` - Output: recognized text, confidence, model version, processing time
- `BoundingBox` - Spatial coordinates of annotation
- `ParagraphContext` - Source document info and surrounding text
- `ModelInfo` - Model version, base model, fine-tuning status

### 3. Paragraph Mapper (`src/rock_paper_sync/ocr/paragraph_mapper.py`)

Maps annotations to source paragraphs using:
- **Spatial overlap**: Bounding box intersection
- **Text matching**: Fuzzy matching to handle layout changes
- **Fallback**: Uses surrounding context (preceding/following text)

```
Annotation in .rm file
    ↓
Extract image & bounding box
    ↓
Find matching paragraph in source
    ├─ Spatial overlap (primary)
    ├─ Text matching (secondary)
    └─ Context matching (fallback)
    ↓
Create OCRRequest with context
```

### 4. OCR Markers (`src/rock_paper_sync/ocr/markers.py`)

Embeds OCR results in markdown as special comments:

```markdown
# Document

Paragraph with handwritten text.

<!-- RPS:OCR:annotation-uuid-1:confidence-0.95:model-v1 -->
Recognized text from annotation

More content...
```

Markers enable:
- Tracking which annotations were recognized
- Detecting user corrections (modification since recognition)
- Comparing results between sync runs
- Training data collection

### 5. Service Implementations

#### RunpodsOCRService (`src/rock_paper_sync/ocr/runpods.py`)

Cloud-based OCR service using Runpods serverless:
- HTTP API calls to Runpods endpoint
- Automatic retry with exponential backoff
- Job status polling
- Timeout handling

#### Local Service (Planned)

Containerized local inference with Podman:
- GPU passthrough support
- No API key required
- Offline operation

### 6. Correction Management (`src/rock_paper_sync/ocr/corrections.py`)

Detects when users modify OCR results:
- Compares current text to marker text
- Stores corrections in database
- Collects for training dataset

### 7. Training Pipeline (`src/rock_paper_sync/ocr/training.py`)

Prepares fine-tuning data:
- Collects corrections through `ocr-collect-training` CLI
- Creates Parquet dataset with DVC
- Manages dataset versions
- Provides training job lifecycle management

## Data Flow

### During Sync

```
1. Read .rm file
   ├─ Extract annotations (strokes, text boxes)
   ├─ Generate images from annotations
   └─ Get bounding boxes

2. Load source markdown
   ├─ Parse document structure
   └─ Extract paragraphs

3. Map annotations to paragraphs
   ├─ Spatial overlap check
   ├─ Text matching
   └─ Context matching

4. Create OCR requests
   ├─ Encode images as base64
   ├─ Build paragraph context
   └─ Include confidence thresholds

5. Send to OCR service
   ├─ HTTP request to Runpods
   ├─ Batch processing
   └─ Get results with confidence scores

6. Process OCR results
   ├─ Store in database
   ├─ Add OCR markers to markdown
   └─ Update vault

7. Store OCR metadata
   ├─ Save markers (UUID, confidence, model version)
   ├─ Track corrections
   └─ Update state database
```

### Configuration Integration

The OCR system reads from `[ocr]` section in config.toml:

```toml
[ocr]
enabled = true
provider = "runpods"
runpods_endpoint_id = "your-endpoint-id"
runpods_api_key = "your-api-key"
timeout = 300
cache_dir = "/tmp/ocr-cache"
```

When enabled, OCRProcessor is instantiated in SyncEngine and called for each document.

## How OCR is Invoked

### Automatic (Default)

When `ocr.enabled = true` in config, OCR runs during normal sync:

```bash
rock-paper-sync sync --vault my-vault
# Automatically processes annotations with OCR
```

### Manual Commands

```bash
# Collect training corrections
rock-paper-sync ocr-collect-training --vault my-vault --output-dir ./training-data

# Submit training job (if implemented)
rock-paper-sync ocr-train --dataset-version v1 --model-version model-v1
```

## Service Deployment

### Running the OCR Service

#### Local Development with Podman

```bash
# Build container
podman build -t rock-paper-sync-ocr:latest -f docker/ocr/Containerfile docker/ocr/

# Run FastAPI server
podman run -p 8000:8000 rock-paper-sync-ocr:latest serve

# Test health
curl http://localhost:8000/health

# Send test OCR request
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "images": [
        {
          "image_b64": "...",
          "cluster_id": "test_1",
          "paragraph_id": 1
        }
      ]
    }
  }'
```

#### Cloud Deployment with Runpods

1. **Build and push image:**
   ```bash
   podman tag rock-paper-sync-ocr:latest <registry>/rock-paper-sync-ocr:latest
   podman push <registry>/rock-paper-sync-ocr:latest
   ```

2. **Create Runpods endpoint:**
   - Go to Runpods console
   - Create Serverless Endpoint
   - Select GPU tier (RTX A4000+)
   - Set image URL and command: `serverless`

3. **Configure rock-paper-sync:**
   ```toml
   [ocr]
   enabled = true
   provider = "runpods"
   runpods_endpoint_id = "<endpoint-id>"
   runpods_api_key = "<api-key>"
   ```

### Service API

#### POST /run - Batch Async Inference

Process multiple images:

```json
{
  "input": {
    "images": [
      {
        "image_b64": "<base64_encoded_image>",
        "cluster_id": "cluster_1",
        "paragraph_id": 1,
        "context": {
          "expected_text": "Expected text for this paragraph",
          "block_type": "PARAGRAPH"
        }
      }
    ]
  }
}
```

Response:
```json
{
  "id": "job_12345",
  "status": "IN_PROGRESS"
}
```

#### POST /runsync - Model Info

Get current model information:

```json
{
  "input": {}
}
```

Response:
```json
{
  "model_name": "microsoft/trocr-base-handwritten",
  "model_version": "base"
}
```

#### GET /health

Health check response: `{"status": "healthy"}`

## Testing OCR

### Unit Tests

```bash
# OCR-specific tests
uv run pytest tests/test_ocr.py -v
uv run pytest tests/test_ocr_testdata.py -v
uv run pytest tests/test_paragraph_mapper.py -v

# All tests
uv run pytest --cov=src/rock_paper_sync/ocr
```

### Integration Tests (Record/Replay)

The record/replay framework provides OCR testing utilities via `OCRIntegrationMixin`:

```python
from tests.record_replay.harness.ocr_integration import OCRIntegrationMixin

class OCRTest(OCRIntegrationMixin, DeviceTestCase):
    name = "ocr-test"
    requires_ocr = True

    ocr_expected_texts = {
        "annotation-uuid-1": "hello world",
        "annotation-uuid-2": "2025",
    }

    @device_test(requires_ocr=True)
    def execute(self) -> bool:
        ret, _, _ = self.sync("Sync with OCR")
        return ret == 0
```

**Key Features:**
- Automatic mocking of OCR service
- Recording of requests and responses
- Verification that expected texts appear
- Golden file comparison

### Manual Testing

1. **With real device (online mode):**
   ```bash
   uv run pytest tests/record_replay/scenarios/ocr_tests.py --device-mode=online
   ```

2. **With recorded artifacts (offline mode):**
   ```bash
   uv run pytest tests/record_replay/scenarios/ocr_tests.py --device-mode=offline
   ```

3. **Debug workspace:**
   ```bash
   uv run pytest tests/record_replay/... --no-cleanup
   # Inspect /tmp/rock-paper-sync-test/
   ```

## Fine-Tuning and Training

### Collect Training Data

1. **Make corrections in normal workflow:**
   ```bash
   rock-paper-sync sync --vault my-vault
   # Review and correct OCR results manually
   ```

2. **Collect corrections:**
   ```bash
   rock-paper-sync ocr-collect-training \
     --vault my-vault \
     --output-dir ./training-data
   ```

3. **Create dataset with DVC:**
   ```bash
   export OCR_DATASET_VERSION=v1
   dvc repro prepare_dataset
   # Creates: ${XDG_CACHE_HOME}/rock-paper-sync/ocr/datasets/v1.parquet
   ```

### Run Fine-Tuning

Inside container or via Runpods:

```bash
python -m ocr_service train \
  --dataset-version v1 \
  --output-version model-v1 \
  --epochs 3 \
  --use-lora
```

**LoRA Configuration:**
- Rank: 16
- Alpha: 32
- Target modules: `q_proj`, `v_proj`, `k_proj`, `out_proj`
- Dropout: 0.1

Fine-tuned model saved to `/app/checkpoints/model-v1/`

### Deploy Fine-Tuned Model

Push updated model to Runpods endpoint:

```bash
# Build new image with fine-tuned weights
podman build -t <registry>/rock-paper-sync-ocr:model-v1 \
  --build-arg MODEL_VERSION=model-v1 \
  -f docker/ocr/Containerfile docker/ocr/

# Update Runpods endpoint to use new image
```

## Confidence Scores

The OCR service returns confidence scores based on beam search probabilities:

```python
confidence = average(top_beam_token_probabilities)
# Fallback: 0.3 if unavailable (conservative low-confidence indicator)
```

**Important Limitations:**
- Scores are relative indicators, not calibrated probabilities
- Not suitable for filtering without validation
- Treat `confidence <= 0.3` as requiring manual review

**Future Improvements Needed:**
- Calibration against validation set
- Proper uncertainty estimation
- Distinguish between model confidence and input quality

## Coordinate Systems and Stroke Anchoring

OCR uses coordinate transformation to map annotations to source text. This is critical for:
- Converting .rm file coordinates to image coordinates
- Matching annotations to paragraphs
- Preserving spatial relationships

**See:** `docs/STROKE_ANCHORING.md` for detailed coordinate space analysis.

## Known Limitations

1. **Confidence Scores:** Simplified estimates, not calibrated (see above)
2. **Handwriting Support:** Works best with clear, print-like handwriting
3. **Layout Changes:** May fail if document layout changed significantly
4. **Large Documents:** Batch size limited to prevent timeouts
5. **Language Support:** TrOCR base model trained on English text

## Error Handling

### OCR Service Errors

```python
from rock_paper_sync.ocr.protocol import OCRServiceError, OCRDataError

try:
    results = ocr_service.recognize_batch(requests)
except OCRServiceError as e:
    # Service communication failed (network, timeout, etc.)
    logger.error(f"OCR service error: {e}")
except OCRDataError as e:
    # Data format or validation error
    logger.error(f"OCR data error: {e}")
```

### Sync Integration Error Recovery

If OCR fails during sync:
- Exception is caught and logged
- Document processing continues
- Users can retry with `--ocr-skip` flag if needed

## Configuration Reference

```toml
[ocr]
# Enable/disable OCR
enabled = true

# Service provider: "runpods" or "local" (local not yet implemented)
provider = "runpods"

# Runpods configuration
runpods_endpoint_id = "your-endpoint-id"
runpods_api_key = "your-api-key"

# Service timeout in seconds
timeout = 300

# Cache directory for models and datasets
cache_dir = "/home/user/.cache/rock-paper-sync/ocr"

# Minimum confidence threshold for inserting results
min_confidence = 0.3

# Maximum batch size for OCR requests
batch_size = 32

# Paragraph mapper strategy: "spatial" or "text_matching"
paragraph_mapper = "spatial"
```

## Development Guidelines

### Adding a New OCR Provider

1. Create service implementation in `src/rock_paper_sync/ocr/`:
   ```python
   class MyOCRService:
       def recognize_batch(self, requests: list[OCRRequest]) -> list[OCRResult]:
           """Implement recognition logic."""

       def get_model_info(self) -> ModelInfo:
           """Return model information."""
   ```

2. Update factory in `factory.py`:
   ```python
   elif provider == "myprovider":
       from rock_paper_sync.ocr.my_provider import MyOCRService
       return MyOCRService(...)
   ```

3. Add configuration fields to `config.py`

4. Write tests in `tests/test_ocr.py`

### Adding Training Features

1. Extend `CorrectionManager` in `corrections.py`
2. Update training pipeline in `training.py`
3. Add DVC pipeline stages in `dvc.yaml`
4. Update CLI commands in `cli.py`

## Troubleshooting

### OCR Service Not Responding

**Symptom:** Sync hangs or times out on OCR

**Diagnostic:**
```bash
# Check service health
curl http://localhost:8000/health

# Check Runpods endpoint
aws lambda invoke --function-name your-endpoint
```

**Fix:**
- Verify endpoint ID and API key in config
- Check network connectivity to Runpods
- Increase timeout in config
- Check service logs: `podman logs <container-id>`

### Low Confidence Results

**Symptom:** OCR results consistently have confidence < 0.3

**Cause:** Model not well-trained on your handwriting style

**Fix:**
- Collect corrections for fine-tuning
- Create dataset: `rock-paper-sync ocr-collect-training`
- Fine-tune model with your data
- Deploy updated model to Runpods

### Paragraph Mapping Failures

**Symptom:** Annotations mapped to wrong paragraphs or not at all

**Diagnostic:**
```bash
# Enable debug logging
[logging]
level = "debug"

# Check logs for paragraph mapping details
```

**Fix:**
- Try switching paragraph mapper: `paragraph_mapper = "text_matching"`
- Ensure document layout is consistent
- Check bounding boxes are within page bounds

### Memory Issues in Container

**Symptom:** CUDA out of memory during inference

**Fix:**
- Reduce batch size in config
- Use smaller base model
- Increase GPU memory tier on Runpods
- Reduce image resolution in integration.py

## Performance Considerations

### Optimization Strategies

1. **Batch Size:** Larger batches improve throughput but increase memory
2. **Image Resolution:** Reduce if memory is constrained
3. **Model Size:** Fine-tuned models (LoRA) have minimal overhead
4. **Service Location:** Runpods latency 100-500ms per batch

### Benchmarks

- Base model inference: ~75ms per image
- Batch of 10 images: ~400-500ms total
- Network roundtrip: +100-200ms

## File Reference

| File | Purpose |
|------|---------|
| `src/rock_paper_sync/ocr/__init__.py` | Package exports |
| `src/rock_paper_sync/ocr/protocol.py` | Service interface and data types |
| `src/rock_paper_sync/ocr/factory.py` | Service instantiation |
| `src/rock_paper_sync/ocr/integration.py` | Sync flow integration (OCRProcessor) |
| `src/rock_paper_sync/ocr/paragraph_mapper.py` | Annotation-to-paragraph mapping |
| `src/rock_paper_sync/ocr/markers.py` | OCR marker management |
| `src/rock_paper_sync/ocr/corrections.py` | Correction detection |
| `src/rock_paper_sync/ocr/training.py` | Training pipeline |
| `src/rock_paper_sync/ocr/runpods.py` | Runpods implementation |
| `docker/ocr/Containerfile` | Container definition |
| `docker/ocr/src/ocr_service/app.py` | FastAPI application |
| `docker/ocr/src/ocr_service/inference.py` | TrOCR inference |
| `docker/ocr/src/ocr_service/training.py` | Fine-tuning |
| `tests/test_ocr.py` | Unit tests |
| `tests/test_paragraph_mapper.py` | Mapper unit tests |
| `tests/record_replay/harness/ocr_integration.py` | Record/replay integration |
| `tests/record_replay/scenarios/ocr_tests.py` | Integration test examples |
| `docs/STROKE_ANCHORING.md` | Coordinate system details |
| `docs/RECORD_REPLAY_FRAMEWORK.md` | Testing framework |

## Related Documentation

- **STROKE_ANCHORING.md** - Coordinate transformation and annotation mapping
- **RECORD_REPLAY_FRAMEWORK.md** - Device testing with OCR mocking
- **SYNC_PROTOCOL.md** - Cloud sync protocol
- **MULTI_VAULT.md** - Multi-vault configuration
