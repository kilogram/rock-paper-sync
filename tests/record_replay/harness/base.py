"""Base classes for device test harness.

Provides the foundational infrastructure for interactive device tests.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TypeVar


@dataclass
class DeviceTestResult:
    """Result of a device test execution."""

    name: str
    success: bool
    duration: float
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    observations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None


class DeviceTestCase(ABC):
    """Base class for device-interactive tests.

    Subclasses implement the `execute()` method which contains the test logic.
    The harness handles setup, teardown, timing, and result collection.

    Example:
        class AnnotationRoundtripTest(DeviceTestCase):
            name = "annotation-roundtrip"
            description = "Full annotation sync cycle"

            def execute(self) -> bool:
                self.sync_and_verify()
                self.prompt_user_annotation()
                self.sync_and_verify_markers()
                return True
    """

    name: str = "unnamed-test"
    description: str = ""
    requires_ocr: bool = False
    cleanup_on_success: bool = True
    cleanup_on_failure: bool = False

    def __init__(self, workspace: "WorkspaceManager", bench: "Bench") -> None:
        """Initialize test case with workspace and bench utilities.

        Args:
            workspace: Manages test workspace (config, state, documents)
            bench: Provides logging, command execution, and user interaction
        """
        self.workspace = workspace
        self.bench = bench
        self._start_time: float | None = None
        self._result: DeviceTestResult | None = None

    @abstractmethod
    def execute(self) -> bool:
        """Execute the test logic.

        Returns:
            True if test passed, False otherwise

        Raises:
            Any exception will be caught and recorded as test failure
        """
        pass

    def setup(self) -> None:
        """Optional setup before test execution.

        Override to perform test-specific setup.
        Called after workspace reset but before execute().
        """
        pass

    def teardown(self) -> None:
        """Optional cleanup after test execution.

        Override to perform test-specific cleanup.
        Called after execute() regardless of success/failure.
        """
        pass

    def skip_if(self, condition: bool, reason: str) -> None:
        """Skip the test if condition is true.

        Args:
            condition: If True, test will be skipped
            reason: Human-readable reason for skipping
        """
        if condition:
            raise SkipTestError(reason)

    @contextmanager
    def managed_run(self) -> Generator[None, None, None]:
        """Context manager for test execution with timing and cleanup.

        Handles:
        - Timing measurement
        - Setup/teardown calls
        - Exception capture
        - Result recording
        """
        self._start_time = time.time()
        self._result = DeviceTestResult(
            name=self.name,
            success=False,
            duration=0.0,
            observations=self.bench.observations.copy(),
            errors=self.bench.errors.copy(),
        )

        try:
            self.setup()
            yield
        except SkipTestError as e:
            self._result.skipped = True
            self._result.skip_reason = str(e)
            self._result.success = True  # Skipped tests are not failures
        except Exception as e:
            self.bench.error(f"Test exception: {e}")
            self._result.errors.append(str(e))
        finally:
            try:
                self.teardown()
            except Exception as e:
                self.bench.error(f"Teardown exception: {e}")

            self._result.duration = time.time() - self._start_time
            self._result.observations = self.bench.observations.copy()
            self._result.errors = self.bench.errors.copy()

    def run(self) -> DeviceTestResult:
        """Execute the test with full lifecycle management.

        Returns:
            DeviceTestResult with test outcome and metadata
        """
        self.bench.header(f"TEST: {self.name}")
        self.bench.observations.clear()
        self.bench.errors.clear()

        with self.managed_run():
            success = self.execute()
            self._result.success = success

        # Log result
        if self._result.skipped:
            self.bench.warn(f"SKIPPED: {self._result.skip_reason}")
        elif self._result.success:
            self.bench.header("PASSED")
        else:
            self.bench.header("FAILED")

        return self._result

    # Convenience methods for common operations

    def sync(self, description: str = "Sync") -> tuple[int, str, str]:
        """Run sync command and return result.

        Args:
            description: Description for logging

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        return self.workspace.run_sync(description)

    def sync_and_assert_success(self, description: str = "Sync") -> str:
        """Run sync and assert it succeeds.

        Args:
            description: Description for logging

        Returns:
            stdout from sync command

        Raises:
            AssertionError if sync fails
        """
        ret, out, err = self.sync(description)
        assert ret == 0, f"Sync failed: {err}"
        return out

    def assert_file_contains(self, path: Path, pattern: str, msg: str | None = None) -> None:
        """Assert file contains pattern.

        Args:
            path: Path to file
            pattern: String pattern to search for
            msg: Optional failure message
        """
        content = path.read_text()
        assert pattern in content, msg or f"Pattern '{pattern}' not found in {path.name}"

    def assert_markers_present(self, marker_type: str = "<!-- ANNOTATED") -> int:
        """Assert annotation markers are present in test document.

        Args:
            marker_type: Marker pattern to search for

        Returns:
            Number of markers found

        Raises:
            AssertionError if no markers found
        """
        content = self.workspace.test_doc.read_text()
        count = content.count(marker_type)
        assert count > 0, f"No {marker_type} markers found"
        self.bench.observe(f"Found {count} {marker_type} marker(s)")
        return count


class SkipTestError(Exception):
    """Exception raised to skip a test."""

    pass


# Type variable for decorator
F = TypeVar("F", bound=Callable)


def device_test(
    requires_ocr: bool = False,
    cleanup_on_success: bool = True,
    cleanup_on_failure: bool = False,
) -> Callable[[F], F]:
    """Decorator marking a method as a device test.

    Can be applied to test methods or classes.

    Args:
        requires_ocr: Whether test requires OCR service
        cleanup_on_success: Whether to cleanup workspace after success
        cleanup_on_failure: Whether to cleanup workspace after failure

    Example:
        @device_test(requires_ocr=True)
        def test_ocr_recognition(self):
            ...
    """

    def decorator(func: F) -> F:
        func._device_test = True  # type: ignore
        func._requires_ocr = requires_ocr  # type: ignore
        func._cleanup_on_success = cleanup_on_success  # type: ignore
        func._cleanup_on_failure = cleanup_on_failure  # type: ignore
        return func

    return decorator


def requires_ocr(func: F) -> F:
    """Shorthand decorator for tests requiring OCR."""
    func._device_test = True  # type: ignore
    func._requires_ocr = True  # type: ignore
    return func


# Import workspace at end to avoid circular import
from .logging import Bench  # noqa: E402
from .workspace import WorkspaceManager  # noqa: E402
