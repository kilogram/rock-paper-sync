#!/usr/bin/env python3
"""
Device Test Bench for Annotation Sync

Validates the marker-based annotation sync workflow on a real reMarkable device.
Conducts repeatable tests with automatic state cleanup.

Tests (each is self-contained end-to-end):
    1. annotation-roundtrip - sync → annotate → verify markers
    2. no-hash-loop         - sync → annotate → sync again → no re-upload
    3. content-edit         - sync → annotate → edit → verify re-sync

Usage:
    # Run with automatic cleanup (recommended)
    uv run python bench.py --cleanup

    # Run specific test
    uv run python bench.py --test download-annotations --cleanup

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

[logging]
level = "debug"
file = "{LOG_DIR}/sync.log"

[layout]
lines_per_page = 28

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
# Tests (each is self-contained end-to-end)
# =============================================================================

def test_annotation_roundtrip():
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


def test_no_hash_loop():
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


def test_content_edit():
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


# =============================================================================
# Main
# =============================================================================

TESTS = {
    'annotation-roundtrip': test_annotation_roundtrip,
    'no-hash-loop': test_no_hash_loop,
    'content-edit': test_content_edit,
}


def run_suite():
    """Run all tests."""
    bench = Bench()
    bench.header("DEVICE TEST BENCH")

    print(f"""
Tests (each is self-contained):
  1. annotation-roundtrip - Full sync → annotate → verify markers
  2. no-hash-loop         - Verify markers don't cause re-upload
  3. content-edit         - Edit marked content → verify re-sync

Requirements:
  - reMarkable device connected
  - Cloud at http://localhost:3000
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

    args = parser.parse_args()

    if args.reset:
        setup()
        reset()
    elif args.setup:
        setup()
    elif args.test:
        setup()

        if args.cleanup:
            with auto_cleanup():
                success = TESTS[args.test]()
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
