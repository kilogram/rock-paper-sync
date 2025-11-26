# Minimal OCR Service for Testing

A lightweight OCR service for testing that returns deterministic dummy results without requiring GPU, model downloads, or heavy dependencies.

## Overview

This container provides a FastAPI-based OCR service that implements the same interface as the full TrOCR service, but returns consistent dummy text results. It's designed specifically for testing and CI/CD pipelines where:

- OCR accuracy is not required
- Fast startup and minimal resource usage is important
- Tests need deterministic, repeatable results
- GPU availability cannot be guaranteed

## Features

- ✅ Minimal dependencies (FastAPI, Uvicorn, Pillow only)
- ✅ Fast startup (~1-2 seconds)
- ✅ Small image size (~300MB)
- ✅ Deterministic results (same image → same text)
- ✅ Full API compatibility with TrOCR service
- ✅ Health check endpoint
- ✅ Support for batch recognition and fine-tuning API stubs

## Building

From the repository root:

```bash
# Build with podman
podman build -t rock-paper-sync-ocr-minimal:latest -f docker/ocr-minimal/Containerfile docker/ocr-minimal/

# Or with docker
docker build -t rock-paper-sync-ocr-minimal:latest -f docker/ocr-minimal/Containerfile docker/ocr-minimal/
```

## Running

### Standalone

```bash
# Start the container
podman run -d -p 8000:8000 --name ocr-minimal rock-paper-sync-ocr-minimal:latest

# Check health
curl http://localhost:8000/health

# Stop when done
podman stop ocr-minimal && podman rm ocr-minimal
```

### With Docker Compose (for tests)

```bash
cd tests/record_replay
docker-compose up ocr-minimal

# In another terminal, run tests
cd ../..
uv run pytest tests/record_replay -m ocr -v
```

## API Interface

The service implements the same endpoints as the full TrOCR service:

### Health Check
```
GET /health
```

Returns: `{"status": "healthy", "version": "0.1.0"}`

### Batch Recognition
```
POST /run
```

Request:
```json
{
  "input": {
    "action": "recognize_batch",
    "images": [
      {
        "uuid": "annotation-uuid",
        "image_b64": "base64-encoded-image",
        "context": {
          "document_id": "doc-1",
          "page_number": 1,
          "paragraph_index": 0,
          "paragraph_text": "surrounding text"
        }
      }
    ]
  }
}
```

Response:
```json
{
  "id": "batch-1234567890",
  "status": "COMPLETED",
  "output": {
    "results": [
      {
        "uuid": "annotation-uuid",
        "text": "recognized text",
        "confidence": 0.95,
        "model_version": "minimal-0.1.0",
        "inference_time_ms": 10
      }
    ]
  }
}
```

### Model Info
```
POST /runsync
```

Request:
```json
{
  "input": {
    "action": "model_info"
  }
}
```

### Fine-Tuning (Stub)
```
POST /run
```

Request:
```json
{
  "input": {
    "action": "fine_tune",
    "dataset_version": "v1"
  }
}
```

Returns job ID for status polling.

## Using with Tests

### Configuration

To use the local OCR service in your application, set the configuration:

```python
from rock_paper_sync.config import OCRConfig

ocr_config = OCRConfig(
    enabled=True,
    provider="local",
    # container_url="http://localhost:8000"  # default
)
```

Or in your config file:
```toml
[ocr]
enabled = true
provider = "local"
```

### Test Setup

Tests can use the local service in two ways:

1. **Start container manually** before running tests:
   ```bash
   podman run -d -p 8000:8000 rock-paper-sync-ocr-minimal:latest
   ```

2. **Use docker-compose** (recommended):
   ```bash
   cd tests/record_replay
   docker-compose up -d ocr-minimal
   ```

Then create a service instance:

```python
from rock_paper_sync.ocr.factory import create_ocr_service
from rock_paper_sync.config import OCRConfig

config = OCRConfig(enabled=True, provider="local")
service = create_ocr_service(config)

# Use the service
results = service.recognize_batch(requests)
```

## Deterministic Results

The OCR service generates consistent dummy text based on:
- Image dimensions
- Annotation UUID
- Hash of image data

This ensures that:
- The same test image always produces the same OCR result
- Test results are reproducible across runs
- No external model downloads or randomness involved

## Performance

Typical response times:
- Health check: <10ms
- Single image recognition: 5-15ms
- Batch of 10 images: 50-100ms

Memory usage: ~100MB
Image size: ~300MB (compressed)

## Limitations

⚠️ **This service is NOT for production use.** It:
- Returns dummy text, not actual OCR results
- Does not perform real fine-tuning
- Has no model persistence or training
- Returns high confidence (0.95) for all results regardless of actual image content

## Architecture

See [main OCR service README](../ocr/README.md) for information about the full TrOCR service architecture and capabilities.
