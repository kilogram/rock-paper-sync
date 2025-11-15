# Parser Testing Guide

This document demonstrates how the markdown parser works, shows example outputs, and explains how to verify formatting positions manually.

## Test Coverage

Current test coverage: **93.46%**

- 45 test cases covering all major functionality
- Tests for frontmatter extraction, inline formatting, content blocks, and edge cases
- Character position accuracy tests (critical for formatting preservation)

## Running Tests

```bash
# Run all parser tests
uv run pytest tests/test_parser.py -v

# Run with coverage report
uv run pytest tests/test_parser.py --cov=rock_paper_sync.parser --cov-report=term-missing

# Run specific test class
uv run pytest tests/test_parser.py::TestInlineFormatting -v

# Run specific test
uv run pytest tests/test_parser.py::TestFormattingPositionAccuracy::test_bold_position_exact -v
```

## Test Fixtures

The following test fixtures are available in `tests/fixtures/sample_markdown/`:

### 1. simple.md
Simple paragraphs with no special formatting.

**Purpose**: Test basic paragraph parsing.

### 2. headers.md
All header levels (H1-H6).

**Purpose**: Verify header level detection and text extraction.

### 3. formatting.md
Various inline formatting examples:
- Bold text (`**bold**`)
- Italic text (`*italic*`)
- Bold and italic (`***both***`)
- Inline code (`` `code` ``)
- Links (`[text](url)`)
- Strikethrough (`~~text~~`)

**Purpose**: Test inline formatting detection and character position accuracy.

### 4. lists.md
- Bullet lists
- Numbered lists
- Nested lists (up to 3 levels)
- Mixed lists

**Purpose**: Verify list item detection and nesting level tracking.

### 5. frontmatter.md
Document with YAML frontmatter containing title, author, tags, and date.

**Purpose**: Test frontmatter extraction and body separation.

### 6. code_blocks.md
- Fenced code blocks with language specification
- Fenced code blocks without language
- Inline code

**Purpose**: Verify code block handling and language tag preservation.

### 7. complex.md
Comprehensive document combining all markdown elements.

**Purpose**: Integration testing of full parser pipeline.

### 8. edge_cases.md
Edge cases including:
- Multiple blank lines
- Unicode characters (café, 你好, emoji)
- Special HTML characters (`<>&"'`)
- Escaped markdown (`\*not italic\*`)
- Trailing whitespace

**Purpose**: Test robustness and Unicode handling.

### 9. empty.md
Completely empty file.

**Purpose**: Ensure parser doesn't crash on empty input.

### 10. only_whitespace.md
File with only whitespace.

**Purpose**: Verify proper handling of whitespace-only content.

## Example Parse Results

### Simple Example

**Input** (simple.md):
```markdown
This is a simple paragraph with no special formatting.

Here is another paragraph. It has two sentences.
```

**Output**:
```
Title: simple
Frontmatter: {}
Total Blocks: 2

[1] PARAGRAPH (level=0)
    Text: This is a simple paragraph with no special formatting.
    Formatting: 0 range(s)

[2] PARAGRAPH (level=0)
    Text: Here is another paragraph. It has two sentences.
    Formatting: 0 range(s)
```

### Formatting Example

**Input**:
```markdown
This is **bold** and *italic* text.
```

**Output**:
```
[1] PARAGRAPH (level=0)
    Text: This is bold and italic text.
    Formatting: 2 range(s)
      - bold: [8:12] = "bold"
      - italic: [17:23] = "italic"
```

### Complex Document Example

**Input** (complex.md):
```markdown
---
title: Complex Document
tags:
  - comprehensive
  - test
---

# Complex Markdown Document

This document combines **all** markdown elements to test the *full* parser.

## Inline Formatting

Here we have **bold**, *italic*, `code`, and ***bold italic*** text.

[... continues with lists, code blocks, etc ...]
```

**Output**: 31 blocks including:
- Headers (6 blocks)
- Paragraphs with formatting (10 blocks)
- List items with nesting (10 blocks)
- Code blocks (1 block)
- Blockquotes (1 block)
- Horizontal rules (1 block)

See full output by running:
```bash
uv run python -c "
from pathlib import Path
from rock_paper_sync.parser import parse_markdown_file

fixtures_dir = Path('tests/fixtures/sample_markdown')
doc = parse_markdown_file(fixtures_dir / 'complex.md')

for i, block in enumerate(doc.content, 1):
    print(f'[{i}] {block.type.value}: {block.text[:50]}...')
    for fmt in block.formatting:
        text_slice = block.text[fmt.start:fmt.end]
        print(f'  {fmt.style.value}: [{fmt.start}:{fmt.end}] = \"{text_slice}\"')
"
```

## Verifying Formatting Positions Manually

Formatting positions are **character offsets** in the plain text (with formatting markers removed).

### Example 1: Simple Bold

**Markdown**: `This is **bold** text.`
**Plain text**: `This is bold text.`

Positions:
- Start: 8 (index of 'b' in "bold")
- End: 12 (index after 'd' in "bold")

**Verification**:
```python
text = "This is bold text."
print(text[8:12])  # Output: "bold"
```

### Example 2: Multiple Formats

**Markdown**: `**bold** and *italic*`
**Plain text**: `bold and italic`

Positions:
- Bold: [0:4] = "bold"
- Italic: [9:15] = "italic"

**Verification**:
```python
text = "bold and italic"
print(text[0:4])    # Output: "bold"
print(text[9:15])   # Output: "italic"
```

### Example 3: Nested Formatting

**Markdown**: `***bold and italic***`
**Plain text**: `bold and italic`

Positions:
- Bold: [0:15]
- Italic: [0:15]

Both formats cover the same text range.

### Using the Visualization Helper

The parser includes a `visualize_formatting()` function for debugging:

```python
from rock_paper_sync.parser import visualize_formatting, TextFormat, FormatStyle

text = "This is bold and italic text"
formatting = [
    TextFormat(8, 12, FormatStyle.BOLD),
    TextFormat(17, 23, FormatStyle.ITALIC),
]

print(visualize_formatting(text, formatting))
```

Output:
```
This is bold and italic text
        ^^^^         ^^^^^^
        BOLD         ITALIC
```

## Critical Test Cases

### Character Position Accuracy

The most critical tests verify exact character positions:

```python
def test_bold_position_exact():
    markdown = "Start **bold** end"
    blocks = parse_content(markdown)

    para = blocks[0]
    assert para.text == "Start bold end"

    bold_fmt = next(f for f in para.formatting if f.style == FormatStyle.BOLD)
    bold_text = para.text[bold_fmt.start:bold_fmt.end]

    assert bold_text == "bold"  # MUST be exact
```

### Nested Formatting

```python
def test_nested_bold_in_italic():
    markdown = "*Italic with **bold** inside*"
    blocks = parse_content(markdown)

    # Should have both italic (entire text) and bold (subset)
    # Positions must be accurate for both overlapping ranges
```

### Link Conversion

```python
def test_link_conversion():
    markdown = "Visit [this site](https://example.com) now."
    blocks = parse_content(markdown)

    # Plain text should be: "Visit this site (https://example.com) now."
    # Link formatting should cover the entire transformed text including URL
```

## Edge Cases Handled

### 1. Empty Input
```python
parse_content("")  # Returns: []
parse_content("   \n\n   ")  # Returns: []
```

### 2. Malformed Markdown
```python
# Parser should not crash, returns best-effort parse
parse_content("###\n\n**\n\n`unclosed code")
```

### 3. Unicode Content
```python
# All unicode preserved correctly
parse_content("# 你好\n\nこんにちは café 🎉")
```

### 4. Very Long Lines
```python
# No truncation, entire text preserved
long_text = "A" * 10000
parse_content(f"# Header\n\n{long_text}")
```

### 5. Invalid YAML Frontmatter
```python
# Returns empty dict, logs warning, continues parsing body
content = """---
invalid: [unclosed
---
# Content"""
frontmatter, body = extract_frontmatter(content)
# frontmatter == {}
# body starts with "# Content"
```

## Known Limitations

### 1. Mistune Compatibility
The parser relies on mistune's AST structure. If mistune changes its AST format in future versions, the parser may need updates.

### 2. Markdown Flavor
The parser supports CommonMark syntax via mistune. Some GitHub Flavored Markdown or other extensions may not be fully supported.

### 3. Image Handling
Images are converted to placeholders `[Image: alt_text]`. The actual image data is not preserved (by design for reMarkable compatibility).

### 4. HTML in Markdown
Raw HTML in markdown is stripped during parsing. Only plain text content is extracted.

### 5. Line Break Handling
Line breaks (soft breaks and hard breaks) are currently converted to spaces. This may need refinement for specific use cases.

## Future Testing Improvements

### Planned Additions
1. Performance benchmarks for large documents
2. Memory usage tests for very large vaults
3. Concurrent parsing stress tests
4. Round-trip testing (parse → convert → verify)

### Coverage Goals
- Current: 93.46%
- Target: 95%+
- Missing coverage: Some error handling branches and edge cases

## Debugging Failed Tests

### If formatting position tests fail:

1. **Check the plain text**:
   ```python
   print(f"Expected: '{expected_text}'")
   print(f"Actual: '{block.text}'")
   ```

2. **Visualize formatting**:
   ```python
   print(visualize_formatting(block.text, block.formatting))
   ```

3. **Inspect AST**:
   ```python
   import mistune, json
   md = mistune.create_markdown(renderer='ast')
   print(json.dumps(md(markdown_text), indent=2))
   ```

### If list parsing fails:

Check if mistune is using `block_text` vs `paragraph` for list items. The parser handles both, but the AST structure varies by mistune version.

### If frontmatter extraction fails:

Verify YAML syntax is valid:
```python
import yaml
yaml.safe_load(yaml_content)  # Check for errors
```

## Contributing Tests

When adding new tests:

1. **Create a minimal fixture** that demonstrates the issue
2. **Write a focused test** that verifies one specific behavior
3. **Include verification comments** showing expected character positions
4. **Add edge cases** to stress test the implementation
5. **Update this document** with new example outputs

### Test Naming Convention
- `test_<feature>_<scenario>` for specific features
- `test_<fixture>_fixture` for fixture-based tests
- `test_<edge_case>` for edge case handling

## Test Execution Time

All tests should complete quickly:
- Individual tests: < 10ms
- Full test suite: < 1 second
- No network I/O or external dependencies

Current timing: **~0.1 seconds** for all 45 tests.
