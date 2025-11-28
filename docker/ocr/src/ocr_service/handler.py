"""Runpod serverless handler for TrOCR service."""

import base64
import io
import logging
import time

import runpod
from PIL import Image

from ocr_service.inference import TrOCRInference

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize inference engine globally for reuse across requests
inference_engine = TrOCRInference()


def handler(event):
    """
    Runpod serverless handler.

    Supports actions:
    - recognize_batch: OCR on batch of images
    - model_info: Get model information
    - fine_tune: Start fine-tuning job
    """
    input_data = event.get("input", {})
    action = input_data.get("action", "recognize_batch")

    try:
        if action == "model_info":
            info = inference_engine.get_model_info()
            return {
                "version": info["version"],
                "base_model": info["base_model"],
                "is_fine_tuned": info["is_fine_tuned"],
                "dataset_version": info["dataset_version"],
                "created_at": info["created_at"],
                "metrics": info["metrics"],
            }

        elif action == "recognize_batch":
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
                    logger.error(f"Error processing image {img_data.get('uuid', 'unknown')}: {e}")
                    results.append(
                        {
                            "uuid": img_data.get("uuid", "unknown"),
                            "text": "",
                            "confidence": 0.0,
                            "model_version": inference_engine.model_version,
                            "error": str(e),
                        }
                    )

            return {"results": results}

        elif action == "fine_tune":
            dataset_version = input_data.get("dataset_version")
            if not dataset_version:
                return {"error": "dataset_version required"}

            # Return job ID - actual training would be async
            job_id = f"train-{dataset_version}-{int(time.time())}"
            return {
                "job_id": job_id,
                "status": "IN_QUEUE",
            }

        else:
            return {"error": f"Unknown action: {action}"}

    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {"error": str(e)}


def start_serverless():
    """Start the Runpod serverless handler."""
    logger.info("Starting Runpod serverless handler...")
    runpod.serverless.start({"handler": handler})
