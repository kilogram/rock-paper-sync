"""Training pipeline with DVC integration.

Handles dataset versioning, training orchestration, and model management
using DVC for reproducible ML workflows.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.parquet as pq

if TYPE_CHECKING:
    from rock_paper_sync.config import OCRConfig
    from rock_paper_sync.state import StateManager

logger = logging.getLogger("rock_paper_sync.ocr.training")


@dataclass
class DatasetVersion:
    """Information about a dataset version."""

    version: str
    sample_count: int
    created_at: datetime
    parquet_path: Path
    manifest_path: Path


@dataclass
class ModelVersion:
    """Information about a model version."""

    version: str
    base_model: str
    dataset_version: str
    created_at: datetime
    checkpoint_path: Path
    metrics: dict
    is_active: bool = False


class DatasetManager:
    """Manages correction datasets for fine-tuning."""

    def __init__(self, cache_dir: Path, state_manager: "StateManager") -> None:
        """Initialize dataset manager.

        Args:
            cache_dir: XDG cache directory for OCR data
            state_manager: State manager for database access
        """
        self.cache_dir = cache_dir
        self.state_manager = state_manager

        self.datasets_dir = cache_dir / "datasets"
        self.datasets_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(f"DatasetManager initialized: {self.datasets_dir}")

    def create_dataset_version(self, min_samples: int = 100) -> DatasetVersion | None:
        """Batch pending corrections into a versioned dataset.

        Args:
            min_samples: Minimum corrections required to create dataset

        Returns:
            DatasetVersion if created, None if insufficient samples
        """
        # Get pending corrections
        pending = self.state_manager.get_pending_ocr_corrections()

        if len(pending) < min_samples:
            logger.info(f"Insufficient corrections for dataset: {len(pending)}/{min_samples}")
            return None

        # Generate version string
        version = f"v{self._next_version_number()}"
        created_at = datetime.now()

        # Create dataset files
        parquet_path = self.datasets_dir / f"{version}.parquet"
        manifest_path = self.datasets_dir / f"{version}.manifest.json"

        # Export to parquet
        self._export_to_parquet(pending, parquet_path)

        # Create manifest
        manifest = {
            "version": version,
            "created_at": created_at.isoformat(),
            "sample_count": len(pending),
            "corrections": [c["id"] for c in pending],
        }
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        # Mark corrections as assigned
        self.state_manager.assign_corrections_to_dataset([c["id"] for c in pending], version)

        logger.info(f"Created dataset {version} with {len(pending)} samples")

        return DatasetVersion(
            version=version,
            sample_count=len(pending),
            created_at=created_at,
            parquet_path=parquet_path,
            manifest_path=manifest_path,
        )

    def get_dataset_versions(self) -> list[DatasetVersion]:
        """Get all available dataset versions.

        Returns:
            List of DatasetVersion objects
        """
        versions = []

        for manifest_path in self.datasets_dir.glob("*.manifest.json"):
            with open(manifest_path) as f:
                manifest = json.load(f)

            version = manifest["version"]
            parquet_path = self.datasets_dir / f"{version}.parquet"

            if parquet_path.exists():
                versions.append(
                    DatasetVersion(
                        version=version,
                        sample_count=manifest["sample_count"],
                        created_at=datetime.fromisoformat(manifest["created_at"]),
                        parquet_path=parquet_path,
                        manifest_path=manifest_path,
                    )
                )

        return sorted(versions, key=lambda v: v.created_at, reverse=True)

    def _export_to_parquet(self, corrections: list[dict], output_path: Path) -> None:
        """Export corrections to parquet format.

        Args:
            corrections: List of correction dictionaries
            output_path: Output parquet file path
        """
        # Build table schema
        schema = pa.schema(
            [
                ("id", pa.string()),
                ("image_path", pa.string()),
                ("original_text", pa.string()),
                ("corrected_text", pa.string()),
                ("paragraph_context", pa.string()),
                ("created_at", pa.int64()),
            ]
        )

        # Build arrays
        arrays = [
            pa.array([c["id"] for c in corrections]),
            pa.array([c["image_path"] for c in corrections]),
            pa.array([c["original_text"] for c in corrections]),
            pa.array([c["corrected_text"] for c in corrections]),
            pa.array([c["paragraph_context"] or "" for c in corrections]),
            pa.array([c["created_at"] for c in corrections]),
        ]

        table = pa.Table.from_arrays(arrays, schema=schema)
        pq.write_table(table, output_path)

        logger.debug(f"Exported {len(corrections)} corrections to {output_path}")

    def _next_version_number(self) -> int:
        """Get next version number.

        Returns:
            Next version number
        """
        existing = self.get_dataset_versions()
        if not existing:
            return 1

        # Extract version numbers
        numbers = []
        for v in existing:
            try:
                num = int(v.version.lstrip("v"))
                numbers.append(num)
            except ValueError:
                pass

        return max(numbers, default=0) + 1


class ModelRegistry:
    """Manages trained model versions."""

    def __init__(self, cache_dir: Path) -> None:
        """Initialize model registry.

        Args:
            cache_dir: XDG cache directory for OCR data
        """
        self.cache_dir = cache_dir
        self.models_dir = cache_dir / "models"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.registry_path = self.models_dir / "registry.json"
        self._registry: dict = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """Load registry from disk."""
        if self.registry_path.exists():
            with open(self.registry_path) as f:
                self._registry = json.load(f)
        else:
            self._registry = {"active": None, "versions": {}}

    def _save_registry(self) -> None:
        """Save registry to disk."""
        with open(self.registry_path, "w") as f:
            json.dump(self._registry, f, indent=2)

    def register(self, version: ModelVersion) -> None:
        """Register a new model version.

        Args:
            version: ModelVersion to register
        """
        self._registry["versions"][version.version] = {
            "base_model": version.base_model,
            "dataset_version": version.dataset_version,
            "created_at": version.created_at.isoformat(),
            "checkpoint_path": str(version.checkpoint_path),
            "metrics": version.metrics,
        }
        self._save_registry()
        logger.info(f"Registered model version: {version.version}")

    def activate(self, version: str) -> None:
        """Set a model version as active.

        Args:
            version: Version string to activate
        """
        if version not in self._registry["versions"]:
            raise ValueError(f"Unknown model version: {version}")

        self._registry["active"] = version
        self._save_registry()
        logger.info(f"Activated model version: {version}")

    def get_active(self) -> ModelVersion | None:
        """Get the currently active model version.

        Returns:
            Active ModelVersion or None
        """
        active = self._registry.get("active")
        if not active:
            return None

        return self.get_version(active)

    def get_version(self, version: str) -> ModelVersion | None:
        """Get a specific model version.

        Args:
            version: Version string

        Returns:
            ModelVersion or None
        """
        if version not in self._registry["versions"]:
            return None

        data = self._registry["versions"][version]
        return ModelVersion(
            version=version,
            base_model=data["base_model"],
            dataset_version=data["dataset_version"],
            created_at=datetime.fromisoformat(data["created_at"]),
            checkpoint_path=Path(data["checkpoint_path"]),
            metrics=data["metrics"],
            is_active=version == self._registry.get("active"),
        )

    def get_all_versions(self) -> list[ModelVersion]:
        """Get all registered model versions.

        Returns:
            List of ModelVersion objects
        """
        versions = []
        for version in self._registry["versions"]:
            mv = self.get_version(version)
            if mv:
                versions.append(mv)
        return sorted(versions, key=lambda v: v.created_at, reverse=True)


class TrainingPipeline:
    """Orchestrates training with DVC integration."""

    def __init__(
        self,
        config: "OCRConfig",
        state_manager: "StateManager",
    ) -> None:
        """Initialize training pipeline.

        Args:
            config: OCR configuration
            state_manager: State manager for database access
        """
        self.config = config
        self.state_manager = state_manager
        self.cache_dir = config.cache_dir or Path.home() / ".cache" / "rock-paper-sync"

        self.dataset_manager = DatasetManager(self.cache_dir, state_manager)
        self.model_registry = ModelRegistry(self.cache_dir)

    def prepare_dataset(self) -> DatasetVersion | None:
        """Prepare a new dataset from pending corrections.

        Returns:
            DatasetVersion if created, None if insufficient samples
        """
        return self.dataset_manager.create_dataset_version(
            min_samples=self.config.min_corrections_for_dataset
        )

    def train(
        self,
        dataset_version: str,
        output_version: str | None = None,
    ) -> ModelVersion:
        """Train a model on a dataset.

        Args:
            dataset_version: Dataset version to train on
            output_version: Output model version (auto-generated if None)

        Returns:
            Trained ModelVersion
        """
        # Verify dataset exists
        datasets = self.dataset_manager.get_dataset_versions()
        dataset = next((d for d in datasets if d.version == dataset_version), None)
        if not dataset:
            raise ValueError(f"Dataset not found: {dataset_version}")

        # Generate output version if not specified
        if not output_version:
            output_version = f"ft-{dataset_version}"

        # Checkpoint path
        checkpoint_path = self.cache_dir / "models" / output_version

        # Build training command
        runtime = self.config.container_runtime
        image = self.config.local_image

        # Mount paths
        data_mount = f"{self.cache_dir}:/app/data"
        checkpoints_mount = f"{checkpoint_path.parent}:/app/checkpoints"

        cmd = [
            runtime,
            "run",
            "--rm",
            "-v",
            data_mount,
            "-v",
            checkpoints_mount,
        ]

        # Add GPU support if available
        if self.config.local_gpu_device != "cpu":
            if runtime == "podman":
                cmd.extend(["--device", f"nvidia.com/gpu={self.config.local_gpu_device}"])
            else:
                cmd.extend(["--gpus", f"device={self.config.local_gpu_device}"])

        cmd.extend(
            [
                image,
                "train",
                "--dataset",
                dataset_version,
                "--output",
                output_version,
            ]
        )

        logger.info(f"Running training: {' '.join(cmd)}")

        # Run training
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"Training failed: {result.stderr}")
            raise RuntimeError(f"Training failed: {result.stderr}")

        # Load metrics from output
        metadata_path = checkpoint_path / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path) as f:
                metadata = json.load(f)
            metrics = metadata.get("metrics", {})
        else:
            metrics = {}

        # Create model version
        model_version = ModelVersion(
            version=output_version,
            base_model=self.config.base_model,
            dataset_version=dataset_version,
            created_at=datetime.now(),
            checkpoint_path=checkpoint_path,
            metrics=metrics,
        )

        # Register model
        self.model_registry.register(model_version)

        logger.info(f"Training complete: {output_version}, metrics: {metrics}")

        return model_version

    def run_dvc_pipeline(self, dataset_version: str) -> ModelVersion:
        """Run training through DVC pipeline.

        This creates reproducible training runs tracked by DVC.

        Args:
            dataset_version: Dataset version to train on

        Returns:
            Trained ModelVersion
        """
        # Check if DVC is available
        try:
            subprocess.run(["dvc", "version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("DVC not available, falling back to direct training")
            return self.train(dataset_version)

        output_version = f"ft-{dataset_version}"

        # Run DVC pipeline
        cmd = [
            "dvc",
            "repro",
            "-s",  # Single stage
            "--set-param",
            f"version={dataset_version}",
            "--set-param",
            f"output_version={output_version}",
        ]

        logger.info(f"Running DVC pipeline: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"DVC pipeline failed: {result.stderr}")
            raise RuntimeError(f"DVC pipeline failed: {result.stderr}")

        # Get model version from registry
        model_version = self.model_registry.get_version(output_version)
        if not model_version:
            raise RuntimeError(f"Model not found after training: {output_version}")

        return model_version

    def get_stats(self) -> dict:
        """Get training pipeline statistics.

        Returns:
            Dictionary with stats
        """
        correction_stats = self.state_manager.get_ocr_correction_stats()
        datasets = self.dataset_manager.get_dataset_versions()
        models = self.model_registry.get_all_versions()
        active = self.model_registry.get_active()

        return {
            "corrections": correction_stats,
            "datasets": len(datasets),
            "models": len(models),
            "active_model": active.version if active else None,
        }
