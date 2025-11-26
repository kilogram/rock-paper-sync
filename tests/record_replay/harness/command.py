"""Command execution utilities for test harness.

Provides subprocess execution with logging and output capture.
"""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .output import Colors, print_error, print_ok


@dataclass
class CommandResult:
    """Result of a command execution.

    Attributes:
        return_code: Exit code from command
        stdout: Standard output from command
        stderr: Standard error output from command
        duration: Execution time in seconds
    """

    return_code: int
    stdout: str
    stderr: str
    duration: float


def run_cmd(
    cmd: list[str],
    repo_root: Path,
    desc: str,
    timeout: int = 300,
    capture: bool = True,
) -> tuple[int, str, str]:
    """Execute a command and log the result.

    Args:
        cmd: Command and arguments
        repo_root: Working directory for command
        desc: Description for logging
        timeout: Timeout in seconds (default 300s for Runpods cold start)
        capture: Whether to capture output

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    print(f"\n{Colors.BOLD}> {desc}{Colors.END}")

    start = time.time()
    result = subprocess.run(
        cmd,
        cwd=repo_root,
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
        print_error(f"Command failed: {desc} ({duration:.1f}s)")
        if result.stderr:
            for line in result.stderr.strip().split("\n"):
                if line:
                    print(f"  {Colors.RED}{line}{Colors.END}")
    else:
        print_ok(f"Done: {desc} ({duration:.1f}s)")

    return result.returncode, result.stdout, result.stderr


def run_sync(
    config_file: Path,
    repo_root: Path,
    desc: str = "Sync",
    extra_args: list[str] | None = None,
) -> tuple[int, str, str]:
    """Run rock-paper-sync command.

    Args:
        config_file: Path to config file
        repo_root: Path to repository root
        desc: Description for logging
        extra_args: Additional arguments

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    cmd = ["uv", "run", "rock-paper-sync", "--config", str(config_file), "sync"]
    if extra_args:
        cmd.extend(extra_args)
    return run_cmd(cmd, repo_root, desc)


def run_unsync(
    config_file: Path,
    repo_root: Path,
    delete_from_cloud: bool = True,
) -> tuple[int, str, str]:
    """Run unsync command to cleanup.

    Args:
        config_file: Path to config file
        repo_root: Path to repository root
        delete_from_cloud: Whether to delete from cloud

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    cmd = ["uv", "run", "rock-paper-sync", "--config", str(config_file), "unsync", "-y"]
    if delete_from_cloud:
        cmd.append("--delete-from-cloud")
    return run_cmd(cmd, repo_root, "Unsync from cloud", timeout=30)
