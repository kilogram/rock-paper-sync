# OCR Service

Containerized TrOCR inference service for handwriting recognition from reMarkable annotations.

## Architecture

- **FastAPI** application for HTTP API
- **Runpods serverless** handler for cloud deployment
- **TrOCR** base model with optional LoRA fine-tuning
- Pre-downloaded base model in Docker layer for fast cold starts

### Service Boundaries

```
┌─────────────────────────────────────┐
│  rock-paper-sync (Client)           │
│  - Clustering & paragraph mapping   │
│  - OCRServiceProtocol interface     │
└──────────────┬──────────────────────┘
               │ HTTP/Runpods API
┌──────────────┴──────────────────────┐
│  OCR Service (Container)            │
│  - TrOCR model inference            │
│  - Batch processing                 │
│  - LoRA fine-tuning                 │
└─────────────────────────────────────┘
```

## Supported Providers

### ✅ Runpods (Serverless)
Fully implemented and tested. Supports GPU inference on Runpods cloud infrastructure.

### 🚧 Local Podman (Coming Soon)
Not yet implemented. Will support local container execution with GPU passthrough.
**Status**: Planned for future release. Contributions welcome!

## Local Development

### Build Container

```bash
podman build -t rock-paper-sync-ocr:latest -f docker/ocr/Containerfile docker/ocr/
```

### Run Service

```bash
# FastAPI server mode (for local testing)
podman run -p 8000:8000 rock-paper-sync-ocr:latest serve

# Test health check
curl http://localhost:8000/health
```

### Test Inference

```bash
# Send OCR request
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "images": [
        {
          "image_b64": "<base64_encoded_image>",
          "cluster_id": "test_1",
          "paragraph_id": 1
        }
      ]
    }
  }'
```

## Runpods Deployment

### Deploy to Runpods

1. **Build and push to registry** (Runpods pulls from Docker Hub or your registry):

```bash
podman tag rock-paper-sync-ocr:latest <your-registry>/rock-paper-sync-ocr:latest
podman push <your-registry>/rock-paper-sync-ocr:latest
```

2. **Create Runpods endpoint**:
   - Go to Runpods console
   - Create new Serverless Endpoint
   - Select GPU tier (recommend: RTX A4000 or better)
   - Set container image: `<your-registry>/rock-paper-sync-ocr:latest`
   - Set container command: `serverless` (default from Containerfile)

3. **Configure rock-paper-sync**:

```toml
[ocr]
provider = "runpods"
runpods_endpoint_id = "<your-endpoint-id>"
runpods_api_key = "<your-api-key>"
```

### Environment Variables for Deployment

```bash
# Required for Runpods
RUNPODS_ENDPOINT_ID=<endpoint-id>
RUNPODS_API_KEY=<api-key>

# Optional
TRANSFORMERS_CACHE=/app/models
HF_HOME=/app/models
```

## API Endpoints

### POST /run (Async Batch Recognition)

Process multiple images asynchronously:

```json
{
  "input": {
    "images": [
      {
        "image_b64": "<base64>",
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

**Response**:
```json
{
  "id": "job_12345",
  "status": "IN_PROGRESS"
}
```

### POST /runsync (Sync Model Info)

Get model information synchronously:

```json
{
  "input": {}
}
```

**Response**:
```json
{
  "model_name": "microsoft/trocr-base-handwritten",
  "model_version": "base"
}
```

### GET /health

Health check for container orchestration.

**Response**: `200 OK` with `{"status": "healthy"}`

## Training Pipeline

### Prepare Training Data

1. **Collect corrections** through normal sync workflow:

```bash
rock-paper-sync ocr-process --vault my-vault
# Make corrections on device
rock-paper-sync ocr-collect-training --output-dir ./training-data
```

2. **Create dataset with DVC**:

```bash
# Set version
export OCR_DATASET_VERSION=v1

# Run DVC pipeline
dvc repro prepare_dataset
```

This creates a Parquet file at:
```
${XDG_CACHE_HOME}/rock-paper-sync/ocr/datasets/v1.parquet
```

### Run Fine-Tuning

```bash
# Inside container or via Runpods
python -m ocr_service train \
  --dataset-version v1 \
  --output-version model-v1 \
  --epochs 3 \
  --use-lora
```

**LoRA Configuration**:
- Rank: 16
- Alpha: 32
- Target modules: `q_proj`, `v_proj`, `k_proj`, `out_proj`
- Dropout: 0.1

Output saved to `/app/checkpoints/model-v1/`

## Known Limitations

### Confidence Scores

The confidence scores returned by the service are **simplified estimates** based on beam search probabilities. They should be interpreted as relative indicators, not calibrated probabilities.

**Current implementation**:
- Average of top beam token probabilities
- Fallback value of `0.3` if scores unavailable (conservative low-confidence indicator)

**Future improvements needed**:
- Calibrate scores against validation set
- Use proper confidence estimation techniques
- Distinguish between model uncertainty types

**Usage guideline**: Treat confidence <= 0.3 as low-confidence results that may need review.

## Dependencies

See `requirements.txt` for Python dependencies.

**Key dependencies**:
- `torch==2.1.0` - PyTorch with CUDA 12.1
- `transformers>=4.35.0,<4.50.0` - Hugging Face transformers
- `peft>=0.10.0` - LoRA/PEFT support
- `fastapi>=0.110.0` - HTTP API framework
- `runpod>=1.0.0` - Runpods serverless integration

## Troubleshooting

### Container fails health check

**Symptom**: Container shows unhealthy in `podman ps`

**Cause**: Service not starting or port not exposed

**Fix**: Check logs with `podman logs <container-id>` and verify port 8000 is exposed

### Out of memory during training

**Symptom**: CUDA OOM error during fine-tuning

**Solutions**:
- Reduce `batch_size` (default: 8)
- Use LoRA instead of full fine-tuning (`--use-lora`)
- Use smaller base model
- Increase GPU memory tier on Runpods

### Model not loading

**Symptom**: "Model not found" errors

**Cause**: Base model not pre-downloaded or corrupted cache

**Fix**: Rebuild container to re-download base model

## License

Same as parent rock-paper-sync project.
