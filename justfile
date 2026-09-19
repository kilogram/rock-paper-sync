# Rock-Paper-Sync task runner

# Full check: format, lint, import boundaries, tests
check: format-check lint-check imports test

# Fast check: format + lint only (no tests)
check-fast: format-check lint-check

# Check formatting (no modifications)
format-check:
    uv run ruff format --check src tests

# Check lint
lint-check:
    uv run ruff check src tests

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
