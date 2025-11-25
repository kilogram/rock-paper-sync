"""TrOCR inference engine."""

import logging
import os
from datetime import datetime
from pathlib import Path

import torch
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

logger = logging.getLogger(__name__)


class TrOCRInference:
    """TrOCR model for handwritten text recognition."""

    def __init__(
        self,
        model_path: str | None = None,
        base_model: str = "microsoft/trocr-base-handwritten",
    ):
        """Initialize TrOCR inference engine.

        Args:
            model_path: Path to fine-tuned model (or None for base model)
            base_model: Base model identifier
        """
        self.base_model = base_model
        self.model_path = model_path
        self.model_version = "base"
        self.is_fine_tuned = False
        self.dataset_version = None
        self.created_at = None
        self.metrics = {}

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Using device: {self.device}")

        self._load_model()

    def _load_model(self):
        """Load model and processor."""
        try:
            if self.model_path and Path(self.model_path).exists():
                # Load fine-tuned model
                logger.info(f"Loading fine-tuned model from {self.model_path}")
                self.processor = TrOCRProcessor.from_pretrained(self.base_model)
                self.model = VisionEncoderDecoderModel.from_pretrained(self.model_path)

                # Load metadata
                metadata_path = Path(self.model_path) / "metadata.json"
                if metadata_path.exists():
                    import json
                    with open(metadata_path) as f:
                        metadata = json.load(f)
                    self.model_version = metadata.get("version", "fine-tuned")
                    self.is_fine_tuned = True
                    self.dataset_version = metadata.get("dataset_version")
                    self.created_at = metadata.get("created_at")
                    self.metrics = metadata.get("metrics", {})
                else:
                    self.model_version = "fine-tuned"
                    self.is_fine_tuned = True
            else:
                # Load base model
                logger.info(f"Loading base model: {self.base_model}")
                self.processor = TrOCRProcessor.from_pretrained(self.base_model)
                self.model = VisionEncoderDecoderModel.from_pretrained(self.base_model)
                self.model_version = "base"
                self.is_fine_tuned = False

            self.model.to(self.device)
            self.model.eval()
            self.is_loaded = True
            logger.info(f"Model loaded successfully: {self.model_version}")

        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            self.is_loaded = False
            raise

    def recognize(self, image: Image.Image) -> tuple[str, float]:
        """Recognize text in an image.

        Args:
            image: PIL Image to recognize

        Returns:
            Tuple of (recognized_text, confidence_score)
        """
        if not self.is_loaded:
            raise RuntimeError("Model not loaded")

        # Preprocess image
        pixel_values = self.processor(
            images=image,
            return_tensors="pt"
        ).pixel_values.to(self.device)

        # Generate text
        with torch.no_grad():
            outputs = self.model.generate(
                pixel_values,
                max_length=128,
                num_beams=4,
                return_dict_in_generate=True,
                output_scores=True,
            )

        # Decode text
        generated_ids = outputs.sequences
        generated_text = self.processor.batch_decode(
            generated_ids,
            skip_special_tokens=True
        )[0]

        # Calculate confidence (SIMPLIFIED ESTIMATE - see limitations below)
        #
        # LIMITATIONS:
        # - This is NOT a calibrated probability
        # - Simple average of top beam token probabilities
        # - Fallback value (0.5) is arbitrary when scores unavailable
        # - Should be treated as relative indicator, not absolute confidence
        #
        # USAGE: Confidence <= 0.3 suggests low-confidence results needing review
        #
        # TODO: Calibrate against validation set for proper confidence estimation
        if outputs.scores:
            scores = torch.stack(outputs.scores, dim=1)
            probs = torch.softmax(scores, dim=-1)
            top_probs = probs.max(dim=-1).values
            confidence = top_probs.mean().item()
        else:
            # No scores available - return low confidence rather than fake middle value
            confidence = 0.3  # Low confidence indicator (was 0.5, changed to avoid misleading users)

        return generated_text, confidence

    def get_model_info(self) -> dict:
        """Get model information.

        Returns:
            Dictionary with model metadata
        """
        return {
            "version": self.model_version,
            "base_model": self.base_model,
            "is_fine_tuned": self.is_fine_tuned,
            "dataset_version": self.dataset_version,
            "created_at": self.created_at,
            "metrics": self.metrics,
            "device": self.device,
        }
