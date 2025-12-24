# Technical Debt - Deferred Items

This file tracks architectural improvements and code quality issues identified during the major overhaul review that are deferred for future work.

---

## SyncEngine Refactoring

**File:** `converter.py`

- Split `sync_file()` (333 lines) into focused methods
- Extract responsibilities into services (currently 11 responsibilities)
- Fix leaky `VirtualDeviceState` abstraction in public API

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

**File:** `generator.py` (~1935 lines after dead code removal)

Future extraction candidates:
- `CrdtFormatter` - CRDT encoding/decoding (lines 59-215)
- `PageLayoutEngine` - Text positioning and pagination
- `AnnotationMigrator` - Annotation extraction and adjustment
- `RmBinaryGenerator` - Binary .rm file generation

~~Dead code to remove:~~
- ~~`_match_rm_files_to_pages()` (90 lines, never called)~~ (DONE)

---

## State Manager

**File:** `state.py`

- Extract business logic to `ChangeDetector` class
- ~~Remove unused `reset()` method or wire to CLI for disaster recovery~~ (DONE - wired to CLI)

---

## Anchor Type Consolidation

**Files:** `annotations/document_model.py`, `annotations/core_types.py`, `annotations/common/anchors.py`

Currently there are redundant anchor types:
- `AnchorContext` in document_model.py - main production anchor for migration
- `AnnotationAnchor` in common/anchors.py - used by handler Protocol
- `TextAnchor` in core_types.py (line 534) - used by HeuristicTextAnchor
- `TextAnchor` in common/anchors.py (line 157) - used by AnnotationAnchor

Proposed consolidation:
- Keep `AnchorContext` as unified anchor (already handles text + spatial)
- Migrate handler `create_anchor()` to return `AnchorContext` instead of `AnnotationAnchor`
- Remove duplicate `TextAnchor` definitions
- Keep `HeuristicTextAnchor` as service class (not a data type)

Risk: High - affects migration pipeline and handler protocol. Requires careful testing.

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
