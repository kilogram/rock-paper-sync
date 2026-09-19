# Rock-Paper-Sync task runner

# Full check: format, lint, types (new modules), import boundaries, tests
check: format-check lint-check types imports test

# Fast check: format + lint only (no tests)
check-fast: format-check lint-check

# Check formatting (no modifications)
format-check:
    uv run ruff format --check src tests

# Check lint
lint-check:
    uv run ruff check src tests

# Type-check new modules (Phase 1+4 scope)
types:
    uv run mypy --ignore-missing-imports src/rock_paper_sync/device_profile.py src/rock_paper_sync/cloud_reader.py src/rock_paper_sync/render/ src/rock_paper_sync/ink/ src/rock_paper_sync/pull_sync.py src/rock_paper_sync/errors.py

# Check import boundaries
imports:
    uv run lint-imports

# Run default test suite
test:
    uv run pytest

# Format code
format:
    uv run ruff format src tests

# Fix lint issues
fix:
    uv run ruff check --fix src tests
