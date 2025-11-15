# Multi-Vault Support

## Overview

Rock-paper-sync supports syncing multiple Obsidian vaults to a single reMarkable device. Each vault can optionally be organized into its own folder on the reMarkable for better organization.

## Configuration

### Basic Multi-Vault Setup

```toml
[paths]
state_database = "~/.local/share/rock-paper-sync/state.db"

[[vaults]]
name = "personal"
path = "~/obsidian-vault-personal"
remarkable_folder = "Personal Notes"
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**"]

[[vaults]]
name = "work"
path = "~/obsidian-vault-work"
remarkable_folder = "Work"
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**", "archive/**"]

[cloud]
base_url = "http://localhost:3000"
```

### Vault Configuration Options

Each `[[vaults]]` section supports:

- **name** (required): Unique identifier for the vault
- **path** (required): Absolute or expandable path to the Obsidian vault directory
- **remarkable_folder** (optional): Folder name on reMarkable. If omitted, files go to root
- **include_patterns** (required): List of glob patterns for files to sync (e.g., `["**/*.md"]`)
- **exclude_patterns** (optional): List of glob patterns for files to exclude

### Folder Organization Rules

1. **Single vault**: Can have or omit `remarkable_folder`
2. **Multiple vaults**: At most ONE vault can omit `remarkable_folder`
   - Prevents mixing files from different vaults in the root folder
   - Validation enforced at config load time

### Examples

#### Three Vaults with Folders

```toml
[[vaults]]
name = "personal"
path = "~/Documents/Personal Vault"
remarkable_folder = "Personal"
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**"]

[[vaults]]
name = "work"
path = "~/Documents/Work Vault"
remarkable_folder = "Work Projects"
include_patterns = ["**/*.md"]
exclude_patterns = ["archive/**", "templates/**"]

[[vaults]]
name = "reading"
path = "~/Documents/Reading Notes"
remarkable_folder = "Reading"
include_patterns = ["**/*.md"]
exclude_patterns = []
```

#### Two Vaults, One at Root

```toml
[[vaults]]
name = "main"
path = "~/obsidian-main"
# No remarkable_folder - files go to root
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**"]

[[vaults]]
name = "archive"
path = "~/obsidian-archive"
remarkable_folder = "Archive"
include_patterns = ["**/*.md"]
exclude_patterns = []
```

## CLI Usage

### Sync Commands

Sync all vaults:
```bash
rock-paper-sync sync
```

Sync specific vault:
```bash
rock-paper-sync sync --vault personal
```

Dry run (preview changes):
```bash
rock-paper-sync sync --dry-run
rock-paper-sync sync --vault work --dry-run
```

### Watch Mode

Watch all vaults:
```bash
rock-paper-sync watch
```

Watch specific vault:
```bash
rock-paper-sync watch --vault personal
```

### Status and Statistics

Overall status:
```bash
rock-paper-sync status
```

Output:
```
Sync Status (All Vaults):
  Total Synced:  25
  Total Pending: 0
  Total Errors:  0

Per-Vault Breakdown:
  personal: 15 synced, 0 errors
  work: 10 synced, 0 errors

Recent Activity:
  2024-01-15 14:30 [personal] synced notes/meeting.md
  2024-01-15 14:28 [work] synced projects/quarterly.md
  ...
```

Vault-specific status:
```bash
rock-paper-sync status --vault personal
```

Output:
```
Sync Status for 'personal':
  Synced:  15
  Pending: 0
  Errors:  0

Recent Activity:
  2024-01-15 14:30 synced notes/meeting.md
  2024-01-15 14:25 synced journal/2024-01-15.md
  ...
```

## State Database

### Schema Changes

The state database (schema v2) is vault-aware:

**sync_state** table:
- Primary key: `(vault_name, obsidian_path)`
- Tracks which files have been synced for each vault

**folder_mapping** table:
- Primary key: `(vault_name, obsidian_folder)`
- Maps Obsidian folders to reMarkable folder UUIDs per vault

**sync_history** table:
- Includes `vault_name` column
- Logs all sync actions with vault context

### Database Location

Default: `~/.local/share/rock-paper-sync/state.db`

Configure via:
```toml
[paths]
state_database = "/custom/path/to/state.db"
```

## Folder Hierarchy

### With Vault Folders

When `remarkable_folder` is set:
```
reMarkable Root
├── Personal Notes/         (vault root folder)
│   ├── projects/
│   │   └── document.md
│   └── journal/
│       └── 2024-01-15.md
└── Work/                   (vault root folder)
    ├── meetings/
    │   └── standup.md
    └── reports/
        └── quarterly.md
```

### Without Vault Folder

When `remarkable_folder` is omitted (only one vault can do this):
```
reMarkable Root
├── projects/               (from vault)
│   └── document.md
├── journal/                (from vault)
│   └── 2024-01-15.md
└── Work/                   (other vault with folder)
    └── meetings/
        └── standup.md
```

## Best Practices

1. **Unique vault names**: Use descriptive, unique names for each vault
2. **Folder organization**: Use `remarkable_folder` for better organization when syncing multiple vaults
3. **Exclude patterns**: Exclude `.obsidian/`, `templates/`, and other non-content folders
4. **Test first**: Use `--dry-run` to preview changes before actual sync
5. **Backup**: The state database tracks everything, but keep backups of your vaults

## Migration from Single Vault

If you previously used a single vault setup, your config might look like:

```toml
[paths]
obsidian_vault = "~/my-vault"
state_database = "~/.local/share/rock-paper-sync/state.db"

[sync]
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**"]
```

Convert to multi-vault:

```toml
[paths]
state_database = "~/.local/share/rock-paper-sync/state.db"

[[vaults]]
name = "my-vault"
path = "~/my-vault"
remarkable_folder = "My Notes"  # Optional
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**"]

[sync]
debounce_seconds = 5
```

**Note**: The database schema has changed (v1 → v2), so you'll need to re-sync all files. The old state won't be migrated.

## Troubleshooting

### "Vault names must be unique"

Each vault must have a unique `name` field. Change one of the duplicate names.

### "At most one vault can omit 'remarkable_folder'"

When you have multiple vaults configured, only one can have no `remarkable_folder` set. Add a `remarkable_folder` to all but one vault.

### "Vault 'xyz' not found in configuration"

When using `--vault xyz`, make sure 'xyz' matches a vault `name` in your config exactly.

### Files syncing to wrong location

Check your `remarkable_folder` settings. Files from a vault will appear under its configured folder (or root if no folder is set).
