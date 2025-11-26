"""Test harness logging and observation tracking.

Provides observation/error collection and result serialization.
"""

import json
from datetime import datetime
from pathlib import Path

from .output import Colors, print_error as print_error_msg
from .output import print_info, print_warn


class TestHarnessLogger:
    """Logger for test harness observations and errors.

    Tracks observations and errors with timestamps, with optional logging to disk.

    Attributes:
        observations: List of timestamped observations
        errors: List of timestamped errors
    """

    def __init__(self, log_dir: Path | None = None) -> None:
        """Initialize logger.

        Args:
            log_dir: Optional directory for log files
        """
        self.log_dir = log_dir
        self.observations: list[str] = []
        self.errors: list[str] = []

    def observe(self, msg: str) -> None:
        """Record an observation with timestamp.

        Args:
            msg: Observation message
        """
        ts = datetime.now().strftime("%H:%M:%S")
        self.observations.append(f"[{ts}] {msg}")
        print(f"{Colors.CYAN}  {msg}{Colors.END}")

    def error(self, msg: str) -> None:
        """Record an error with timestamp.

        Args:
            msg: Error message
        """
        ts = datetime.now().strftime("%H:%M:%S")
        self.errors.append(f"[{ts}] {msg}")
        print_error_msg(msg)

    def info(self, msg: str) -> None:
        """Print info message.

        Args:
            msg: Info message
        """
        print_info(msg)

    def warn(self, msg: str) -> None:
        """Print warning message.

        Args:
            msg: Warning message
        """
        print_warn(msg)

    def save_result(self, result: "DeviceTestResult") -> Path | None:
        """Save test result to JSON file.

        Args:
            result: Test result to save

        Returns:
            Path to saved file, or None if no log_dir
        """
        if not self.log_dir:
            return None

        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.log_dir / f"{result.name}_{timestamp}.json"

        with open(log_file, "w") as f:
            json.dump(
                {
                    "name": result.name,
                    "timestamp": result.timestamp,
                    "success": result.success,
                    "duration": result.duration,
                    "observations": result.observations,
                    "errors": result.errors,
                    "skipped": result.skipped,
                    "skip_reason": result.skip_reason,
                },
                f,
                indent=2,
            )

        return log_file


# Backward compatibility: Bench class delegates to new modules
class Bench:
    """Legacy name for TestHarnessLogger - use TestHarnessLogger instead.

    Maintained for backward compatibility.
    """

    def __init__(self, repo_root: Path, log_dir: Path | None = None) -> None:
        """Initialize bench with repository root.

        Args:
            repo_root: Path to repository root (for running commands)
            log_dir: Optional directory for log files
        """
        self.repo_root = repo_root
        self._logger = TestHarnessLogger(log_dir)
        # Copy attributes for backward compatibility
        self.observations = self._logger.observations
        self.errors = self._logger.errors
        self.log_dir = log_dir

    def observe(self, msg: str) -> None:
        """Record an observation with timestamp."""
        self._logger.observe(msg)
        self.observations = self._logger.observations

    def error(self, msg: str) -> None:
        """Record an error with timestamp."""
        self._logger.error(msg)
        self.errors = self._logger.errors

    def ok(self, msg: str) -> None:
        """Print success message."""
        from .output import print_ok
        print_ok(msg)

    def info(self, msg: str) -> None:
        """Print info message."""
        self._logger.info(msg)

    def warn(self, msg: str) -> None:
        """Print warning message."""
        self._logger.warn(msg)

    def header(self, title: str) -> None:
        """Print section header."""
        from .output import print_header
        print_header(title)

    def subheader(self, title: str) -> None:
        """Print subsection header."""
        from .output import print_subheader
        print_subheader(title)

    def run_cmd(
        self,
        cmd: list[str],
        desc: str,
        timeout: int = 300,
        capture: bool = True,
    ) -> tuple[int, str, str]:
        """Execute a command and log the result."""
        from .command import run_cmd
        return run_cmd(cmd, self.repo_root, desc, timeout, capture)

    def run_sync(
        self,
        config_file: Path,
        desc: str = "Sync",
        extra_args: list[str] | None = None,
    ) -> tuple[int, str, str]:
        """Run rock-paper-sync command."""
        from .command import run_sync
        return run_sync(config_file, self.repo_root, desc, extra_args)

    def run_unsync(
        self,
        config_file: Path,
        delete_from_cloud: bool = True,
    ) -> tuple[int, str, str]:
        """Run unsync command to cleanup."""
        from .command import run_unsync
        return run_unsync(config_file, self.repo_root, delete_from_cloud)

    def prompt_user(self, *messages: str) -> None:
        """Display a formatted prompt and wait for user input.

        Displays messages surrounded by separator lines and waits for user
        to press Enter. Handles keyboard interrupts gracefully.

        Args:
            *messages: Messages to display to user

        Raises:
            KeyboardInterrupt: If user presses Ctrl+C
        """
        # Display separator and messages
        self.info(f"\n{'='*70}")
        for msg in messages:
            self.info(msg)
        self.info(f"{'='*70}\n")

        # Wait for user input
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            self.warn("Interrupted by user")
            raise
        except OSError as e:
            # Handle case where stdin is not available due to pytest output capture
            if "reading from stdin while output is captured" in str(e):
                self.error(
                    "ERROR: Cannot read user input - pytest output capture is enabled.\n"
                    "Run tests with: uv run pytest tests/record_replay --online -s\n"
                    "(the -s flag disables output capture for interactive tests)"
                )
            raise

    def save_result(self, result: "DeviceTestResult") -> Path | None:
        """Save test result to JSON file."""
        return self._logger.save_result(result)


# Import at end to avoid circular import
from .base import DeviceTestResult  # noqa: E402, F401
