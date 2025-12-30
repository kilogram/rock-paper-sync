# Technical Debt - Deferred Items

This file tracks architectural improvements and code quality issues identified during the major overhaul review that are deferred for future work.

---

## SyncEngine Refactoring (CRITICAL - GOD OBJECT)

**File:** `converter.py` (1577 lines)

**Problem:** Single SyncEngine class with 11+ responsibilities:
1. File discovery
2. Change detection
3. Folder hierarchy management
4. Document generation
5. Cloud upload
6. State updates
7. Annotation downloading
8. Annotation marker updates
9. OCR correction detection
10. Retry logic
11. Vault/file deletion

**Architecture Review Findings:**
The god object of the codebase. While well-documented, it's hard to test individual pieces. The `sync_file()` method alone is 333 lines.

**Proposed Split:**
```
converter.py ->
  ├── sync_orchestrator.py (main SyncEngine class, ~300 lines)
  ├── folder_sync.py (folder hierarchy, ~150 lines)
  ├── file_sync.py (single file sync, ~400 lines)
  ├── annotation_sync.py (downloading/markers, ~300 lines)
  └── vault_operations.py (unsync, delete, ~200 lines)
```

**Minimum Refactoring:**
- Extract `sync_file()` into smaller methods
- Extract annotation-related logic into `AnnotationSyncHelper` class
- Fix leaky `VirtualDeviceState` abstraction in public API

**Lines Saved:** 0 (restructure, not reduction)
**Risk:** Medium (large refactor, needs careful coordination)
**Value:** High (improves testability, reduces cognitive load, enables parallel testing)

---

## OCR Submodule Modernization

**Directory:** `ocr/`

- Extract base class for `LocalOCRService`/`RunpodsOCRService` (80 lines duplication)
- Consolidate color mapping constants (duplicated in `integration.py`)
- Extract paragraph tracking into shared abstraction (fragile state machines in `markers.py`)
- Standardize error handling across providers

---

## Layout Module Cleanup

**Directory:** `layout/`

- ~~Resolve `text_width` naming confusion~~ (DONE - already well-documented in code)
- ~~Fix `PAPER_PRO` vs `PAPER_PRO_MOVE` naming inconsistency~~ (DONE)
- Simplify factory method parameter precedence in `LayoutContext`

---

## Code Quality

- ~~Replace bare `except Exception` with specific exceptions~~ (DONE)
  - ~~`layout/context.py:399`~~
  - ~~`layout/engine.py:94`~~
- ~~Remove debug print statements~~ (DONE - removed in relocate() refactoring)
- ~~Centralize `END_OF_DOC_ANCHOR_MARKER` constant~~ (DONE - uses coordinate_transformer.py)
- Centralize CRDT format tags (0x7F, 0x8F in generator.py)

---

## Coordinate Transformation

**Files:** `coordinate_transformer.py`, `generator.py`

- Unify coordinate transformation strategies
- Currently 3 different approaches in codebase:
  1. Per-parent anchor resolution (`coordinate_transformer.py`)
  2. Simple projection fallback (`generator.py`)
  3. Delta-based with font metrics (`generator.py`)

---

## Test Coverage Gaps

Priority modules needing improved coverage:

| Module | Coverage | Key Gaps |
|--------|----------|----------|
| `cli.py` | 52.69% | OCR commands untested |
| `generator.py` | 53.35% | Annotation injection |
| `coordinate_transformer.py` | 50.20% | Resolver methods |
| `highlight_handler.py` | 63.35% | relocate() edge cases |
| `scene_adapter/translator.py` | 55.06% | Extract/inject methods |

---

## Generator Refactoring

**File:** `generator.py` (1257 lines)

Future extraction candidates:
- `PageLayoutEngine` - Text positioning and pagination
- `AnnotationMigrator` - Annotation extraction and adjustment
- `RmBinaryGenerator` - Binary .rm file generation

Note: CRDT handling was extracted to `annotations/services/crdt_format.py` module

---

## State Manager

**File:** `state.py`

- Extract business logic to `ChangeDetector` class
- ~~Remove unused `reset()` method or wire to CLI for disaster recovery~~ (DONE - wired to CLI)

---

## Extract RmFileExtractor

**Files:** `generator.py`, `document_model.py`, `converter.py`, `layout/context.py`

Consolidate .rm file reading logic scattered across modules:
- `generator.py:_extract_text_blocks_from_rm()`
- `document_model.py:from_rm_files()`
- `converter.py:_build_annotation_map()` setup
- `layout/context.py:from_rm_file()`

Proposed interface:
```python
class RmFileExtractor:
    def extract_text_blocks(self, rm_path: Path) -> list[RmTextBlock]
    def extract_annotations(self, rm_path: Path) -> list[DocumentAnnotation]
    def extract_scene_blocks(self, rm_path: Path) -> list[Block]
```

---

## Extract AnnotationStore from DocumentModel

**File:** `annotations/document_model.py`

DocumentModel is currently ~1200 lines with mixed responsibilities:
- Paragraph/content storage (keep)
- `project_to_pages()` (keep)
- `DocumentAnnotation` storage and retrieval (extract)
- `_assign_stroke_clusters()` logic (extract)
- `get_annotation_clusters()` (extract)

Proposed extraction to `annotations/annotation_store.py`.

---

## Standardize annotations/ Submodule Structure

**Current issues:**
- Root-level files mixed with submodules
- Inconsistent organization

**Target structure:**
```
annotations/
├── __init__.py           # Public API exports
├── core/
│   ├── types.py          # Consolidated types
│   ├── protocol.py       # AnnotationHandler (cleaned)
│   └── processor.py      # AnnotationProcessor
├── model/
│   ├── document.py       # DocumentModel (slimmed)
│   └── store.py          # AnnotationStore (extracted)
├── handlers/
│   ├── highlight.py      # HighlightHandler
│   └── stroke.py         # StrokeHandler
├── services/
│   ├── crdt_service.py
│   ├── context_resolver.py
│   └── merger.py         # Renamed from merging.py
└── scene_adapter/        # Keep as-is
```

Per plan: Do all at once in a single commit to avoid broken imports.

---

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
