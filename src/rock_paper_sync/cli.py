"""Command-line interface for reMarkable-Obsidian Sync.

Provides user-facing commands:
- sync: One-time sync of all changed files
- watch: Continuously monitor for changes
- status: Show sync statistics
- reset: Clear sync state
- init: Create example config file
"""

import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import click

from .config import AppConfig, load_config, validate_config
from .converter import SyncEngine
from .logging_setup import setup_logging
from .state import StateManager
from .watcher import VaultWatcher


@click.group()
@click.option(
    "--config",
    "-c",
    default="~/.config/rock-paper-sync/config.toml",
    help="Path to config file",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, config: str, verbose: bool) -> None:
    """reMarkable-Obsidian Sync Tool.

    Synchronize Obsidian markdown files to reMarkable Paper Pro format.

    Use --help on any command to see detailed usage.
    """
    ctx.ensure_object(dict)

    # Expand config path
    config_path = Path(config).expanduser()

    # Check if config exists (except for init command)
    if ctx.invoked_subcommand != "init" and not config_path.exists():
        click.echo(f"Error: Config file not found: {config_path}", err=True)
        click.echo(f"Create one using: rock-paper-sync init {config_path}", err=True)
        sys.exit(1)

    # Load and validate config (except for init command)
    if ctx.invoked_subcommand != "init":
        try:
            app_config = load_config(config_path)
            validate_config(app_config)
        except Exception as e:
            click.echo(f"Error loading config: {e}", err=True)
            sys.exit(1)

        # Override log level if verbose flag is set
        log_level = "debug" if verbose else app_config.log_level
        setup_logging(log_level, app_config.log_file)

        ctx.obj["config"] = app_config


@main.command()
@click.option("--dry-run", is_flag=True, help="Preview changes without writing files")
@click.pass_context
def sync(ctx: click.Context, dry_run: bool) -> None:
    """Sync all changed files once.

    Scans the Obsidian vault for files that have changed since the last sync
    and converts them to reMarkable format.

    Files are skipped if their content hash hasn't changed.
    """
    config: AppConfig = ctx.obj["config"]

    state = StateManager(config.sync.state_database)
    engine = SyncEngine(config, state)

    if dry_run:
        click.echo("Dry run mode - no files will be written")
        click.echo(f"Would scan: {config.sync.obsidian_vault}")
        click.echo(f"Would write to: {config.sync.remarkable_output}")
        state.close()
        return

    click.echo(f"Scanning {config.sync.obsidian_vault}...")
    results = engine.sync_all_changed()

    success_count = sum(1 for r in results if r.success)
    click.echo(f"\nSynced {success_count}/{len(results)} file(s)")

    # Show results
    for result in results:
        if result.success:
            click.echo(f"  ✓ {result.path.name} ({result.page_count} page(s))")
        else:
            click.echo(f"  ✗ {result.path.name}: {result.error}", err=True)

    state.close()


@main.command()
@click.pass_context
def watch(ctx: click.Context) -> None:  # pragma: no cover
    """Continuously monitor for changes.

    Watches the Obsidian vault directory for file modifications and
    automatically syncs changed files after a debounce period.

    Press Ctrl+C to stop watching.
    """
    config: AppConfig = ctx.obj["config"]

    state = StateManager(config.sync.state_database)
    engine = SyncEngine(config, state)

    def on_file_change(path: Path) -> None:
        """Callback for file changes."""
        result = engine.sync_file(path)
        if result.success:
            click.echo(f"Synced: {path.name} ({result.page_count} page(s))")
        else:
            click.echo(f"Error syncing {path.name}: {result.error}", err=True)

    watcher = VaultWatcher(
        config.sync.obsidian_vault, on_file_change, config.sync.debounce_seconds
    )

    # Set up graceful shutdown
    def shutdown(signum: int, frame: object) -> None:
        """Handle shutdown signals."""
        click.echo("\nShutting down...")
        watcher.stop()
        state.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    click.echo(f"Watching {config.sync.obsidian_vault}")
    click.echo(f"Debounce: {config.sync.debounce_seconds}s")
    click.echo("Press Ctrl+C to stop\n")

    watcher.start()

    # Keep main thread alive
    try:
        while True:
            signal.pause()  # type: ignore[attr-defined]
    except AttributeError:
        # Windows doesn't have signal.pause()
        while True:
            time.sleep(1)


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show sync status and statistics.

    Displays:
    - Number of synced, pending, and error files
    - Recent sync activity
    """
    config: AppConfig = ctx.obj["config"]

    state = StateManager(config.sync.state_database)

    # Get sync statistics
    stats = state.get_stats()

    click.echo("Sync Status:")
    click.echo(f"  Synced:  {stats.get('synced', 0)}")
    click.echo(f"  Pending: {stats.get('pending', 0)}")
    click.echo(f"  Errors:  {stats.get('error', 0)}")

    # Recent activity
    history = state.get_recent_history(limit=10)

    if history:
        click.echo("\nRecent Activity:")
        for obsidian_path, action, timestamp, details in history:
            dt = datetime.fromtimestamp(timestamp)
            click.echo(f"  {dt.strftime('%Y-%m-%d %H:%M')} {action:8s} {obsidian_path}")
    else:
        click.echo("\nNo sync history yet")

    state.close()


@main.command()
@click.confirmation_option(
    prompt="This will clear all sync state. Continue?"
)
@click.pass_context
def reset(ctx: click.Context) -> None:
    """Clear sync state (force full re-sync).

    Deletes all sync records from the database. The next sync will
    process all files as if they were new.

    Warning: This does not delete files from the reMarkable output directory.
    """
    config: AppConfig = ctx.obj["config"]

    state_db = config.sync.state_database
    if state_db.exists():
        state_db.unlink()
        click.echo("Sync state cleared")
    else:
        click.echo("No sync state to clear")


@main.command()
@click.argument("output", type=click.Path(), required=False)
def init(output: str | None) -> None:
    """Create example config file.

    Creates a TOML configuration file with default settings.
    Edit the file to set your vault and output paths.

    If OUTPUT is not specified, defaults to ~/.config/rock-paper-sync/config.toml
    """
    if output is None:
        output = "~/.config/rock-paper-sync/config.toml"

    output_path = Path(output).expanduser()

    if output_path.exists():
        if not click.confirm(f"{output_path} exists. Overwrite?"):
            return

    example_config = """# reMarkable-Obsidian Sync Configuration
# Edit paths below to match your setup

[paths]
obsidian_vault = "~/obsidian-vault"
remarkable_output = "~/remarkable-sync"
state_database = "~/.local/share/rock-paper-sync/state.db"

[sync]
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**", "templates/**"]
debounce_seconds = 5

[layout]
lines_per_page = 45
margin_top = 50
margin_bottom = 50
margin_left = 50
margin_right = 50

[logging]
level = "info"
file = "~/.local/share/rock-paper-sync/sync.log"
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(example_config)
    click.echo(f"Created config file: {output_path}")
    click.echo("\nNext steps:")
    click.echo("1. Edit the config file to set your vault and output paths")
    click.echo("2. Create the directories specified in the config")
    click.echo("3. Run: rock-paper-sync sync")


if __name__ == "__main__":
    main()
