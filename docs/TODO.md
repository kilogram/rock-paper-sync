# Technical Debt - Deferred Items

This file tracks architectural improvements and code quality issues identified during the major overhaul review that are deferred for future work.

---

# Module Refactorings

## Generator Refactoring

**File:** `generator.py` (1257 lines)

Future extraction candidates:
- `PageLayoutEngine` - Text positioning and pagination
- `AnnotationMigrator` - Annotation extraction and adjustment
- `RmBinaryGenerator` - Binary .rm file generation

Note: CRDT format utilities were extracted to `crdt_format.py` module

---

## Extract RmFileExtractor

**Files:** `generator.py`, `document_model.py`, `annotation_sync_helper.py`, `layout/context.py`

Consolidate .rm file reading logic scattered across modules:
- `generator.py:_extract_text_blocks_from_rm()`
- `document_model.py:from_rm_files()`
- `annotation_sync_helper.py:build_annotation_map()` setup
- `layout/context.py:from_rm_file()`

Proposed interface:
```python
class RmFileExtractor:
    def extract_text_blocks(self, rm_path: Path) -> list[RmTextBlock]
    def extract_annotations(self, rm_path: Path) -> list[DocumentAnnotation]
    def extract_scene_blocks(self, rm_path: Path) -> list[Block]
```

---

## State Manager Refactoring

**File:** `state.py`

- Extract business logic to `ChangeDetector` class

---

## OCR Submodule Modernization

**Directory:** `ocr/`

- Extract base class for `LocalOCRService`/`RunpodsOCRService` (80 lines duplication)
- Consolidate color mapping constants (duplicated in `integration.py`)
- Extract paragraph tracking into shared abstraction (fragile state machines in `markers.py`)
- Standardize error handling across providers

---

# Code Quality & Cleanup

## Layout Module Cleanup

**Directory:** `layout/`

- Simplify factory method parameter precedence in `LayoutContext`

---

## Coordinate Transformation

**Files:** `coordinate_transformer.py`, `generator.py`

- Unify coordinate transformation strategies
- Currently 3 different approaches in codebase:
  1. Per-parent anchor resolution (`coordinate_transformer.py`)
  2. Simple projection fallback (`generator.py`)
  3. Delta-based with font metrics (`generator.py`)

---

# Feature Additions

## Heading Style Support

**Files:** `generator.py`, `annotations/scene_adapter/executor.py`

Generator should set `ParagraphStyle.HEADING` for all markdown headings, not just the first line.

**Current behavior:**
- Generator only tracks whether the first block is a heading
- All paragraphs after the first get `ParagraphStyle.PLAIN`
- Headings mid-document render as body text

**Device-native behavior:**
- Each paragraph has its own style in the `styles` dictionary
- Headings use `ParagraphStyle.HEADING` (value 2)
- Headings render with larger font

**Proposed fix:**
1. Track heading status per paragraph in `ContentBlock` or `TextItem`
2. Generator builds styles dictionary with HEADING for each heading paragraph
3. Renderer already handles HEADING style correctly

Risk: Low - additive change to generator output.

---

## Paragraph Spacing Preservation

**Files:** `parser.py`, `generator.py`

Extra blank lines between paragraphs in markdown are not preserved in generated .rm files.

**Current behavior:**
- Markdown with double blank lines between paragraphs
- Parser outputs separate ContentBlock per paragraph (loses spacing info)
- Generator joins blocks with single `\n`
- Result: all paragraphs have uniform spacing

**Device-native behavior:**
- Extra blank lines render as `\n\n` in RootTextBlock text content
- Creates visual spacing between paragraph groups

**Proposed fix:**
1. Add `preceding_blank_lines: int = 0` field to `ContentBlock`
2. Parser tracks blank line count before each block
3. Generator inserts extra `\n` characters based on count

Risk: Low - additive change to parser output structure.

---

# Testing

## Test Coverage Gaps

Priority modules needing improved coverage:

| Module | Coverage | Key Gaps |
|--------|----------|----------|
| `cli.py` | 52.69% | OCR commands untested |
| `generator.py` | 53.35% | Annotation injection |
| `coordinate_transformer.py` | 50.20% | Resolver methods |
| `highlight_handler.py` | 63.35% | relocate() edge cases |
| `scene_adapter/translator.py` | 55.06% | Extract/inject methods |
