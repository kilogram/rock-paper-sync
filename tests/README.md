# Test Suite Documentation

This directory contains the comprehensive test suite for the rock-paper-sync project.

## Test Structure

```
tests/
├── README.md                 # This file
├── conftest.py              # Shared pytest fixtures
├── test_parser.py           # Markdown parser tests
├── test_state.py            # State management tests
├── test_config.py           # Configuration tests
├── test_integration.py      # Integration tests (full pipeline)
└── fixtures/
    ├── sample_markdown/     # Test markdown files
    └── config_samples/      # Sample config files
```

## Test Categories

### Unit Tests

#### test_parser.py
Comprehensive tests for the markdown parser module.

**Coverage:**
- Frontmatter extraction (YAML)
- Inline formatting (bold, italic, code, links, strikethrough)
- Content blocks (paragraphs, headers, lists, code blocks, blockquotes)
- Nested formatting
- Unicode handling
- Edge cases (empty files, malformed markdown)
- Position accuracy for formatting ranges

**Key Test Classes:**
- `TestFrontmatterExtraction` - YAML parsing
- `TestInlineFormatting` - Text formatting extraction
- `TestContentBlocks` - Block-level parsing
- `TestFileFixtures` - Testing against fixture files
- `TestFormattingPositionAccuracy` - Critical position tests

#### test_state.py
Tests for state database management.

**Coverage:**
- Database initialization and schema creation
- File state CRUD operations
- Folder mapping
- File content hashing (SHA-256)
- Finding changed files
- Sync history logging
- Statistics and reporting
- Database connection management
- WAL mode for concurrency

**Key Test Classes:**
- `TestStateManagerInit` - Database setup
- `TestFileState` - File tracking operations
- `TestFolderMapping` - Folder hierarchy
- `TestFileHashing` - Content hash generation
- `TestFindChangedFiles` - Change detection
- `TestSyncHistory` - History logging
- `TestStats` - Statistics reporting

#### test_config.py
Tests for configuration management.

**Coverage:**
- TOML config file loading
- Path expansion (~/ and environment variables)
- Configuration validation
- Required field checking
- Directory existence validation
- Numeric value validation (positive, non-negative)
- Log level validation
- Error messages for invalid configs

**Key Test Classes:**
- `TestExpandPath` - Path expansion utilities
- `TestLoadConfig` - Configuration loading
- `TestValidateConfig` - Validation rules

### Integration Tests

#### test_integration.py
Tests for integration between components and full pipeline workflows.

**Coverage:**
- Parser + State integration
- Config + State integration
- Multi-file workflows
- Nested folder structures
- Complex markdown scenarios
- Error recovery
- Performance testing

**Key Test Classes:**
- `TestParserStateIntegration` - Parser and state working together
- `TestConfigIntegration` - Config integration with components
- `TestComplexMarkdownScenarios` - Real-world document testing
- `TestErrorRecovery` - Error handling across components
- `TestFullPipelineStubs` - Placeholder tests for full pipeline (requires generator.py)
- `TestStateManagementEdgeCases` - Additional edge cases
- `TestPerformance` - Performance benchmarks

## Test Fixtures

### Sample Markdown Files

Located in `fixtures/sample_markdown/`:

1. **simple.md** - Basic paragraphs, no formatting
2. **headers.md** - All header levels (H1-H6)
3. **formatting.md** - Bold, italic, code, strikethrough
4. **lists.md** - Bullet lists, numbered lists, nested lists
5. **frontmatter.md** - YAML frontmatter example
6. **code_blocks.md** - Fenced code blocks with language tags
7. **complex.md** - Combination of all elements
8. **edge_cases.md** - Unusual markdown, unicode, special characters
9. **empty.md** - Empty file
10. **only_whitespace.md** - File with only whitespace
11. **comprehensive.md** - Complete test of all features
12. **long.md** - Long document for pagination testing (150+ lines)

### Config Samples

Located in `fixtures/config_samples/`:

1. **minimal_valid.toml** - Minimal valid configuration
2. **invalid_toml.toml** - Malformed TOML syntax
3. **missing_paths_section.toml** - Missing required section
4. **missing_vault_path.toml** - Missing required field

## Running Tests

### Run All Tests

```bash
pytest tests/
```

### Run Specific Test File

```bash
pytest tests/test_parser.py
pytest tests/test_state.py
pytest tests/test_config.py
pytest tests/test_integration.py
```

### Run Specific Test Class

```bash
pytest tests/test_parser.py::TestInlineFormatting
pytest tests/test_state.py::TestFileState
```

### Run Specific Test

```bash
pytest tests/test_parser.py::TestInlineFormatting::test_bold_text
```

### Run with Verbose Output

```bash
pytest tests/ -v
```

### Run with Coverage

```bash
pytest --cov=rock_paper_sync --cov-report=html --cov-report=term tests/
```

This generates:
- Terminal coverage report
- HTML coverage report in `htmlcov/` directory

### Run Only Fast Tests

```bash
pytest tests/ -m "not slow"
```

### Run Only Integration Tests

```bash
pytest tests/ -m integration
```

## Test Markers

Tests use pytest markers for categorization:

- `@pytest.mark.slow` - Tests that take significant time (> 1 second)
- `@pytest.mark.integration` - Integration tests spanning multiple components
- `@pytest.mark.skip(reason="...")` - Temporarily disabled tests

## Coverage Targets

**Overall Target: >80% coverage**

Coverage by module:
- `parser.py` - Target: 95%+ (critical component)
- `state.py` - Target: 90%+ (data integrity)
- `config.py` - Target: 85%+ (validation logic)
- `logging_setup.py` - Target: 70%+ (utility module)

**Excluded from coverage:**
- Test files themselves
- `__pycache__` directories
- Type checking blocks (`if TYPE_CHECKING:`)
- `__repr__` methods
- `if __name__ == "__main__":` blocks

## Common Test Patterns

### Testing File Operations

```python
def test_file_operation(tmp_path: Path):
    """Use tmp_path fixture for isolated file operations."""
    test_file = tmp_path / "test.md"
    test_file.write_text("content")
    # ... test logic ...
```

### Testing Database Operations

```python
def test_database_operation(temp_db: Path):
    """Use temp_db fixture for isolated database testing."""
    manager = StateManager(temp_db)
    # ... test logic ...
    manager.close()
```

### Testing with Fixtures

```python
def test_with_fixture(sample_markdown_dir: Path):
    """Load fixture files for testing."""
    doc = parse_markdown_file(sample_markdown_dir / "simple.md")
    assert doc.title == "simple"
```

### Testing Error Conditions

```python
def test_error_condition():
    """Use pytest.raises for expected exceptions."""
    with pytest.raises(ConfigError, match="specific error message"):
        load_config(Path("nonexistent.toml"))
```

## Adding New Tests

When adding new tests:

1. **Choose the right file:**
   - Unit tests for single module → `test_<module>.py`
   - Integration tests → `test_integration.py`
   - New component → Create new `test_<component>.py`

2. **Use descriptive names:**
   - Test functions: `test_<what_it_tests>`
   - Test classes: `Test<Feature>`
   - Example: `test_parse_bold_text`, `TestInlineFormatting`

3. **Follow AAA pattern:**
   - **Arrange:** Set up test data
   - **Act:** Execute the code being tested
   - **Assert:** Verify the results

4. **Add docstrings:**
   - Explain what the test verifies
   - Include edge cases or special conditions

5. **Use fixtures:**
   - Leverage existing fixtures in `conftest.py`
   - Add new fixtures for repeated setup

6. **Consider edge cases:**
   - Empty inputs
   - Unicode/special characters
   - Very large inputs
   - Invalid inputs
   - Boundary conditions

## Test Data Guidelines

### Markdown Fixtures

When creating markdown test files:
- Use realistic content
- Include variety of elements
- Test edge cases explicitly
- Keep files focused on specific features
- Use descriptive filenames

### Config Fixtures

When creating config test files:
- Test both valid and invalid configs
- Cover all validation rules
- Use clear error cases
- Include minimal valid examples

## Continuous Integration

Tests are designed to run in CI environments:
- No external dependencies (beyond pip packages)
- Use temporary directories (`tmp_path`)
- Deterministic (no random behavior)
- Fast execution (< 30 seconds for full suite)
- Clear failure messages

## Debugging Failed Tests

### View Test Output

```bash
pytest tests/ -v -s
```

The `-s` flag shows print statements.

### Run Single Failing Test

```bash
pytest tests/test_parser.py::test_specific_failing_test -v
```

### Use Debugger

```bash
pytest tests/ --pdb
```

Drops into debugger on first failure.

### Check Coverage for Specific File

```bash
pytest --cov=rock_paper_sync.parser --cov-report=term-missing tests/test_parser.py
```

Shows which lines aren't covered.

## Future Test Additions

When implementing new components, add tests for:

### generator.py
- Text item positioning
- Page generation
- reMarkable file format
- Formatting application
- Multi-page documents

### metadata.py
- Metadata file generation
- Folder metadata
- Timestamp handling
- JSON structure validation

### converter.py
- Full pipeline orchestration
- Error handling
- Folder hierarchy creation
- Incremental sync logic

### watcher.py
- File system event detection
- Debounce logic
- Thread safety
- Graceful shutdown

### cli.py
- Command parsing
- Error messages
- Output formatting
- Signal handling

## Test Maintenance

### Regular Tasks

1. **Run full test suite** before committing changes
2. **Check coverage** to identify gaps
3. **Update fixtures** as features evolve
4. **Review skipped tests** periodically
5. **Refactor** tests that become too complex

### Signs Tests Need Updating

- Failing tests after valid code changes
- Low coverage in new areas
- Slow test execution times
- Duplicate test logic
- Unclear test failures

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-cov documentation](https://pytest-cov.readthedocs.io/)
- [Testing Best Practices](https://docs.python-guide.org/writing/tests/)
- Project architecture: `docs/ARCHITECTURE.md`
- Implementation tasks: `docs/TASKS.md`
