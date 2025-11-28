"""FastAPI application for TrOCR service."""

import base64
import io
import logging
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel

from ocr_service.inference import TrOCRInference

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="TrOCR Service", version="1.0.0")

# Initialize inference engine
inference_engine = TrOCRInference()


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


class FineTuneRequest(BaseModel):
    action: str = "fine_tune"
    dataset_version: str


class FineTuneResponse(BaseModel):
    job_id: str
    status: str


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "model_loaded": inference_engine.is_loaded}


@app.post("/runsync")
async def run_sync(request: dict):
    """Synchronous endpoint for simple requests."""
    action = request.get("input", {}).get("action")

    if action == "model_info":
        info = inference_engine.get_model_info()
        return {
            "output": {
                "version": info["version"],
                "base_model": info["base_model"],
                "is_fine_tuned": info["is_fine_tuned"],
                "dataset_version": info["dataset_version"],
                "created_at": info["created_at"],
                "metrics": info["metrics"],
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
                image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

                # Run inference
                start_time = time.time()
                text, confidence = inference_engine.recognize(image)
                inference_time = int((time.time() - start_time) * 1000)

                results.append(
                    {
                        "uuid": img_data["uuid"],
                        "text": text,
                        "confidence": confidence,
                        "model_version": inference_engine.model_version,
                        "inference_time_ms": inference_time,
                    }
                )

            except Exception as e:
                logger.error(f"Error processing image {img_data['uuid']}: {e}")
                results.append(
                    {
                        "uuid": img_data["uuid"],
                        "text": "",
                        "confidence": 0.0,
                        "model_version": inference_engine.model_version,
                        "error": str(e),
                    }
                )

        return {
            "id": f"batch-{int(time.time())}",
            "status": "COMPLETED",
            "output": {"results": results},
        }

    elif action == "fine_tune":
        dataset_version = input_data.get("dataset_version")
        if not dataset_version:
            raise HTTPException(status_code=400, detail="dataset_version required")

        # For now, return a job ID - actual training would be async
        job_id = f"train-{dataset_version}-{int(time.time())}"
        return {
            "id": job_id,
            "status": "IN_QUEUE",
        }

    raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@app.get("/status/{job_id}")
async def get_job_status(job_id: str):
    """Get status of an async job."""
    # In a real implementation, this would check actual job status
    # For now, return completed for demo purposes
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
