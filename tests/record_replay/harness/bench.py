"""Bench utilities for device testing.

Provides logging, command execution, and terminal output formatting.
"""

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class Colors:
    """ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    END = "\033[0m"
    BOLD = "\033[1m"


@dataclass
class CommandResult:
    """Result of a command execution."""

    return_code: int
    stdout: str
    stderr: str
    duration: float


class Bench:
    """Test bench utilities for logging and command execution.

    Provides:
    - Colored terminal output
    - Command execution with logging
    - Observation and error collection
    - Result serialization
    """

    def __init__(self, repo_root: Path, log_dir: Path | None = None) -> None:
        """Initialize bench with repository root.

        Args:
            repo_root: Path to repository root (for running commands)
            log_dir: Optional directory for log files
        """
        self.repo_root = repo_root
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
        print(f"{Colors.RED}  {msg}{Colors.END}")

    def ok(self, msg: str) -> None:
        """Print success message.

        Args:
            msg: Success message
        """
        print(f"{Colors.GREEN}  {msg}{Colors.END}")

    def info(self, msg: str) -> None:
        """Print info message.

        Args:
            msg: Info message
        """
        print(f"{Colors.BLUE}  {msg}{Colors.END}")

    def warn(self, msg: str) -> None:
        """Print warning message.

        Args:
            msg: Warning message
        """
        print(f"{Colors.YELLOW}  {msg}{Colors.END}")

    def header(self, title: str) -> None:
        """Print section header.

        Args:
            title: Header title
        """
        print(f"\n{Colors.BOLD}{Colors.HEADER}{'=' * 60}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.HEADER}{title.center(60)}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.HEADER}{'=' * 60}{Colors.END}\n")

    def subheader(self, title: str) -> None:
        """Print subsection header.

        Args:
            title: Subheader title
        """
        print(f"\n{Colors.BOLD}{Colors.BLUE}{'-' * 40}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.BLUE}{title}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.BLUE}{'-' * 40}{Colors.END}")

    def run_cmd(
        self,
        cmd: list[str],
        desc: str,
        timeout: int = 300,  # 5 minutes for Runpods cold start
        capture: bool = True,
    ) -> tuple[int, str, str]:
        """Execute a command and log the result.

        Args:
            cmd: Command and arguments
            desc: Description for logging
            timeout: Timeout in seconds
            capture: Whether to capture output

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        import time

        print(f"\n{Colors.BOLD}> {desc}{Colors.END}")

        start = time.time()
        result = subprocess.run(
            cmd,
            cwd=self.repo_root,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start

        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if line:
                    print(f"  {line}")

        if result.returncode != 0:
            self.error(f"Command failed: {desc} ({duration:.1f}s)")
            if result.stderr:
                for line in result.stderr.strip().split("\n"):
                    if line:
                        print(f"  {Colors.RED}{line}{Colors.END}")
        else:
            self.ok(f"Done: {desc} ({duration:.1f}s)")

        return result.returncode, result.stdout, result.stderr

    def run_sync(
        self,
        config_file: Path,
        desc: str = "Sync",
        extra_args: list[str] | None = None,
    ) -> tuple[int, str, str]:
        """Run rock-paper-sync command.

        Args:
            config_file: Path to config file
            desc: Description for logging
            extra_args: Additional arguments

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        cmd = ["uv", "run", "rock-paper-sync", "--config", str(config_file), "sync"]
        if extra_args:
            cmd.extend(extra_args)
        return self.run_cmd(cmd, desc)

    def run_unsync(
        self,
        config_file: Path,
        delete_from_cloud: bool = True,
    ) -> tuple[int, str, str]:
        """Run unsync command to cleanup.

        Args:
            config_file: Path to config file
            delete_from_cloud: Whether to delete from cloud

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        cmd = ["uv", "run", "rock-paper-sync", "--config", str(config_file), "unsync", "-y"]
        if delete_from_cloud:
            cmd.append("--delete-from-cloud")
        return self.run_cmd(cmd, "Unsync from cloud", timeout=30)

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


# Import at end to avoid circular import
from .base import DeviceTestResult  # noqa: E402, F401
