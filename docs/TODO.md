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

- Resolve `text_width` naming confusion (750px vs 758px mean different things)
- Fix `PAPER_PRO` vs `PAPER_PRO_MOVE` naming inconsistency
- Simplify factory method parameter precedence in `LayoutContext`

---

## Code Quality

- Replace bare `except Exception` with specific exceptions
  - `layout/context.py:399`
  - `layout/engine.py:94`
- ~~Remove debug print statements~~ (DONE - removed in relocate() refactoring)
- Centralize magic constants
  - `END_OF_DOC_SENTINEL = 281474976710655` (used in document_model.py)
  - CRDT format tags (0x7F, 0x8F in generator.py)

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

**File:** `generator.py` (2025 lines)

Future extraction candidates:
- `CrdtFormatter` - CRDT encoding/decoding (lines 59-215)
- `PageLayoutEngine` - Text positioning and pagination
- `AnnotationMigrator` - Annotation extraction and adjustment
- `RmBinaryGenerator` - Binary .rm file generation

Dead code to remove:
- `_match_rm_files_to_pages()` (90 lines, never called)

---

## State Manager

**File:** `state.py`

- Extract business logic to `ChangeDetector` class
- Remove unused `reset()` method or wire to CLI for disaster recovery
