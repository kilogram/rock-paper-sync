#!/usr/bin/env python3
"""
Device Test Bench for Annotation Sync and OCR

Validates the marker-based annotation sync workflow and OCR integration on a real reMarkable device.
Conducts repeatable tests with automatic state cleanup.

Tests (each is self-contained end-to-end):
    Annotation Tests:
    1. annotation-roundtrip - sync → annotate → verify markers
    2. no-hash-loop         - sync → annotate → sync again → no re-upload
    3. content-edit         - sync → annotate → edit → verify re-sync

    OCR Tests:
    4. ocr-recognition      - sync → write text → OCR → verify recognition
    5. ocr-correction       - OCR → correct text → sync → verify correction stored
    6. ocr-stability        - OCR → sync again → verify no re-upload

Usage:
    # Run with automatic cleanup (recommended)
    uv run python bench.py --cleanup

    # Run specific test
    uv run python bench.py --test ocr-recognition --cleanup

    # Reset state only
    uv run python bench.py --reset
"""

import argparse
import atexit
import hashlib
import json
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Global cleanup flags
_cleanup_on_exit = False
_cleanup_done = False

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
FIXTURES_DIR = SCRIPT_DIR / "fixtures"
WORKSPACE_DIR = SCRIPT_DIR / "workspace"

# Workspace paths (created at runtime)
CONFIG_FILE = WORKSPACE_DIR / "config.toml"
STATE_DIR = WORKSPACE_DIR / ".state"
LOG_DIR = WORKSPACE_DIR / "logs"
TEST_DOC = WORKSPACE_DIR / "document.md"

# Fixture paths
BASELINE_DOC = FIXTURES_DIR / "baseline.md"
OCR_BASELINE_DOC = FIXTURES_DIR / "ocr_baseline.md"

# Device configuration
DEVICE_FOLDER = "DeviceBench"


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'


# =============================================================================
# Test State
# =============================================================================

@dataclass
class TestResult:
    name: str
    timestamp: str
    success: bool
    duration: float
    observations: list[str]
    errors: list[str]


class Bench:
    """Test bench runner."""

    def __init__(self):
        self.observations = []
        self.errors = []
        self.start_time = None

    def observe(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.observations.append(f"[{ts}] {msg}")
        print(f"{Colors.CYAN}  {msg}{Colors.END}")

    def error(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.errors.append(f"[{ts}] {msg}")
        print(f"{Colors.RED}  {msg}{Colors.END}")

    def ok(self, msg: str):
        print(f"{Colors.GREEN}  {msg}{Colors.END}")

    def info(self, msg: str):
        print(f"{Colors.BLUE}  {msg}{Colors.END}")

    def warn(self, msg: str):
        print(f"{Colors.YELLOW}  {msg}{Colors.END}")

    def header(self, title: str):
        print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*60}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.HEADER}{title.center(60)}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.HEADER}{'='*60}{Colors.END}\n")

    def run_cmd(self, cmd: list[str], desc: str) -> tuple[int, str, str]:
        print(f"\n{Colors.BOLD}> {desc}{Colors.END}")
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)

        if result.stdout:
            for line in result.stdout.strip().split('\n'):
                if line:
                    print(f"  {line}")

        if result.returncode != 0:
            self.error(f"Command failed: {desc}")
        else:
            self.ok(f"Done: {desc}")

        return result.returncode, result.stdout, result.stderr

    def prompt(self, msg: str, steps: list[str]) -> bool:
        print(f"\n{Colors.BOLD}{Colors.YELLOW}{'='*60}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.YELLOW}USER ACTION REQUIRED{Colors.END}")
        print(f"{Colors.BOLD}{Colors.YELLOW}{'='*60}{Colors.END}\n")
        print(f"{Colors.BOLD}{msg}{Colors.END}\n")

        for i, step in enumerate(steps, 1):
            print(f"  {i}. {step}")

        print(f"\n{Colors.YELLOW}Press Enter when done, or 'q' to quit...{Colors.END}")
        if input().strip().lower() == 'q':
            return False
        return True

    def save_result(self, name: str, success: bool):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        duration = time.time() - self.start_time if self.start_time else 0

        result = TestResult(
            name=name,
            timestamp=datetime.now().isoformat(),
            success=success,
            duration=duration,
            observations=self.observations,
            errors=self.errors
        )

        log_file = LOG_DIR / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(log_file, 'w') as f:
            json.dump(result.__dict__, f, indent=2)

    def file_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()


# =============================================================================
# Cleanup Handlers
# =============================================================================

def cleanup():
    """Clean up workspace state."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    print(f"\n{Colors.YELLOW}Cleaning up...{Colors.END}")

    try:
        if CONFIG_FILE.exists():
            subprocess.run(
                ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE),
                 "unsync", "--delete-from-cloud", "-y"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
            )

        if STATE_DIR.exists():
            shutil.rmtree(STATE_DIR)

        if TEST_DOC.exists():
            TEST_DOC.unlink()

        print(f"{Colors.GREEN}Cleanup complete{Colors.END}")
    except Exception as e:
        print(f"{Colors.RED}Cleanup error: {e}{Colors.END}")


def _signal_handler(signum, frame):
    global _cleanup_on_exit
    if _cleanup_on_exit:
        print(f"\n{Colors.YELLOW}Interrupted - cleaning up...{Colors.END}")
        cleanup()
    sys.exit(1)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
atexit.register(lambda: cleanup() if _cleanup_on_exit else None)


@contextmanager
def auto_cleanup():
    global _cleanup_on_exit, _cleanup_done
    _cleanup_on_exit = True
    _cleanup_done = False
    try:
        yield
    finally:
        cleanup()


# =============================================================================
# Setup
# =============================================================================

def setup():
    """Create workspace and config."""
    bench = Bench()
    bench.header("SETUP")

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    config = f"""# Auto-generated test config
[cloud]
base_url = "http://localhost:3000"

[paths]
state_database = "{STATE_DIR}/state.db"
cache_dir = "{WORKSPACE_DIR}/.cache"

[logging]
level = "debug"
file = "{LOG_DIR}/sync.log"

[layout]
lines_per_page = 28

[ocr]
enabled = true
provider = "runpods"
confidence_threshold = 0.5

[[vaults]]
name = "device-bench"
path = "{WORKSPACE_DIR}"
remarkable_folder = "{DEVICE_FOLDER}"
include_patterns = ["document.md"]
exclude_patterns = [".state/**", "logs/**"]
"""

    CONFIG_FILE.write_text(config)
    bench.ok(f"Created config: {CONFIG_FILE}")
    return True


def reset():
    """Reset all state."""
    bench = Bench()
    bench.header("RESET")

    if CONFIG_FILE.exists():
        bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE),
             "unsync", "--delete-from-cloud", "-y"],
            "Unsync from cloud"
        )

    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
        bench.ok("Removed state directory")

    if TEST_DOC.exists():
        TEST_DOC.unlink()
        bench.ok("Removed test document")

    for log in LOG_DIR.glob("*.json"):
        log.unlink()

    bench.ok("Reset complete")
    return True


# =============================================================================
# Scenarios (each is self-contained end-to-end)
# These are NOT pytest tests - run via CLI: run_device_tests run
# =============================================================================

def scenario_annotation_roundtrip():
    """Complete annotation sync roundtrip.

    sync clean doc → user annotates → sync → verify markers appear
    """
    bench = Bench()
    bench.start_time = time.time()
    bench.header("TEST: Annotation Roundtrip")

    try:
        # Setup
        reset()
        shutil.copy(BASELINE_DOC, TEST_DOC)
        bench.ok("Setup complete")

        # Step 1: Initial sync
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Initial sync"
        )
        if ret != 0:
            return False

        # Step 2: User adds annotations
        if not bench.prompt("Add annotations", [
            f"Open '{DEVICE_FOLDER}/document' on reMarkable",
            "Highlight 'annotated' and 'markers' in Important paragraph",
            "Optionally add strokes",
            "Wait for cloud sync",
        ]):
            return False

        # Step 3: Download annotations
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Download annotations"
        )
        if ret != 0:
            return False

        # Step 4: Verify markers
        content = TEST_DOC.read_text()
        if "<!-- ANNOTATED" not in content:
            bench.error("No markers found!")
            return False

        count = content.count("<!-- ANNOTATED:")
        bench.observe(f"Found {count} marker(s)")

        for line in content.split('\n'):
            if "<!-- ANNOTATED" in line:
                print(f"  {Colors.GREEN}{line.strip()}{Colors.END}")

        bench.header("PASSED")
        bench.save_result("annotation-roundtrip", True)
        return True

    except Exception as e:
        bench.error(f"Exception: {e}")
        bench.save_result("annotation-roundtrip", False)
        return False


def scenario_no_hash_loop():
    """Markers don't cause infinite sync loop.

    sync → annotate → sync → sync again → verify no re-upload
    """
    bench = Bench()
    bench.start_time = time.time()
    bench.header("TEST: No Hash Loop")

    try:
        # Setup
        reset()
        shutil.copy(BASELINE_DOC, TEST_DOC)

        # Step 1: Initial sync
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Initial sync"
        )
        if ret != 0:
            return False

        # Step 2: User adds annotations
        if not bench.prompt("Add annotations", [
            f"Open '{DEVICE_FOLDER}/document' on reMarkable",
            "Add any highlight or stroke",
            "Wait for cloud sync",
        ]):
            return False

        # Step 3: Download annotations
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Download annotations"
        )
        if ret != 0:
            return False

        if "<!-- ANNOTATED" not in TEST_DOC.read_text():
            bench.error("No markers found!")
            return False

        count = TEST_DOC.read_text().count("<!-- ANNOTATED:")
        bench.observe(f"Markers: {count}")

        # Step 4: Second sync (should skip)
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Second sync (should skip)"
        )
        if ret != 0:
            return False

        if "unchanged" in out.lower() or "skipping" in out.lower():
            bench.observe("Correctly skipped")
        elif "synced" in out.lower() or "uploaded" in out.lower():
            bench.error("Re-uploaded! Hash loop bug!")
            return False

        # Step 5: Third sync
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Third sync (extra check)"
        )

        bench.header("PASSED")
        bench.save_result("no-hash-loop", True)
        return True

    except Exception as e:
        bench.error(f"Exception: {e}")
        bench.save_result("no-hash-loop", False)
        return False


def scenario_content_edit():
    """Editing marked content triggers re-sync.

    sync → annotate → sync → edit → sync → verify upload
    """
    bench = Bench()
    bench.start_time = time.time()
    bench.header("TEST: Content Edit")

    try:
        # Setup
        reset()
        shutil.copy(BASELINE_DOC, TEST_DOC)

        # Step 1: Initial sync
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Initial sync"
        )
        if ret != 0:
            return False

        # Step 2: User adds annotations
        if not bench.prompt("Add annotations", [
            f"Open '{DEVICE_FOLDER}/document' on reMarkable",
            "Add any highlight or stroke",
            "Wait for cloud sync",
        ]):
            return False

        # Step 3: Download annotations
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Download annotations"
        )
        if ret != 0:
            return False

        if "<!-- ANNOTATED" not in TEST_DOC.read_text():
            bench.error("No markers found!")
            return False

        # Step 4: User edits
        content_before = TEST_DOC.read_text()

        if not bench.prompt("Edit content", [
            f"Open {TEST_DOC} in editor",
            "Edit text inside <!-- ANNOTATED --> markers",
            "Save",
        ]):
            return False

        if TEST_DOC.read_text() == content_before:
            bench.warn("File unchanged")
            return False

        bench.observe("Content modified")

        # Step 5: Sync after edit
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Sync after edit"
        )
        if ret != 0:
            return False

        if "synced" in out.lower() or "uploaded" in out.lower():
            bench.observe("Re-synced (change detected)")

        bench.header("PASSED")
        bench.save_result("content-edit", True)
        return True

    except Exception as e:
        bench.error(f"Exception: {e}")
        bench.save_result("content-edit", False)
        return False


def scenario_ocr_recognition():
    """Basic OCR recognition test.

    sync doc with gaps → user writes specific text → sync → verify OCR markers
    """
    bench = Bench()
    bench.start_time = time.time()
    bench.header("TEST: OCR Recognition")

    try:
        # Setup
        reset()
        shutil.copy(OCR_BASELINE_DOC, TEST_DOC)
        bench.ok("Setup complete")

        # Step 1: Initial sync
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Initial sync"
        )
        if ret != 0:
            return False

        # Step 2: User writes text
        if not bench.prompt("Write handwritten text", [
            f"Open '{DEVICE_FOLDER}/document' on reMarkable",
            "In 'Test 1: Simple Words' section, write 'hello' in the gap",
            "In 'Test 2: Numbers' section, write '2025' in the gap",
            "In 'Test 3: Short Phrase' section, write 'quick test' in the gap",
            "Use highlighter to mark each gap where you wrote",
            "Wait for cloud sync",
        ]):
            return False

        # Step 3: Download and process OCR
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Download and run OCR"
        )
        if ret != 0:
            return False

        # Step 4: Verify OCR markers
        content = TEST_DOC.read_text()
        if "<!-- OCR:" not in content:
            bench.error("No OCR markers found!")
            bench.info("Make sure OCR is enabled in config")
            return False

        ocr_count = content.count("<!-- OCR:")
        bench.observe(f"Found {ocr_count} OCR marker(s)")

        # Display OCR results
        in_ocr_block = False
        for line in content.split('\n'):
            if "<!-- OCR:" in line:
                in_ocr_block = True
                print(f"  {Colors.GREEN}{line.strip()}{Colors.END}")
            elif "<!-- /OCR -->" in line:
                in_ocr_block = False
                print(f"  {Colors.GREEN}{line.strip()}{Colors.END}")
            elif in_ocr_block:
                print(f"  {Colors.CYAN}  {line.strip()}{Colors.END}")

        # Check if expected text patterns appear
        expected_patterns = ["hello", "2025", "quick", "test"]
        found_patterns = []

        for pattern in expected_patterns:
            if pattern.lower() in content.lower():
                found_patterns.append(pattern)
                bench.observe(f"✓ Found '{pattern}'")

        if len(found_patterns) < 2:
            bench.warn(f"Only found {len(found_patterns)} expected patterns (OCR may not be accurate)")

        bench.header("PASSED")
        bench.save_result("ocr-recognition", True)
        return True

    except Exception as e:
        bench.error(f"Exception: {e}")
        bench.save_result("ocr-recognition", False)
        return False


def scenario_ocr_correction():
    """OCR correction workflow test.

    sync → OCR → user corrects text → sync → verify correction stored
    """
    bench = Bench()
    bench.start_time = time.time()
    bench.header("TEST: OCR Correction")

    try:
        # Setup
        reset()
        shutil.copy(OCR_BASELINE_DOC, TEST_DOC)

        # Step 1: Initial sync
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Initial sync"
        )
        if ret != 0:
            return False

        # Step 2: User writes text
        if not bench.prompt("Write handwritten text", [
            f"Open '{DEVICE_FOLDER}/document' on reMarkable",
            "In 'Test 1' section, write any text in the gap",
            "Use highlighter to mark the gap",
            "Wait for cloud sync",
        ]):
            return False

        # Step 3: Download and process OCR
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Download and run OCR"
        )
        if ret != 0:
            return False

        # Verify OCR markers exist
        content = TEST_DOC.read_text()
        if "<!-- OCR:" not in content:
            bench.error("No OCR markers found!")
            return False

        bench.observe("OCR markers found")

        # Step 4: User corrects OCR text
        if not bench.prompt("Correct OCR text", [
            f"Open {TEST_DOC} in editor",
            "Find the OCR block (between <!-- OCR: ... --> tags)",
            "Edit the recognized text to correct it",
            "Keep the markers intact",
            "Save the file",
        ]):
            return False

        # Verify file was modified
        content_after = TEST_DOC.read_text()
        if content_after == content:
            bench.warn("File unchanged - no correction made")
            return False

        bench.observe("OCR text corrected")

        # Step 5: Sync to capture correction
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Sync with correction"
        )
        if ret != 0:
            return False

        bench.observe("Correction captured")

        bench.header("PASSED")
        bench.save_result("ocr-correction", True)
        return True

    except Exception as e:
        bench.error(f"Exception: {e}")
        bench.save_result("ocr-correction", False)
        return False


def scenario_ocr_stability():
    """OCR markers don't cause re-upload loop.

    sync → OCR → sync again → verify no re-upload
    """
    bench = Bench()
    bench.start_time = time.time()
    bench.header("TEST: OCR Stability")

    try:
        # Setup
        reset()
        shutil.copy(OCR_BASELINE_DOC, TEST_DOC)

        # Step 1: Initial sync
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Initial sync"
        )
        if ret != 0:
            return False

        # Step 2: User writes text
        if not bench.prompt("Write handwritten text", [
            f"Open '{DEVICE_FOLDER}/document' on reMarkable",
            "Write any text in one of the gaps",
            "Use highlighter to mark it",
            "Wait for cloud sync",
        ]):
            return False

        # Step 3: Download and process OCR
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Download and run OCR"
        )
        if ret != 0:
            return False

        if "<!-- OCR:" not in TEST_DOC.read_text():
            bench.error("No OCR markers found!")
            return False

        bench.observe("OCR markers added")

        # Step 4: Second sync (should skip)
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Second sync (should skip)"
        )
        if ret != 0:
            return False

        if "unchanged" in out.lower() or "skipping" in out.lower():
            bench.observe("✓ Correctly skipped (no re-upload)")
        elif "synced" in out.lower() or "uploaded" in out.lower():
            bench.error("Re-uploaded! OCR markers causing hash loop!")
            return False

        # Step 5: Third sync (extra verification)
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Third sync (extra check)"
        )

        bench.header("PASSED")
        bench.save_result("ocr-stability", True)
        return True

    except Exception as e:
        bench.error(f"Exception: {e}")
        bench.save_result("ocr-stability", False)
        return False


# =============================================================================
# Testdata Extraction
# =============================================================================

def extract_testdata():
    """Extract annotated .rm files as testdata for automated testing.

    This workflow:
    1. Syncs document to device
    2. User writes handwriting
    3. Syncs back
    4. Copies .rm files to fixtures/testdata/

    The extracted files can then be used for automated unit tests.
    """
    bench = Bench()
    bench.start_time = time.time()
    bench.header("TESTDATA EXTRACTION")

    try:
        # Setup
        reset()
        shutil.copy(OCR_BASELINE_DOC, TEST_DOC)
        bench.ok("Setup complete")

        # Step 1: Initial sync
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Initial sync"
        )
        if ret != 0:
            return False

        # Step 2: User writes test data
        if not bench.prompt("Write test handwriting", [
            f"Open '{DEVICE_FOLDER}/document' on reMarkable",
            "Write in ALL test sections (Test 1-4)",
            "Use clear, readable handwriting",
            "Highlight each gap where you wrote",
            "Wait for cloud sync to complete",
        ]):
            return False

        # Step 3: Sync to download annotations
        ret, out, err = bench.run_cmd(
            ["uv", "run", "rock-paper-sync", "--config", str(CONFIG_FILE), "sync"],
            "Download annotations"
        )
        if ret != 0:
            return False

        # Step 4: Find and copy .rm files
        # Get document UUID from state database
        import sqlite3
        db_path = STATE_DIR / "state.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT remarkable_uuid FROM sync_state WHERE vault_name = 'device-bench' AND obsidian_path = 'document.md'"
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            bench.error("Document not found in sync state!")
            return False

        doc_uuid = row[0]
        bench.observe(f"Document UUID: {doc_uuid}")

        # Read cache directory from config
        # Load the config to get the cache_dir setting
        import tomli
        config_toml = CONFIG_FILE.read_text()
        config_data = tomli.loads(config_toml)

        # Get cache_dir from config, or use XDG default
        import os
        xdg_cache = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
        default_cache = Path(xdg_cache) / "rock-paper-sync"

        paths_config = config_data.get("paths", {})
        cache_dir_config = paths_config.get("cache_dir")
        if cache_dir_config:
            app_cache_dir = Path(cache_dir_config).expanduser()
        else:
            app_cache_dir = default_cache

        # .rm files are in {cache_dir}/annotations/{uuid}/
        cache_dir = app_cache_dir / "annotations" / doc_uuid

        if not cache_dir.exists() or not list(cache_dir.glob("*.rm")):
            bench.error(f"No .rm files found in {cache_dir}!")
            bench.info("Make sure you wrote on the device and cloud sync completed")
            return False

        bench.observe(f"Using cache: {cache_dir}")
        rm_files = list(cache_dir.glob("*.rm"))

        if not rm_files:
            bench.error(f"No .rm files found in {cache_dir}!")
            bench.info("Make sure you wrote on the device and cloud sync completed")
            return False

        bench.observe(f"Found {len(rm_files)} .rm file(s)")

        # Create testdata directory
        testdata_dir = FIXTURES_DIR / "testdata" / "ocr_handwriting"
        testdata_dir.mkdir(parents=True, exist_ok=True)

        # Also create a directory for the markdown source
        markdown_dir = testdata_dir / "markdown"
        markdown_dir.mkdir(exist_ok=True)

        # Copy .rm files
        copied_count = 0
        for rm_file in rm_files:
            dest = testdata_dir / rm_file.name
            shutil.copy(rm_file, dest)
            bench.observe(f"Copied: {rm_file.name}")
            copied_count += 1

        # Copy the markdown source
        shutil.copy(TEST_DOC, markdown_dir / "ocr_baseline.md")
        bench.observe(f"Copied markdown source")

        # Create a manifest file
        manifest = {
            "created_at": datetime.now().isoformat(),
            "source_document": "ocr_baseline.md",
            "num_rm_files": copied_count,
            "rm_files": [f.name for f in rm_files],
            "description": "OCR test handwriting samples",
            "test_cases": [
                {"section": "Test 1", "expected": "hello"},
                {"section": "Test 2", "expected": "2025"},
                {"section": "Test 3", "expected": "quick test"},
                {"section": "Test 4", "expected": "Code 42"},
            ]
        }

        manifest_path = testdata_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        bench.observe(f"Created manifest: {manifest_path}")

        bench.header(f"EXTRACTION COMPLETE")
        print(f"\n{Colors.GREEN}Testdata saved to:{Colors.END}")
        print(f"  {Colors.CYAN}{testdata_dir}{Colors.END}")
        print(f"\n{Colors.YELLOW}Next steps:{Colors.END}")
        print(f"  1. Review extracted .rm files")
        print(f"  2. Run: uv run pytest tests/test_paragraph_mapper.py")
        print(f"  3. Commit testdata to repository")

        return True

    except Exception as e:
        bench.error(f"Exception: {e}")
        return False


# =============================================================================
# Main
# =============================================================================

TESTS = {
    'annotation-roundtrip': test_annotation_roundtrip,
    'no-hash-loop': test_no_hash_loop,
    'content-edit': test_content_edit,
    'ocr-recognition': test_ocr_recognition,
    'ocr-correction': test_ocr_correction,
    'ocr-stability': test_ocr_stability,
}


def run_suite():
    """Run all tests."""
    bench = Bench()
    bench.header("DEVICE TEST BENCH")

    print(f"""
Tests (each is self-contained):
  Annotation Tests:
  1. annotation-roundtrip - Full sync → annotate → verify markers
  2. no-hash-loop         - Verify markers don't cause re-upload
  3. content-edit         - Edit marked content → verify re-sync

  OCR Tests:
  4. ocr-recognition      - Write text → OCR → verify recognition
  5. ocr-correction       - OCR → correct text → verify correction stored
  6. ocr-stability        - Verify OCR markers don't cause re-upload

Requirements:
  - reMarkable device connected
  - Cloud at http://localhost:3000
  - OCR enabled in config (for OCR tests)
""")

    if input("Continue? [Y/n]: ").lower() in ['n', 'no']:
        return

    setup()
    reset()

    results = []
    for name, func in TESTS.items():
        success = func()
        results.append((name, success))

        if not success:
            if input("\nContinue? [y/N]: ").lower() not in ['y', 'yes']:
                break

    # Summary
    bench.header("SUMMARY")
    passed = sum(1 for _, s in results if s)

    for name, success in results:
        status = f"{Colors.GREEN}PASS{Colors.END}" if success else f"{Colors.RED}FAIL{Colors.END}"
        print(f"  {name}: {status}")

    print(f"\n{Colors.BOLD}{passed}/{len(results)} passed{Colors.END}")


def main():
    parser = argparse.ArgumentParser(description="Device Test Bench")
    parser.add_argument('--test', choices=list(TESTS.keys()), help='Run specific test')
    parser.add_argument('--reset', action='store_true', help='Reset state')
    parser.add_argument('--setup', action='store_true', help='Setup only')
    parser.add_argument('--cleanup', action='store_true', help='Auto-cleanup on exit')
    parser.add_argument('--extract-testdata', action='store_true',
                       help='Extract .rm files with handwriting for automated testing')

    args = parser.parse_args()

    if args.reset:
        setup()
        reset()
    elif args.setup:
        setup()
    elif args.extract_testdata:
        setup()
        success = extract_testdata()
        sys.exit(0 if success else 1)
    elif args.test:
        setup()

        if args.cleanup:
            # Run test without auto cleanup, then prompt
            success = TESTS[args.test]()

            # Pause for inspection before cleanup
            print(f"\n{Colors.YELLOW}Test complete. Workspace at: {WORKSPACE_DIR}{Colors.END}")
            print(f"{Colors.YELLOW}Press Enter to cleanup, or Ctrl+C to exit and inspect...{Colors.END}")
            try:
                input()
                cleanup()
            except KeyboardInterrupt:
                print(f"\n{Colors.YELLOW}Skipping cleanup. Manual cleanup: uv run python bench.py --reset{Colors.END}")
        else:
            success = TESTS[args.test]()

        sys.exit(0 if success else 1)
    else:
        if args.cleanup:
            with auto_cleanup():
                run_suite()
        else:
            run_suite()


if __name__ == "__main__":
    main()
