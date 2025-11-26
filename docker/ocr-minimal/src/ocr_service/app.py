"""Minimal FastAPI application for OCR service testing.

This service provides the same interface as the full TrOCR service but
returns dummy/deterministic results for testing purposes. No GPU or
model downloads required.
"""

import base64
import io
import logging
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Minimal OCR Service", version="0.1.0")


class ImageInput(BaseModel):
    uuid: str
    image_b64: str
    context: dict


class RecognizeBatchRequest(BaseModel):
    action: str = "recognize_batch"
    images: list[ImageInput]


class RecognizeResult(BaseModel):
    uuid: str
    text: str
    confidence: float
    model_version: str


class RecognizeBatchResponse(BaseModel):
    results: list[RecognizeResult]


class ModelInfoResponse(BaseModel):
    version: str
    base_model: str
    is_fine_tuned: bool
    dataset_version: str | None
    created_at: str | None
    metrics: dict


def _generate_dummy_text(uuid: str, image_bytes: bytes) -> str:
    """Generate deterministic dummy text based on image data.

    The same image will always produce the same text, making tests repeatable.
    """
    # Use image size and uuid to generate consistent dummy text
    try:
        image = Image.open(io.BytesIO(image_bytes))
        width, height = image.size
        # Use hash of image data and size to create deterministic text
        text_variants = [
            "handwritten note",
            "text recognition",
            "test annotation",
            "recognized text",
            "ocr result",
        ]
        idx = (hash((width, height, uuid)) % len(text_variants))
        return text_variants[idx]
    except Exception:
        # Fallback if image parsing fails
        return "dummy ocr text"


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "0.1.0"}


@app.post("/runsync")
async def run_sync(request: dict):
    """Synchronous endpoint for simple requests."""
    action = request.get("input", {}).get("action")

    if action == "model_info":
        return {
            "output": {
                "version": "minimal-0.1.0",
                "base_model": "minimal-test",
                "is_fine_tuned": False,
                "dataset_version": None,
                "created_at": datetime.now().isoformat(),
                "metrics": {"cer": 0.5, "wer": 0.5},
            }
        }

    raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@app.post("/run")
async def run_async(request: dict):
    """Async endpoint for batch processing and training."""
    input_data = request.get("input", {})
    action = input_data.get("action")

    if action == "recognize_batch":
        images = input_data.get("images", [])
        results = []

        for img_data in images:
            try:
                # Decode image
                image_bytes = base64.b64decode(img_data["image_b64"])

                # Generate deterministic dummy text
                start_time = time.time()
                text = _generate_dummy_text(img_data["uuid"], image_bytes)
                inference_time = int((time.time() - start_time) * 1000)

                results.append({
                    "uuid": img_data["uuid"],
                    "text": text,
                    "confidence": 0.95,  # High confidence for dummy results
                    "model_version": "minimal-0.1.0",
                    "inference_time_ms": inference_time,
                })

            except Exception as e:
                logger.error(f"Error processing image {img_data['uuid']}: {e}")
                results.append({
                    "uuid": img_data["uuid"],
                    "text": "dummy text",
                    "confidence": 0.95,
                    "model_version": "minimal-0.1.0",
                    "error": str(e),
                })

        return {
            "id": f"batch-{int(time.time())}",
            "status": "COMPLETED",
            "output": {"results": results},
        }

    elif action == "fine_tune":
        # Dummy fine-tuning - just return a job ID
        dataset_version = input_data.get("dataset_version")
        if not dataset_version:
            raise HTTPException(status_code=400, detail="dataset_version required")

        job_id = f"train-{dataset_version}-{int(time.time())}"
        return {
            "id": job_id,
            "status": "IN_QUEUE",
        }

    raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@app.get("/status/{job_id}")
async def get_job_status(job_id: str):
    """Get status of an async job."""
    return {
        "id": job_id,
        "status": "COMPLETED",
        "output": {
            "dataset_version": "v1",
            "model_version": "ft-v1",
            "started_at": datetime.now().isoformat(),
            "completed_at": datetime.now().isoformat(),
            "metrics": {"cer": 0.05, "wer": 0.1},
        },
    }
