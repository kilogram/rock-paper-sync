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
@click.option("--dry-run", is_flag=True, help="Preview changes without uploading")
@click.option("--vault", "-V", help="Sync specific vault only (by name)")
@click.pass_context
def sync(ctx: click.Context, dry_run: bool, vault: str | None) -> None:
    """Sync all changed files once.

    Scans the configured vaults for files that have changed since the last sync
    and uploads them to reMarkable cloud via API.

    Files are skipped if their content hash hasn't changed.

    Use --vault to sync only a specific vault by name.
    """
    config: AppConfig = ctx.obj["config"]

    state = StateManager(config.sync.state_database)
    engine = SyncEngine(config, state)

    # Validate vault name if specified
    if vault:
        vault_names = [v.name for v in config.sync.vaults]
        if vault not in vault_names:
            click.echo(f"Error: Vault '{vault}' not found in configuration", err=True)
            click.echo(f"Available vaults: {', '.join(vault_names)}", err=True)
            state.close()
            return

    if dry_run:
        click.echo("Dry run mode - no documents will be uploaded")
        if vault:
            vault_config = next(v for v in config.sync.vaults if v.name == vault)
            click.echo(f"Would scan vault '{vault}': {vault_config.path}")
        else:
            click.echo(f"Would scan {len(config.sync.vaults)} vault(s):")
            for v in config.sync.vaults:
                folder_note = f" -> {v.remarkable_folder}" if v.remarkable_folder else " -> root"
                click.echo(f"  - {v.name}: {v.path}{folder_note}")
        click.echo(f"Would upload to: {config.cloud.base_url}")
        state.close()
        return

    if vault:
        click.echo(f"Scanning vault '{vault}'...")
    else:
        click.echo(f"Scanning {len(config.sync.vaults)} vault(s)...")

    results = engine.sync_all_changed(vault_name=vault)

    success_count = sum(1 for r in results if r.success)
    click.echo(f"\nSynced {success_count}/{len(results)} file(s)")

    # Show results grouped by vault
    current_vault = None
    for result in sorted(results, key=lambda r: (r.vault_name, r.path.name)):
        if result.vault_name != current_vault:
            current_vault = result.vault_name
            click.echo(f"\n[{current_vault}]")

        if result.success:
            click.echo(f"  ✓ {result.path.name} ({result.page_count} page(s))")
        else:
            click.echo(f"  ✗ {result.path.name}: {result.error}", err=True)

    state.close()


@main.command()
@click.option("--vault", "-V", help="Watch specific vault only (by name)")
@click.pass_context
def watch(ctx: click.Context, vault: str | None) -> None:  # pragma: no cover
    """Continuously monitor for changes.

    Watches the configured vault directories for file modifications and
    automatically syncs changed files after a debounce period.

    Use --vault to watch only a specific vault by name.

    Press Ctrl+C to stop watching.
    """
    config: AppConfig = ctx.obj["config"]

    # Validate and filter vaults
    if vault:
        vault_configs = [v for v in config.sync.vaults if v.name == vault]
        if not vault_configs:
            vault_names = [v.name for v in config.sync.vaults]
            click.echo(f"Error: Vault '{vault}' not found in configuration", err=True)
            click.echo(f"Available vaults: {', '.join(vault_names)}", err=True)
            return
    else:
        vault_configs = config.sync.vaults

    state = StateManager(config.sync.state_database)
    engine = SyncEngine(config, state)

    # Create a mapping of path to vault config for the callback
    path_to_vault = {}
    for v in vault_configs:
        path_to_vault[v.path] = v

    def on_file_change(vault_path: Path, file_path: Path) -> None:
        """Callback for file changes."""
        vault_config = path_to_vault.get(vault_path)
        if not vault_config:
            click.echo(f"Warning: No vault config for {vault_path}", err=True)
            return

        result = engine.sync_file(vault_config, file_path)
        if result.success:
            click.echo(f"[{vault_config.name}] Synced: {file_path.name} ({result.page_count} page(s))")
        else:
            click.echo(f"[{vault_config.name}] Error syncing {file_path.name}: {result.error}", err=True)

    # Create watchers for all relevant vaults
    watchers = []
    for v in vault_configs:
        watcher = VaultWatcher(
            v.path,
            lambda p, vault_path=v.path: on_file_change(vault_path, p),
            config.sync.debounce_seconds,
        )
        watchers.append(watcher)

    # Set up graceful shutdown
    def shutdown(signum: int, frame: object) -> None:
        """Handle shutdown signals."""
        click.echo("\nShutting down...")
        for w in watchers:
            w.stop()
        state.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if len(watchers) == 1:
        click.echo(f"Watching vault '{vault_configs[0].name}' at {vault_configs[0].path}")
    else:
        click.echo(f"Watching {len(watchers)} vault(s):")
        for v in vault_configs:
            click.echo(f"  - {v.name}: {v.path}")

    click.echo(f"Debounce: {config.sync.debounce_seconds}s")
    click.echo("Press Ctrl+C to stop\n")

    for w in watchers:
        w.start()

    # Keep main thread alive
    try:
        while True:
            signal.pause()  # type: ignore[attr-defined]
    except AttributeError:
        # Windows doesn't have signal.pause()
        while True:
            time.sleep(1)


@main.command()
@click.option("--vault", "-V", help="Show status for specific vault only (by name)")
@click.pass_context
def status(ctx: click.Context, vault: str | None) -> None:
    """Show sync status and statistics.

    Displays:
    - Number of synced, pending, and error files per vault
    - Recent sync activity

    Use --vault to show stats for a specific vault only.
    """
    config: AppConfig = ctx.obj["config"]

    # Validate vault name if specified
    if vault:
        vault_names = [v.name for v in config.sync.vaults]
        if vault not in vault_names:
            click.echo(f"Error: Vault '{vault}' not found in configuration", err=True)
            click.echo(f"Available vaults: {', '.join(vault_names)}", err=True)
            return

    state = StateManager(config.sync.state_database)

    if vault:
        # Show stats for specific vault
        stats = state.get_stats(vault_name=vault)
        click.echo(f"Sync Status for '{vault}':")
        click.echo(f"  Synced:  {stats.get('synced', 0)}")
        click.echo(f"  Pending: {stats.get('pending', 0)}")
        click.echo(f"  Errors:  {stats.get('error', 0)}")

        # Recent activity for this vault
        history = state.get_recent_history(limit=10, vault_name=vault)
        if history:
            click.echo("\nRecent Activity:")
            for vault_name, obsidian_path, action, timestamp, details in history:
                dt = datetime.fromtimestamp(timestamp)
                click.echo(f"  {dt.strftime('%Y-%m-%d %H:%M')} {action:8s} {obsidian_path}")
        else:
            click.echo("\nNo sync history yet")
    else:
        # Show stats for all vaults
        click.echo("Sync Status (All Vaults):")

        # Overall stats
        total_stats = state.get_stats()
        click.echo(f"  Total Synced:  {total_stats.get('synced', 0)}")
        click.echo(f"  Total Pending: {total_stats.get('pending', 0)}")
        click.echo(f"  Total Errors:  {total_stats.get('error', 0)}")

        # Per-vault breakdown
        click.echo("\nPer-Vault Breakdown:")
        for v in config.sync.vaults:
            vault_stats = state.get_stats(vault_name=v.name)
            total = sum(vault_stats.values())
            if total > 0:
                click.echo(f"  {v.name}: {vault_stats.get('synced', 0)} synced, "
                          f"{vault_stats.get('error', 0)} errors")

        # Recent activity across all vaults
        history = state.get_recent_history(limit=10)
        if history:
            click.echo("\nRecent Activity:")
            for vault_name, obsidian_path, action, timestamp, details in history:
                dt = datetime.fromtimestamp(timestamp)
                click.echo(f"  {dt.strftime('%Y-%m-%d %H:%M')} [{vault_name}] {action:8s} {obsidian_path}")
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

    example_config = """# rock-paper-sync Configuration
# Sync Obsidian markdown to reMarkable via cloud API

[paths]
state_database = "~/.local/share/rock-paper-sync/state.db"

# Define your Obsidian vaults
# Each vault can optionally be organized into a folder on the reMarkable
[[vaults]]
name = "personal"
path = "~/obsidian-vault-personal"
remarkable_folder = "Personal Notes"  # Creates a folder on reMarkable
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**", "templates/**"]

[[vaults]]
name = "work"
path = "~/obsidian-vault-work"
remarkable_folder = "Work"  # Creates a folder on reMarkable
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**", "archive/**"]

# Example: Vault without a folder (files go to root)
# NOTE: Only one vault can omit remarkable_folder when multiple vaults are configured
# [[vaults]]
# name = "quick-notes"
# path = "~/obsidian-quick"
# # No remarkable_folder - files go directly to root
# include_patterns = ["**/*.md"]
# exclude_patterns = []

[sync]
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

[cloud]
# reMarkable cloud (or rm_cloud) connection
base_url = "http://localhost:3000"  # Change to https://webapp-prod.cloud.remarkable.com for real cloud
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(example_config)
    click.echo(f"Created config file: {output_path}")
    click.echo("\nNext steps:")
    click.echo("1. Edit the config file to set your vault paths and cloud URL")
    click.echo("   - Update vault paths to point to your actual Obsidian vaults")
    click.echo("   - Customize remarkable_folder names for each vault (optional)")
    click.echo("   - Remove/add vaults as needed")
    click.echo("2. Register as a device: rock-paper-sync register <code>")
    click.echo("   (Get code from rm_cloud web UI or reMarkable app)")
    click.echo("3. Run: rock-paper-sync sync")
    click.echo("   (Or: rock-paper-sync sync --vault <name> to sync specific vault)")


@main.command()
@click.option(
    "--device-id",
    "-d",
    default="rock-paper-sync-001",
    help="Unique device identifier",
)
@click.argument("code")
@click.pass_context
def register(ctx: click.Context, device_id: str, code: str) -> None:
    """Register as a device with reMarkable cloud.

    CODE is the one-time registration code from rm_cloud web UI.

    Steps to get a code:
    1. Open rm_cloud web UI (configured in your config.toml)
    2. Go to Settings > Connect a device
    3. Copy the one-time code
    4. Run: rock-paper-sync register <code>

    This command only needs to be run once. Credentials are saved locally.
    Uses cloud.base_url from your config file.
    """
    # Load config to get cloud URL
    config: AppConfig = ctx.obj["config"]
    url = config.cloud.base_url

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
@click.pass_context
def trigger_sync(ctx: click.Context) -> None:
    """Manually trigger sync notification to xochitl.

    Sends a sync-complete notification to all connected devices,
    telling xochitl to reload and display any new/updated documents.

    Requires device registration (run 'register' command first).
    Uses cloud.base_url from your config file.
    """
    # Load config to get cloud URL
    config: AppConfig = ctx.obj["config"]
    url = config.cloud.base_url

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
