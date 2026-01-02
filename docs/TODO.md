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

## ~~Extract RmFileExtractor~~ ✅ DONE

**Status:** Completed 2026-01-02

Created `src/rock_paper_sync/rm_file_extractor.py` consolidating .rm file reading.
Updated callers to delegate:
- `generator.py:_extract_text_blocks_from_rm()` → delegates to RmFileExtractor
- `layout/context.py:from_rm_file()` → delegates to RmFileExtractor
- `coordinates.py:AnchorResolver.from_rm_file()` → delegates to RmFileExtractor

Note: `document_model.py:from_rm_files()` was not migrated as it does additional
annotation-specific processing beyond basic extraction.

---

## ~~State Manager Refactoring~~ ✅ DONE

**Status:** Completed 2026-01-02

Created `src/rock_paper_sync/change_detector.py` extracting change detection logic.
Updated callers to use ChangeDetector:
- `converter.py:sync_vault()` → uses ChangeDetector for find_changed_files/find_deleted_files
- Removed `find_changed_files()`, `find_deleted_files()`, `_is_excluded()` from state.py
- StateManager now focuses on persistence; ChangeDetector handles business logic

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

## ~~Coordinate Round-Trip Tests~~ ✅ DONE

**Status:** Completed 2026-01-02

Created `tests/test_coordinate_round_trip.py` with 31 tests validating:
- DocumentPoint ↔ PageLocalPoint round-trips (page-local storage)
- DocumentPoint ↔ TextRelativePoint round-trips (text-relative storage)
- Character offset ↔ Position round-trips (critical for bidirectional sync)
- Multi-page scenarios and edge cases

---

## Test Coverage Gaps

Priority modules needing improved coverage:

| Module | Coverage | Key Gaps |
|--------|----------|----------|
| `cli.py` | 52.69% | OCR commands untested |
| `generator.py` | 53.35% | Annotation injection |
| `highlight_handler.py` | 63.35% | relocate() edge cases |
| `scene_adapter/translator.py` | 55.06% | Extract/inject methods |
