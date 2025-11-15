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
from .rm_cloud_client import RmCloudClient
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

[rm_cloud]
# Optional: Enable Sync v3 API integration for live sync with xochitl
# This uses pure API calls - no filesystem access required!
# Uncomment and configure the following to enable:
# enabled = true
# base_url = "http://localhost:3000"  # Your rm_cloud URL
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(example_config)
    click.echo(f"Created config file: {output_path}")
    click.echo("\nNext steps:")
    click.echo("1. Edit the config file to set your vault and output paths")
    click.echo("2. Create the directories specified in the config")
    click.echo("3. (Optional) Configure rm_cloud integration")
    click.echo("4. Run: rock-paper-sync sync")


@main.command()
@click.option(
    "--url",
    "-u",
    default="http://localhost:3000",
    help="rm_cloud base URL",
)
@click.option(
    "--device-id",
    "-d",
    default="rock-paper-sync-001",
    help="Unique device identifier",
)
@click.argument("code")
def register(url: str, device_id: str, code: str) -> None:
    """Register as a device with rm_cloud.

    CODE is the one-time registration code from rm_cloud web UI.

    Steps to get a code:
    1. Open rm_cloud web UI (usually http://localhost:3000)
    2. Go to Settings > Connect a device
    3. Copy the one-time code
    4. Run: rock-paper-sync register <code>

    This command only needs to be run once. Credentials are saved locally.
    """
    client = RmCloudClient(base_url=url)

    if client.is_registered():
        if not click.confirm("Device already registered. Re-register?"):
            return

    try:
        click.echo(f"Registering device '{device_id}' with {url}...")
        creds = client.register_device(code, device_id)
        click.echo(f"✓ Device registered successfully!")
        click.echo(f"  Device ID: {creds.device_id}")
        click.echo(f"  Credentials saved to: {client.credentials_path}")
    except Exception as e:
        click.echo(f"✗ Registration failed: {e}", err=True)
        sys.exit(1)


@main.command()
@click.option(
    "--url",
    "-u",
    default="http://localhost:3000",
    help="rm_cloud base URL",
)
def trigger_sync(url: str) -> None:
    """Manually trigger sync notification to xochitl.

    Sends a sync-complete notification to all connected devices,
    telling xochitl to reload and display any new/updated documents.

    Requires device registration (run 'register' command first).
    """
    client = RmCloudClient(base_url=url)

    if not client.is_registered():
        click.echo("Error: Device not registered", err=True)
        click.echo("Run: rock-paper-sync register <code>", err=True)
        sys.exit(1)

    try:
        click.echo("Triggering sync notification...")
        notification_id = client.trigger_sync()
        click.echo(f"✓ Sync notification sent (ID: {notification_id})")
        click.echo("  xochitl should reload documents now")
    except Exception as e:
        click.echo(f"✗ Failed to trigger sync: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
