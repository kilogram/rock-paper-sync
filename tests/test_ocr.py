"""Comprehensive tests for OCR module.

Tests cover:
- Marker parsing and generation
- Correction detection
- State management for OCR
- Training pipeline
- Integration with sync flow
"""

import hashlib
import os
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rock_paper_sync.config import OCRConfig
from rock_paper_sync.state import StateManager


class TestOCRMarkersParsing:
    """Tests for OCR marker parsing and generation."""

    def test_parse_empty_markdown(self):
        """Test parsing markdown with no OCR markers."""
        from rock_paper_sync.ocr.markers import parse_ocr_blocks

        content = "# Title\n\nSome plain text.\n\nMore text."
        blocks = parse_ocr_blocks(content)
        assert blocks == []

    def test_parse_single_ocr_block(self):
        """Test parsing markdown with single OCR block."""
        from rock_paper_sync.ocr.markers import parse_ocr_blocks

        content = """<!-- RPS:ANNOTATED highlights=2 strokes=1 -->
Original text here.
<!-- RPS:OCR -->
recognized handwriting
<!-- RPS:END -->"""

        blocks = parse_ocr_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].highlights == 2
        assert blocks[0].strokes == 1
        assert blocks[0].original_text == "Original text here."
        assert blocks[0].ocr_text == "recognized handwriting"

    def test_parse_multiple_ocr_blocks(self, markdown_with_ocr_markers):
        """Test parsing markdown with multiple OCR blocks."""
        from rock_paper_sync.ocr.markers import parse_ocr_blocks

        blocks = parse_ocr_blocks(markdown_with_ocr_markers)
        assert len(blocks) == 2

        assert blocks[0].highlights == 2
        assert blocks[0].strokes == 1
        assert "annotated paragraph" in blocks[0].original_text

        assert blocks[1].highlights == 0
        assert blocks[1].strokes == 3

    def test_parse_multiline_ocr_text(self):
        """Test parsing OCR block with multiple lines."""
        from rock_paper_sync.ocr.markers import parse_ocr_blocks

        content = """<!-- RPS:ANNOTATED highlights=1 strokes=2 -->
Source paragraph.
<!-- RPS:OCR -->
line one
line two
line three
<!-- RPS:END -->"""

        blocks = parse_ocr_blocks(content)
        assert len(blocks) == 1
        assert "line one\nline two\nline three" == blocks[0].ocr_text

    def test_generate_ocr_block(self):
        """Test generating OCR marker block."""
        from rock_paper_sync.ocr.markers import generate_ocr_block, AnnotationInfo

        annotation = AnnotationInfo(paragraph_index=0, highlights=3, strokes=2)
        original = "Test paragraph content."
        ocr_lines = ["handwriting line 1", "handwriting line 2"]

        result = generate_ocr_block(annotation, original, ocr_lines)

        assert "<!-- RPS:ANNOTATED highlights=3 strokes=2 -->" in result
        assert "Test paragraph content." in result
        assert "<!-- RPS:OCR -->" in result
        assert "handwriting line 1\nhandwriting line 2" in result
        assert "<!-- RPS:END -->" in result

    def test_strip_ocr_markers(self, markdown_with_ocr_markers):
        """Test stripping OCR markers from content."""
        from rock_paper_sync.ocr.markers import strip_ocr_markers

        result = strip_ocr_markers(markdown_with_ocr_markers)

        # Should not contain markers
        assert "RPS:ANNOTATED" not in result
        assert "RPS:OCR" not in result
        assert "RPS:END" not in result

        # Should contain original text
        assert "This is an annotated paragraph" in result
        assert "Second annotated paragraph" in result

        # Should not contain OCR text
        assert "handwritten note here" not in result
        assert "more handwriting" not in result

    def test_extract_paragraph_index_mapping(self, markdown_with_ocr_markers):
        """Test extracting paragraph index mapping."""
        from rock_paper_sync.ocr.markers import extract_paragraph_index_mapping

        mapping = extract_paragraph_index_mapping(markdown_with_ocr_markers)

        # Should have mappings for annotated paragraphs
        assert len(mapping) >= 1

    def test_hash_consistency(self):
        """Test that text hashing is consistent."""
        from rock_paper_sync.ocr.markers import _hash_text

        text = "test text"
        hash1 = _hash_text(text)
        hash2 = _hash_text(text)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex length

    def test_add_ocr_markers_to_content(self):
        """Test adding OCR markers to markdown content."""
        from rock_paper_sync.ocr.markers import add_ocr_markers, AnnotationInfo

        content = """# Title

First paragraph.

Second paragraph.

Third paragraph.
"""
        # Add OCR result for paragraph index 1 (Second paragraph)
        ocr_results = {
            1: (
                AnnotationInfo(paragraph_index=1, highlights=1, strokes=1),
                ["ocr text here"]
            )
        }

        result = add_ocr_markers(content, ocr_results)

        # Original content should be present
        assert "First paragraph" in result
        assert "Third paragraph" in result

        # OCR markers should be added
        assert "RPS:ANNOTATED" in result
        assert "ocr text here" in result


class TestCorrectionDetection:
    """Tests for correction detection from markdown."""

    def test_detect_no_corrections(self, state_manager, ocr_config):
        """Test that no corrections are detected when text unchanged."""
        from rock_paper_sync.ocr.corrections import CorrectionManager

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)

        # Store original OCR result
        state_manager.update_ocr_result(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_uuid="uuid-1",
            paragraph_index=0,
            ocr_text="original text",
            ocr_text_hash=hashlib.sha256(b"original text").hexdigest(),
            original_text_hash=hashlib.sha256(b"paragraph text").hexdigest(),
            image_hash="img-hash-1",
            confidence=0.9,
            model_version="v1",
        )

        # Markdown with unchanged OCR text
        markdown = """<!-- RPS:ANNOTATED highlights=1 strokes=1 -->
paragraph text
<!-- RPS:OCR -->
original text
<!-- RPS:END -->"""

        corrections = manager.detect_corrections("test-vault", "test.md", markdown)
        assert len(corrections) == 0

    def test_detect_correction(self, state_manager, ocr_config):
        """Test detection of user correction."""
        from rock_paper_sync.ocr.corrections import CorrectionManager

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)

        # Store original OCR result
        state_manager.update_ocr_result(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_uuid="uuid-1",
            paragraph_index=0,
            ocr_text="orignal text",  # intentional typo
            ocr_text_hash=hashlib.sha256(b"orignal text").hexdigest(),
            original_text_hash=hashlib.sha256(b"paragraph text").hexdigest(),
            image_hash="img-hash-1",
            confidence=0.9,
            model_version="v1",
        )

        # Markdown with corrected OCR text
        markdown = """<!-- RPS:ANNOTATED highlights=1 strokes=1 -->
paragraph text
<!-- RPS:OCR -->
original text
<!-- RPS:END -->"""

        corrections = manager.detect_corrections("test-vault", "test.md", markdown)
        assert len(corrections) == 1
        assert corrections[0].original_text == "orignal text"
        assert corrections[0].corrected_text == "original text"

    def test_detect_conflict(self, state_manager, ocr_config):
        """Test detection of conflict (edited original text)."""
        from rock_paper_sync.ocr.corrections import CorrectionManager

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)

        # Store original state
        state_manager.update_ocr_result(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_uuid="uuid-1",
            paragraph_index=0,
            ocr_text="ocr text",
            ocr_text_hash=hashlib.sha256(b"ocr text").hexdigest(),
            original_text_hash=hashlib.sha256(b"original paragraph").hexdigest(),
            image_hash="img-hash-1",
            confidence=0.9,
            model_version="v1",
        )

        # Markdown with edited original text (conflict)
        markdown = """<!-- RPS:ANNOTATED highlights=1 strokes=1 -->
modified paragraph
<!-- RPS:OCR -->
ocr text
<!-- RPS:END -->"""

        conflicts = manager.check_conflicts("test-vault", "test.md", markdown)
        assert len(conflicts) == 1
        assert conflicts[0] == 0

    def test_store_correction(self, state_manager, ocr_config):
        """Test storing a correction record."""
        from rock_paper_sync.ocr.corrections import CorrectionManager, Correction

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)

        correction = Correction(
            id="corr-1",
            image_hash="img-hash-1",
            image_path=ocr_config.cache_dir / "corrections" / "images" / "img-hash-1.png",
            original_text="orignal",
            corrected_text="original",
            paragraph_context="context text",
            document_id="doc-1",
            vault_name="test-vault",
            obsidian_path="test.md",
            paragraph_index=0,
            created_at=int(time.time()),
        )

        manager.store_correction(correction)

        # Verify stored in database
        pending = state_manager.get_pending_ocr_corrections()
        assert len(pending) == 1
        assert pending[0]["original_text"] == "orignal"
        assert pending[0]["corrected_text"] == "original"

    def test_store_annotation_image(self, state_manager, ocr_config):
        """Test storing annotation image."""
        from rock_paper_sync.ocr.corrections import CorrectionManager

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)

        image_data = b"fake png data"
        image_hash = manager.store_annotation_image(image_data, "uuid-1")

        # Verify image file created
        image_path = manager.images_dir / f"{image_hash}.png"
        assert image_path.exists()
        assert image_path.read_bytes() == image_data


class TestOCRStateManagement:
    """Tests for OCR-related state database operations."""

    def test_update_and_get_ocr_result(self, state_manager):
        """Test storing and retrieving OCR results."""
        state_manager.update_ocr_result(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_uuid="uuid-1",
            paragraph_index=0,
            ocr_text="recognized text",
            ocr_text_hash="hash-1",
            original_text_hash="hash-2",
            image_hash="img-hash-1",
            confidence=0.95,
            model_version="v1",
        )

        result = state_manager.get_ocr_result("test-vault", "test.md", "uuid-1")
        assert result is not None
        assert result["ocr_text"] == "recognized text"
        assert result["confidence"] == 0.95
        assert result["model_version"] == "v1"

    def test_get_all_ocr_results(self, state_manager):
        """Test getting all OCR results for a document."""
        # Add multiple results
        for i in range(3):
            state_manager.update_ocr_result(
                vault_name="test-vault",
                obsidian_path="test.md",
                annotation_uuid=f"uuid-{i}",
                paragraph_index=i,
                ocr_text=f"text {i}",
                ocr_text_hash=f"hash-{i}",
                original_text_hash=f"orig-hash-{i}",
                image_hash=f"img-hash-{i}",
                confidence=0.9,
                model_version="v1",
            )

        results = state_manager.get_all_ocr_results("test-vault", "test.md")
        assert len(results) == 3
        assert 0 in results
        assert 1 in results
        assert 2 in results

    def test_delete_ocr_results(self, state_manager):
        """Test deleting OCR results for a document."""
        state_manager.update_ocr_result(
            vault_name="test-vault",
            obsidian_path="test.md",
            annotation_uuid="uuid-1",
            paragraph_index=0,
            ocr_text="text",
            ocr_text_hash="hash",
            original_text_hash="orig-hash",
            image_hash="img-hash",
            confidence=0.9,
            model_version="v1",
        )

        state_manager.delete_ocr_results("test-vault", "test.md")

        result = state_manager.get_ocr_result("test-vault", "test.md", "uuid-1")
        assert result is None

    def test_add_ocr_correction(self, state_manager):
        """Test adding OCR correction."""
        state_manager.add_ocr_correction(
            correction_id="corr-1",
            image_hash="img-hash-1",
            image_path="/path/to/image.png",
            original_text="orignal",
            corrected_text="original",
            paragraph_context="context",
            document_id="doc-1",
        )

        pending = state_manager.get_pending_ocr_corrections()
        assert len(pending) == 1
        assert pending[0]["id"] == "corr-1"

    def test_assign_corrections_to_dataset(self, state_manager):
        """Test assigning corrections to a dataset version."""
        # Add corrections
        for i in range(3):
            state_manager.add_ocr_correction(
                correction_id=f"corr-{i}",
                image_hash=f"img-{i}",
                image_path=f"/path/{i}.png",
                original_text=f"orig-{i}",
                corrected_text=f"corr-{i}",
                paragraph_context="context",
                document_id="doc-1",
            )

        # Assign to dataset
        state_manager.assign_corrections_to_dataset(
            ["corr-0", "corr-1"],
            "v1"
        )

        # Check pending (should only have corr-2)
        pending = state_manager.get_pending_ocr_corrections()
        assert len(pending) == 1
        assert pending[0]["id"] == "corr-2"

    def test_get_ocr_correction_stats(self, state_manager):
        """Test getting OCR correction statistics."""
        # Add some corrections
        for i in range(5):
            state_manager.add_ocr_correction(
                correction_id=f"corr-{i}",
                image_hash=f"img-{i}",
                image_path=f"/path/{i}.png",
                original_text=f"orig-{i}",
                corrected_text=f"corr-{i}",
                paragraph_context="context",
                document_id="doc-1",
            )

        # Assign some to datasets
        state_manager.assign_corrections_to_dataset(["corr-0", "corr-1"], "v1")
        state_manager.assign_corrections_to_dataset(["corr-2"], "v2")

        stats = state_manager.get_ocr_correction_stats()
        assert stats["total"] == 5
        assert stats["pending"] == 2
        assert stats["datasets"] == 2


class TestTrainingPipeline:
    """Tests for training pipeline and dataset management."""

    def test_create_dataset_insufficient_samples(self, state_manager, ocr_config):
        """Test dataset creation fails with insufficient samples."""
        from rock_paper_sync.ocr.training import DatasetManager

        manager = DatasetManager(ocr_config.cache_dir, state_manager)

        # Add only 5 corrections (below threshold of 10)
        for i in range(5):
            state_manager.add_ocr_correction(
                correction_id=f"corr-{i}",
                image_hash=f"img-{i}",
                image_path=f"/path/{i}.png",
                original_text=f"orig-{i}",
                corrected_text=f"corr-{i}",
                paragraph_context="context",
                document_id="doc-1",
            )

        result = manager.create_dataset_version(min_samples=10)
        assert result is None

    def test_create_dataset_success(self, state_manager, ocr_config):
        """Test successful dataset creation."""
        from rock_paper_sync.ocr.training import DatasetManager

        manager = DatasetManager(ocr_config.cache_dir, state_manager)

        # Add enough corrections
        for i in range(15):
            state_manager.add_ocr_correction(
                correction_id=f"corr-{i}",
                image_hash=f"img-{i}",
                image_path=f"/path/{i}.png",
                original_text=f"orig-{i}",
                corrected_text=f"corr-{i}",
                paragraph_context="context",
                document_id="doc-1",
            )

        result = manager.create_dataset_version(min_samples=10)
        assert result is not None
        assert result.sample_count == 15
        assert result.parquet_path.exists()
        assert result.manifest_path.exists()

        # Verify corrections assigned
        pending = state_manager.get_pending_ocr_corrections()
        assert len(pending) == 0

    def test_get_dataset_versions(self, state_manager, ocr_config):
        """Test listing dataset versions."""
        from rock_paper_sync.ocr.training import DatasetManager

        manager = DatasetManager(ocr_config.cache_dir, state_manager)

        # Create two datasets
        for batch in range(2):
            for i in range(10):
                state_manager.add_ocr_correction(
                    correction_id=f"corr-{batch}-{i}",
                    image_hash=f"img-{batch}-{i}",
                    image_path=f"/path/{batch}-{i}.png",
                    original_text=f"orig-{batch}-{i}",
                    corrected_text=f"corr-{batch}-{i}",
                    paragraph_context="context",
                    document_id="doc-1",
                )
            manager.create_dataset_version(min_samples=10)

        versions = manager.get_dataset_versions()
        assert len(versions) == 2

    def test_model_registry(self, ocr_config):
        """Test model registry operations."""
        from rock_paper_sync.ocr.training import ModelRegistry, ModelVersion
        from datetime import datetime

        registry = ModelRegistry(ocr_config.cache_dir)

        # Register a model
        model = ModelVersion(
            version="ft-v1",
            base_model="microsoft/trocr-base-handwritten",
            dataset_version="v1",
            created_at=datetime.now(),
            checkpoint_path=ocr_config.cache_dir / "models" / "ft-v1",
            metrics={"cer": 0.05, "wer": 0.1},
        )

        registry.register(model)

        # Retrieve it
        retrieved = registry.get_version("ft-v1")
        assert retrieved is not None
        assert retrieved.version == "ft-v1"
        assert retrieved.metrics["cer"] == 0.05

    def test_model_activation(self, ocr_config):
        """Test model activation and retrieval."""
        from rock_paper_sync.ocr.training import ModelRegistry, ModelVersion
        from datetime import datetime

        registry = ModelRegistry(ocr_config.cache_dir)

        # Register two models
        for v in ["ft-v1", "ft-v2"]:
            model = ModelVersion(
                version=v,
                base_model="microsoft/trocr-base-handwritten",
                dataset_version=v.replace("ft-", ""),
                created_at=datetime.now(),
                checkpoint_path=ocr_config.cache_dir / "models" / v,
                metrics={},
            )
            registry.register(model)

        # Activate v1
        registry.activate("ft-v1")
        active = registry.get_active()
        assert active is not None
        assert active.version == "ft-v1"

        # Activate v2
        registry.activate("ft-v2")
        active = registry.get_active()
        assert active.version == "ft-v2"

    def test_training_pipeline_stats(self, state_manager, ocr_config):
        """Test training pipeline statistics."""
        from rock_paper_sync.ocr.training import TrainingPipeline

        pipeline = TrainingPipeline(ocr_config, state_manager)

        # Add some corrections
        for i in range(5):
            state_manager.add_ocr_correction(
                correction_id=f"corr-{i}",
                image_hash=f"img-{i}",
                image_path=f"/path/{i}.png",
                original_text=f"orig-{i}",
                corrected_text=f"corr-{i}",
                paragraph_context="context",
                document_id="doc-1",
            )

        stats = pipeline.get_stats()
        assert stats["corrections"]["pending"] == 5
        assert stats["corrections"]["total"] == 5
        assert stats["datasets"] == 0
        assert stats["models"] == 0


class TestOCRIntegration:
    """Tests for OCR integration with sync flow."""

    def test_ocr_processor_initialization(self, ocr_config, state_manager):
        """Test OCR processor initialization."""
        from rock_paper_sync.ocr.integration import OCRProcessor

        processor = OCRProcessor(ocr_config, state_manager)
        assert processor.config == ocr_config

    def test_strip_ocr_markers_integration(self, ocr_config, state_manager, markdown_with_ocr_markers):
        """Test stripping OCR markers through processor."""
        from rock_paper_sync.ocr.integration import OCRProcessor

        processor = OCRProcessor(ocr_config, state_manager)
        result = processor.strip_ocr_markers(markdown_with_ocr_markers)

        assert "RPS:OCR" not in result
        assert "handwritten note" not in result

    def test_get_ocr_stats(self, ocr_config, state_manager):
        """Test getting OCR statistics through processor."""
        from rock_paper_sync.ocr.integration import OCRProcessor

        processor = OCRProcessor(ocr_config, state_manager)

        # Add some corrections
        for i in range(3):
            state_manager.add_ocr_correction(
                correction_id=f"corr-{i}",
                image_hash=f"img-{i}",
                image_path=f"/path/{i}.png",
                original_text=f"orig-{i}",
                corrected_text=f"corr-{i}",
                paragraph_context="context",
                document_id="doc-1",
            )

        stats = processor.get_stats()
        assert stats["corrections_pending"] == 3
        assert stats["corrections_total"] == 3

    @patch("rock_paper_sync.ocr.factory.create_ocr_service")
    def test_process_annotations_with_mock_service(
        self, mock_create_service, ocr_config, state_manager, mock_ocr_service
    ):
        """Test processing annotations with mocked OCR service."""
        from rock_paper_sync.ocr.integration import OCRProcessor
        from rock_paper_sync.ocr.markers import AnnotationInfo

        mock_create_service.return_value = mock_ocr_service

        processor = OCRProcessor(ocr_config, state_manager)

        # Mock annotation data
        annotation_map = {
            0: AnnotationInfo(paragraph_index=0, highlights=1, strokes=1)
        }

        # This would need actual .rm files to work fully
        # For now, test that the method runs without error
        result = processor.process_annotations(
            vault_name="test-vault",
            obsidian_path="test.md",
            markdown_content="# Test\n\nParagraph",
            annotation_map=annotation_map,
            rm_files=[],
            paragraph_texts=["Paragraph"],
        )

        # Without .rm files, should return unchanged content
        assert "Test" in result


class TestOCRConfigValidation:
    """Tests for OCR configuration validation."""

    def test_valid_ocr_config(self, tmp_path):
        """Test valid OCR configuration."""
        from rock_paper_sync.config import OCRConfig

        config = OCRConfig(
            enabled=True,
            provider="runpods",
            confidence_threshold=0.8,
            timeout=60.0,
        )

        assert config.enabled is True
        assert config.provider == "runpods"

    def test_config_defaults(self):
        """Test OCR config defaults."""
        from rock_paper_sync.config import OCRConfig

        config = OCRConfig()

        assert config.enabled is False
        assert config.provider == "runpods"
        assert config.model_version == "latest"
        assert config.confidence_threshold == 0.7
        assert config.container_runtime == "podman"

    def test_config_loading_with_ocr(self, tmp_path, temp_vault):
        """Test loading config file with OCR section."""
        from rock_paper_sync.config import load_config

        config_path = tmp_path / "config.toml"
        config_content = f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test-vault"
path = "{temp_vault}"
include_patterns = ["**/*.md"]

[cloud]
base_url = "http://localhost:3000"

[layout]
lines_per_page = 45

[logging]
level = "info"
file = "{tmp_path / 'sync.log'}"

[ocr]
enabled = true
provider = "runpods"
confidence_threshold = 0.8
min_corrections_for_dataset = 50
"""
        config_path.write_text(config_content)

        config = load_config(config_path)

        assert config.ocr.enabled is True
        assert config.ocr.provider == "runpods"
        assert config.ocr.confidence_threshold == 0.8
        assert config.ocr.min_corrections_for_dataset == 50


class TestRunpodsService:
    """Tests for Runpods OCR service client."""

    def test_service_initialization_without_credentials(self):
        """Test that service raises error without credentials."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService
        from rock_paper_sync.ocr.protocol import OCRServiceError

        with pytest.raises(OCRServiceError, match="endpoint ID required"):
            RunpodsOCRService()

    def test_service_initialization_with_credentials(self):
        """Test service initialization with credentials."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService

        service = RunpodsOCRService(
            endpoint_id="test-endpoint",
            api_key="test-key",
        )

        assert service.endpoint_id == "test-endpoint"
        service.close()

    @patch("httpx.Client.get")
    def test_health_check(self, mock_get):
        """Test health check endpoint."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        service = RunpodsOCRService(
            endpoint_id="test-endpoint",
            api_key="test-key",
        )

        result = service.health_check()
        assert result is True
        service.close()


class TestOCREndToEnd:
    """End-to-end tests for OCR correction workflow."""

    def test_full_correction_workflow(self, state_manager, ocr_config):
        """Test full workflow: store OCR -> detect correction -> store for training."""
        from rock_paper_sync.ocr.corrections import CorrectionManager

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)

        # 1. Store initial OCR result (simulating first sync)
        original_ocr = "handwriten note"  # typo
        state_manager.update_ocr_result(
            vault_name="test-vault",
            obsidian_path="notes/meeting.md",
            annotation_uuid="ann-uuid-1",
            paragraph_index=0,  # First paragraph (the annotated one)
            ocr_text=original_ocr,
            ocr_text_hash=hashlib.sha256(original_ocr.encode()).hexdigest(),
            original_text_hash=hashlib.sha256(b"Discussion points").hexdigest(),
            image_hash="img-abc123",
            confidence=0.85,
            model_version="base-v1",
        )

        # 2. User corrects OCR in markdown
        corrected_markdown = """<!-- RPS:ANNOTATED highlights=0 strokes=2 -->
Discussion points
<!-- RPS:OCR -->
handwritten note
<!-- RPS:END -->
"""

        # 3. Detect corrections
        corrections, conflicts = manager.process_markdown_file(
            "test-vault",
            "notes/meeting.md",
            corrected_markdown,
        )

        # 4. Verify correction detected and stored
        assert len(corrections) == 1
        assert corrections[0].original_text == "handwriten note"
        assert corrections[0].corrected_text == "handwritten note"

        # 5. Verify stored for training
        pending = state_manager.get_pending_ocr_corrections()
        assert len(pending) == 1

    def test_multiple_documents_corrections(self, state_manager, ocr_config):
        """Test corrections across multiple documents."""
        from rock_paper_sync.ocr.corrections import CorrectionManager

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)

        # Store OCR for multiple documents
        docs = [
            ("doc1.md", "uuid-1", "orignal"),
            ("doc2.md", "uuid-2", "anothr"),
            ("doc3.md", "uuid-3", "mistke"),
        ]

        for path, uuid, ocr_text in docs:
            state_manager.update_ocr_result(
                vault_name="test-vault",
                obsidian_path=path,
                annotation_uuid=uuid,
                paragraph_index=0,
                ocr_text=ocr_text,
                ocr_text_hash=hashlib.sha256(ocr_text.encode()).hexdigest(),
                original_text_hash=hashlib.sha256(b"text").hexdigest(),
                image_hash=f"img-{uuid}",
                confidence=0.8,
                model_version="v1",
            )

        # Correct each document
        corrections_total = 0
        for path, uuid, _ in docs:
            corrected = path.replace(".md", "")  # just using path as corrected text
            markdown = f"""<!-- RPS:ANNOTATED highlights=1 strokes=1 -->
text
<!-- RPS:OCR -->
{corrected}
<!-- RPS:END -->"""

            corrections, _ = manager.process_markdown_file(
                "test-vault", path, markdown
            )
            corrections_total += len(corrections)

        assert corrections_total == 3

        # Verify all stored
        stats = state_manager.get_ocr_correction_stats()
        assert stats["pending"] == 3


class TestRunpodsServiceMethods:
    """Tests for Runpods OCR service methods with proper mocking."""

    @patch("httpx.Client.post")
    @patch("httpx.Client.get")
    def test_recognize_batch(self, mock_get, mock_post):
        """Test batch recognition with mocked HTTP."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService
        from rock_paper_sync.ocr.protocol import (
            BoundingBox,
            OCRRequest,
            ParagraphContext,
        )

        # Mock job submission
        mock_post_response = MagicMock()
        mock_post_response.json.return_value = {"id": "job-123"}
        mock_post_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_post_response

        # Mock status polling - return completed immediately
        mock_get_response = MagicMock()
        mock_get_response.json.return_value = {
            "status": "COMPLETED",
            "output": {
                "results": [
                    {
                        "text": "recognized text",
                        "confidence": 0.95,
                        "model_version": "v1",
                    }
                ]
            },
        }
        mock_get_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_get_response

        service = RunpodsOCRService(
            endpoint_id="test-endpoint",
            api_key="test-key",
        )

        # Create test request
        request = OCRRequest(
            image=b"fake image data",
            annotation_uuid="test-uuid",
            bounding_box=BoundingBox(0, 0, 100, 50),
            context=ParagraphContext(
                document_id="doc-1",
                page_number=1,
                paragraph_index=0,
                paragraph_text="test paragraph",
            ),
        )

        results = service.recognize_batch([request])

        assert len(results) == 1
        assert results[0].text == "recognized text"
        assert results[0].confidence == 0.95
        assert results[0].annotation_uuid == "test-uuid"

        service.close()

    @patch("httpx.Client.post")
    def test_get_model_info(self, mock_post):
        """Test model info retrieval."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {
                "version": "v1.0",
                "base_model": "microsoft/trocr-base-handwritten",
                "is_fine_tuned": True,
                "dataset_version": "ds-001",
                "created_at": "2025-01-15T10:00:00",
                "metrics": {"cer": 0.05},
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        service = RunpodsOCRService(
            endpoint_id="test-endpoint",
            api_key="test-key",
        )

        info = service.get_model_info()

        assert info.version == "v1.0"
        assert info.is_fine_tuned is True
        assert info.metrics["cer"] == 0.05

        service.close()

    @patch("httpx.Client.post")
    def test_fine_tune_job_submission(self, mock_post):
        """Test fine-tune job submission."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService

        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "train-job-456"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        service = RunpodsOCRService(
            endpoint_id="test-endpoint",
            api_key="test-key",
        )

        job = service.fine_tune("dataset-v1")

        assert job.job_id == "train-job-456"
        assert job.dataset_version == "dataset-v1"

        service.close()

    @patch("httpx.Client.get")
    def test_get_training_job_status(self, mock_get):
        """Test training job status retrieval."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService
        from rock_paper_sync.ocr.protocol import JobStatus

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "COMPLETED",
            "output": {
                "dataset_version": "ds-v1",
                "model_version": "ft-v1",
                "metrics": {"cer": 0.03},
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        service = RunpodsOCRService(
            endpoint_id="test-endpoint",
            api_key="test-key",
        )

        job = service.get_training_job("job-123")

        assert job.status == JobStatus.COMPLETED
        assert job.output_model_version == "ft-v1"
        assert job.metrics["cer"] == 0.03

        service.close()

    def test_service_context_manager(self):
        """Test service as context manager."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService

        with RunpodsOCRService(
            endpoint_id="test-endpoint",
            api_key="test-key",
        ) as service:
            assert service.endpoint_id == "test-endpoint"
        # Client should be closed after context


class TestRealPNGImages:
    """Tests with real PNG image creation and validation."""

    def test_store_real_png_image(self, state_manager, ocr_config):
        """Test storing actual PNG data."""
        from PIL import Image
        import io
        from rock_paper_sync.ocr.corrections import CorrectionManager

        # Create real PNG image
        img = Image.new("RGB", (100, 50), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_data = buf.getvalue()

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)
        image_hash = manager.store_annotation_image(image_data, "uuid-1")

        # Verify it's stored as a valid PNG
        stored_path = manager.images_dir / f"{image_hash}.png"
        assert stored_path.exists()

        # Verify it can be reopened as valid PNG
        reopened = Image.open(stored_path)
        assert reopened.size == (100, 50)
        assert reopened.mode == "RGB"

    def test_store_multiple_images(self, state_manager, ocr_config):
        """Test storing multiple images with deduplication."""
        from PIL import Image
        import io
        from rock_paper_sync.ocr.corrections import CorrectionManager

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)

        # Create different images
        hashes = []
        for i in range(3):
            img = Image.new("RGB", (50 + i * 10, 30), color=(i * 50, 0, 0))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_hash = manager.store_annotation_image(buf.getvalue(), f"uuid-{i}")
            hashes.append(image_hash)

        # All hashes should be unique
        assert len(set(hashes)) == 3

        # All files should exist
        for h in hashes:
            assert (manager.images_dir / f"{h}.png").exists()

    def test_image_deduplication(self, state_manager, ocr_config):
        """Test that identical images are deduplicated."""
        from PIL import Image
        import io
        from rock_paper_sync.ocr.corrections import CorrectionManager

        manager = CorrectionManager(ocr_config.cache_dir, state_manager)

        # Create same image twice
        img = Image.new("RGB", (100, 50), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_data = buf.getvalue()

        hash1 = manager.store_annotation_image(image_data, "uuid-1")
        hash2 = manager.store_annotation_image(image_data, "uuid-2")

        # Same hash for identical images
        assert hash1 == hash2

        # Only one file on disk
        png_files = list(manager.images_dir.glob("*.png"))
        assert len(png_files) == 1


class TestAnnotationImageRendering:
    """Tests for annotation image rendering."""

    def test_render_stroke_to_image(self):
        """Test rendering stroke annotations to PNG."""
        from rock_paper_sync.ocr.integration import OCRProcessor
        from rock_paper_sync.annotations import Annotation, AnnotationType, Stroke, Point
        from rock_paper_sync.config import OCRConfig
        from PIL import Image
        import io
        from pathlib import Path
        from unittest.mock import MagicMock

        # Create OCRProcessor with mock state manager
        config = OCRConfig(enabled=True, cache_dir=Path("/tmp/test-ocr"))
        mock_state = MagicMock()
        processor = OCRProcessor(config, mock_state)

        # Create test stroke
        stroke = Stroke(
            points=[
                Point(10, 10),
                Point(50, 30),
                Point(100, 10),
            ],
            color=0,  # black
            tool=1,
            thickness=2.0,
        )
        annotation = Annotation(type=AnnotationType.STROKE, stroke=stroke)

        # Render to image
        image_data, bbox = processor._render_annotations_to_image([annotation])

        # Verify image was created
        assert len(image_data) > 0
        assert bbox.width > 0
        assert bbox.height > 0

        # Verify it's a valid PNG
        img = Image.open(io.BytesIO(image_data))
        assert img.format == "PNG"
        assert img.size[0] > 0
        assert img.size[1] > 0

    def test_render_highlight_to_image(self):
        """Test rendering highlight annotations to PNG."""
        from rock_paper_sync.ocr.integration import OCRProcessor
        from rock_paper_sync.annotations import (
            Annotation,
            AnnotationType,
            Highlight,
            Rectangle,
        )
        from rock_paper_sync.config import OCRConfig
        from PIL import Image
        import io
        from pathlib import Path
        from unittest.mock import MagicMock

        config = OCRConfig(enabled=True, cache_dir=Path("/tmp/test-ocr"))
        mock_state = MagicMock()
        processor = OCRProcessor(config, mock_state)

        # Create test highlight
        highlight = Highlight(
            text="highlighted text",
            color=3,  # yellow
            rectangles=[Rectangle(x=10, y=20, w=200, h=30)],
        )
        annotation = Annotation(type=AnnotationType.HIGHLIGHT, highlight=highlight)

        # Render to image
        image_data, bbox = processor._render_annotations_to_image([annotation])

        # Verify image was created
        assert len(image_data) > 0

        # Verify it's a valid PNG
        img = Image.open(io.BytesIO(image_data))
        assert img.format == "PNG"

    def test_render_multiple_annotations(self):
        """Test rendering multiple annotations to single image."""
        from rock_paper_sync.ocr.integration import OCRProcessor
        from rock_paper_sync.annotations import (
            Annotation,
            AnnotationType,
            Stroke,
            Highlight,
            Point,
            Rectangle,
        )
        from rock_paper_sync.config import OCRConfig
        from PIL import Image
        import io
        from pathlib import Path
        from unittest.mock import MagicMock

        config = OCRConfig(enabled=True, cache_dir=Path("/tmp/test-ocr"))
        mock_state = MagicMock()
        processor = OCRProcessor(config, mock_state)

        # Create multiple annotations
        annotations = [
            Annotation(
                type=AnnotationType.STROKE,
                stroke=Stroke(
                    points=[Point(10, 10), Point(50, 50)],
                    color=0,
                    tool=1,
                    thickness=2.0,
                ),
            ),
            Annotation(
                type=AnnotationType.HIGHLIGHT,
                highlight=Highlight(
                    text="test",
                    color=3,
                    rectangles=[Rectangle(x=60, y=10, w=100, h=20)],
                ),
            ),
        ]

        # Render to single image
        image_data, bbox = processor._render_annotations_to_image(annotations)

        # Verify combined bounding box
        assert bbox.width >= 150  # Should include both
        assert len(image_data) > 0

        # Verify valid image
        img = Image.open(io.BytesIO(image_data))
        assert img.format == "PNG"

    def test_render_empty_annotations(self):
        """Test rendering with no annotations."""
        from rock_paper_sync.ocr.integration import OCRProcessor
        from rock_paper_sync.config import OCRConfig
        from pathlib import Path
        from unittest.mock import MagicMock

        config = OCRConfig(enabled=True, cache_dir=Path("/tmp/test-ocr"))
        mock_state = MagicMock()
        processor = OCRProcessor(config, mock_state)

        image_data, bbox = processor._render_annotations_to_image([])

        assert image_data == b""
        assert bbox.width == 0
        assert bbox.height == 0


class TestIntegrationWithCredentials:
    """Integration tests that require real credentials (skipped without them)."""

    @pytest.mark.manual
    def test_real_health_check(self, runpods_credentials):
        """Test actual Runpods health endpoint."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService

        with RunpodsOCRService() as service:
            result = service.health_check()
            # May be True or False depending on endpoint status
            assert isinstance(result, bool)

    @pytest.mark.manual
    def test_real_model_info(self, runpods_credentials):
        """Test fetching real model info."""
        from rock_paper_sync.ocr.runpods import RunpodsOCRService

        with RunpodsOCRService() as service:
            try:
                info = service.get_model_info()
                assert info.version is not None
                assert info.base_model is not None
            except Exception as e:
                # Endpoint might not be running
                pytest.skip(f"Endpoint not available: {e}")
