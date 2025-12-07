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


def run_sync(config_file: Path, desc: str = "Sync") -> None:
    """Run rock-paper-sync sync in-process for test coverage.

    Args:
        config_file: Path to config file
        desc: Description for logging

    Raises:
        RuntimeError: If sync fails
    """
    from rock_paper_sync.config import load_config
    from rock_paper_sync.converter import SyncEngine
    from rock_paper_sync.state import StateManager

    print(f"\n{Colors.BOLD}> {desc} (in-process){Colors.END}")
    start = time.time()

    try:
        config = load_config(config_file)
        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state)

        results = engine.sync_all_changed()

        uploaded = sum(1 for r in results if r.success and not r.skipped)
        skipped = sum(1 for r in results if r.success and r.skipped)
        failed = [r for r in results if not r.success]

        msg = f"Synced {uploaded}/{len(results)} file(s)"
        if skipped:
            msg += f", {skipped} unchanged"
        print(f"  {msg}")

        for result in results:
            if result.success and not result.skipped:
                print(f"  ✓ {result.path.name} ({result.page_count} page(s))")

        state.close()

        duration = time.time() - start
        if failed:
            errors = "; ".join(f"{r.path.name}: {r.error}" for r in failed)
            print_error(f"Sync failed: {desc} ({duration:.1f}s)")
            raise RuntimeError(errors)

        print_ok(f"Done: {desc} ({duration:.1f}s)")

    except RuntimeError:
        raise
    except Exception as e:
        duration = time.time() - start
        print_error(f"Sync failed: {desc} ({duration:.1f}s)")
        print(f"  {Colors.RED}{e}{Colors.END}")
        raise RuntimeError(str(e)) from e


def run_unsync(config_file: Path, delete_from_cloud: bool = True) -> None:
    """Run unsync command in-process.

    Args:
        config_file: Path to config file
        delete_from_cloud: Whether to delete from cloud

    Raises:
        RuntimeError: If unsync fails
    """
    from rock_paper_sync.config import load_config
    from rock_paper_sync.converter import SyncEngine
    from rock_paper_sync.state import StateManager

    print(f"\n{Colors.BOLD}> Unsync from cloud (in-process){Colors.END}")
    start = time.time()

    try:
        config = load_config(config_file)
        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state)

        print(f"  Unsyncing all {len(config.sync.vaults)} vault(s)...")

        total_removed = 0
        total_deleted = 0

        for vault_config in config.sync.vaults:
            removed, deleted = engine.unsync_vault(
                vault_config.name, delete_from_cloud=delete_from_cloud
            )
            total_removed += removed
            total_deleted += deleted

        print(f"  Total: {total_removed} files removed, {total_deleted} deleted from cloud")
        state.close()

        duration = time.time() - start
        print_ok(f"Done: Unsync from cloud ({duration:.1f}s)")

    except Exception as e:
        duration = time.time() - start
        print_error(f"Unsync failed ({duration:.1f}s)")
        print(f"  {Colors.RED}{e}{Colors.END}")
        raise RuntimeError(str(e)) from e
