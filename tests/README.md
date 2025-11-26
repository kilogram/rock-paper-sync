# Test Suite

## Structure

```
tests/
├── ocr/                      # OCR marker parsing, recognition, correction
├── sync/                     # Sync v3 protocol, cloud integration
├── annotations/              # Annotation marking, anchoring, preservation
├── service/                  # CLI, config, file watcher
├── parser_generator/         # Markdown parsing, reMarkable generation
├── record_replay/            # Device testing (online recording/offline replay)
├── fixtures/                 # Shared test data (markdown, config, credentials)
├── conftest.py              # Shared pytest fixtures
├── test_state.py            # State database
├── test_integration.py      # Full pipeline
└── test_multi_vault_config.py # Multi-vault support
```

## Running Tests

```bash
# All tests
uv run pytest tests/ -v

# By feature
uv run pytest tests/ocr/ -v
uv run pytest tests/sync/ -v
uv run pytest tests/annotations/ -v
uv run pytest tests/service/ -v
uv run pytest tests/parser_generator/ -v

# Device tests (no device needed)
uv run pytest tests/record_replay/ --device-mode=offline

# Record new device testdata
uv run pytest tests/record_replay/test_highlights.py::TestHighlightsRecording \
    --device-mode=online --online -s
```

## Coverage

```bash
uv run pytest --cov=rock_paper_sync --cov-report=term-missing tests/
```

## Device Testing

See `tests/record_replay/README.md` for recording/replay guide.

## Test Fixtures

- `fixtures/sample_markdown/` - Markdown test files
- `fixtures/config_samples/` - Configuration examples
- `fixtures/rmfakecloud.json` - Cloud test credentials
