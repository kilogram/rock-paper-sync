# Technical Debt - Deferred Items

This file tracks architectural improvements and code quality issues identified during the major overhaul review that are deferred for future work.

---

# Module Refactorings

## Generator Refactoring

**File:** `generator.py` (1257 lines)

The three proposed extraction candidates (`PageLayoutEngine`, `AnnotationMigrator`,
`RmBinaryGenerator`) are largely **already complete** — this note was stale:

- `AnnotationMigrator` → `annotations/services/merger.py` (`AnnotationMerger`, `MergeContext`)
- `RmBinaryGenerator` → `annotations/scene_adapter/executor.py` (`PageTransformExecutor`)
- `PageLayoutEngine` → `layout/engine.py`, `layout/paginator.py`, `layout/context.py`

**What actually remains in `generator.py` that doesn't belong:**

1. `_apply_annotations_to_page` (lines 359–546) — dispatches to handlers and builds
   `PageAnnotationContext`. This is the bridge between the old RemarkablePage model and
   the new executor-based model. Should be replaced by a new `page_planner.py` service
   that builds `PageTransformPlan` directly from `PageProjection + DocumentModel`.

2. `_build_transform_plan` (lines 770–862) — converts `PageAnnotationContext` into
   `PageTransformPlan`. Exists only because `_apply_annotations_to_page` populates the
   context first. Both functions disappear together.

3. `PageAnnotationContext` — a shared mutable state object that exists only because
   the generator straddles two abstraction layers. Delete with the above two.

4. `_extract_text_blocks_from_rm` (line 548) — duplicates `RmFileExtractor`. Used only
   by `_apply_annotations_to_page`.

5. `exclude_ids` / `exclude_tree_node_ids` logic (lines 491–540) — dead weight from
   before the executor's "regenerate fresh, preserve unknown" model was finalized.

**Why this must wait until M5.5:** The generator decomposition requires the layer-aware
`PageTransformPlan` (see pre-M5.5 work below) to be in place first. Refactoring before
the layer model means redoing the work for M7.

**Target state:** `generator.py` shrinks to ~200 lines of orchestration. The new
`annotations/services/page_planner.py` takes `PageProjection + DocumentModel` and builds
`PageTransformPlan` directly, with no intermediate `RemarkablePage.annotation_context`.

---

## ~~Extract RmFileExtractor~~ ✅ DONE

**Status:** Completed 2026-01-02

---

## ~~State Manager Refactoring~~ ✅ DONE

**Status:** Completed 2026-01-02

---

## ~~OCR Base Class~~ ✅ DONE

**Status:** `BaseOCRService` already exists at `src/rock_paper_sync/ocr/base.py`.
`LocalOCRService` and `RunpodsOCRService` are thin subclasses. The TODO was stale.

**Real OCR debt (pre-M8 work):**

1. `integration.py` is ~500 lines doing six things: orchestration, image rendering (PIL),
   color mapping, request building, result handling, state persistence. Extract
   `AnnotationImageRenderer` (or similar) and centralise the two inline color-map dicts
   (lines ~420 and ~456).

2. `markers.py` paragraph tracking walks rendered markdown by line with regex rather than
   using the parser's structural `ContentBlock` output. This will silently break when
   paragraph spacing or any other structural markdown change ships. Fix: integrate marker
   insertion with the parser/`ContentBlock` flow instead of walking raw lines.

3. Provider abstraction is at the right level — both providers differ only in transport
   (sync HTTP vs async polling). No further abstraction needed.

---

## DocumentModel.from_rm_files Extraction

**File:** `src/rock_paper_sync/annotations/document_model.py` (`from_rm_files`, lines 941–1239)

This 290-line method bypasses `RmFileExtractor` and does raw rmscene block introspection
(`type(block).__name__ == "Line..."`) inside what should be a domain layer. It bundles:
extraction → anchoring → clustering into one large procedure.

**Target shape:**
```
RmFileReader / Extractor → raw blocks + page_text
    → AnnotationExtractor → [DocumentAnnotation]  (with anchor_context)
        → DocumentModel(paragraphs, full_text, annotations, ...)
```

**Defer until M7** — M7 will change per-layer extraction, so refactoring now means doing
it twice.

---

# Pre-M5.5 Work (Layer Model Foundation)

**Status:** ✅ Done (this session) — `LayerType`, `LayerPlan` added to `intents.py`;
executor migrated to iterate over layers; single-element list for current content layer.

**What was done:**

- Added `LayerType` enum: `CONTENT`, `ANNOTATIONS`, `OCR_ORIGINAL`, `PRESERVATION`, `USER`
- Added `LayerPlan` dataclass: holds `layer_type`, `visible`, `label`, and all placement
  lists (`stroke_placements`, `highlight_placements`, `unknown_blocks`)
- Removed flat placement fields from `PageTransformPlan`; replaced with
  `layers: list[LayerPlan]`
- Updated `executor.execute()` to iterate `plan.layers` for placements
- Updated `generator._build_transform_plan()` to wrap content in a single
  `LayerPlan(layer_type=CONTENT, visible=True, label="Layer 1")`

**Why this was needed before M5.5:** `PageTransformPlan` was single-layer by construction
(`CrdtId(0, 11)` hard-coded in executor). M5.5 needs to emit a second hidden layer for
orphan preservation. Without the layer model in place first, M5.5 would have to refactor
the plan and executor simultaneously — higher risk. With it in place, M5.5 just adds a
`LayerPlan(layer_type=PRESERVATION, visible=False, ...)` to the list.

---

# M5.5 Work (Orphan Layer Management)

**Goal:** Preserve orphaned annotations in a hidden `.rm` layer, so they survive syncs
and can be recovered if the anchor text reappears.

## 1. Executor: multi-layer output

The executor's `_regenerate_structural()` currently hard-codes a single layer
(`CrdtId(0, 11)`, label "Layer 1"). For M5.5, it must iterate over `plan.layers` and
emit a `SceneTreeBlock` + `TreeNodeBlock` + `SceneGroupItemBlock` triple for each layer,
using distinct CrdtIds per layer.

**CrdtId allocation scheme (proposed):**
```
Root node:            CrdtId(0, 1)   — unchanged, always the scene root
Layer N tree_id:      CrdtId(0, 10 + N*10)   e.g. Layer 1 = (0,11), Layer 2 = (0,21)
Layer N node_id:      same as tree_id
Layer N label ts:     CrdtId(0, 10 + N*10 + 1)
Layer N group link:   CrdtId(0, 10 + N*10 + 2)
```

Layer 1 (content, `CrdtId(0,11)`) keeps its current IDs — no behaviour change for existing
documents. Layer 2 (preservation, `CrdtId(0,21)`) is new.

Visibility: hidden layers must emit a visibility flag in their `SceneTreeBlock`. Verify
the exact rmscene field name — likely `is_visible` or a `visibility` attribute on
`SceneTreeBlock` or its parent `TreeNodeBlock`. Check device-captured `.rm` files for
reference.

## 2. HiddenLayerManager

New class (suggested location: `src/rock_paper_sync/annotations/services/hidden_layer.py`).

**Responsibilities:**
- Accept a list of orphaned annotations from the DB
- Build a `LayerPlan(layer_type=PRESERVATION, visible=False, label="Rock Paper Sync — Orphans")`
  containing `PreserveUnknown` entries for each orphaned block
- Called from `generator.py` during `_build_transform_plan` (or its successor in
  `page_planner.py`) when the document has orphans

**Integration point:** The generator already queries the DB for orphaned annotations via
`PullSyncEngine.attempt_orphan_recovery()`. Pass them into the layer builder.

## 3. Generator: page_planner.py

During M5.5, delete `_apply_annotations_to_page`, `_build_transform_plan`,
`PageAnnotationContext`, and `_extract_text_blocks_from_rm` from `generator.py`. Replace
with `annotations/services/page_planner.py`:

```python
class PagePlanner:
    def build_plan(
        self,
        projection: PageProjection,
        document_model: DocumentModel,
        uuid_to_rm_path: dict[str, Path],
        orphan_blocks: list[Any] | None = None,
    ) -> PageTransformPlan:
        ...
```

The planner returns a multi-layer `PageTransformPlan` (content layer + optional
preservation layer) with no intermediate `PageAnnotationContext`.

## 4. Tests for M5.5

- Unit test for `HiddenLayerManager.build_preservation_layer()`: given orphan blocks,
  returns correct `LayerPlan`
- Unit test for executor multi-layer output: plan with 2 layers produces `.rm` with 2
  `SceneTreeBlock`s and 2 `TreeNodeBlock`s with correct CrdtIds
- Integration test: document with orphan goes through full sync cycle; resulting `.rm`
  has hidden layer; layer survives re-upload; orphan annotation still present after
  second sync
- Verify hidden layer is not visible in xochitl (manual device test)

---

# Code Quality & Cleanup

## Layout Module Cleanup

**Directory:** `layout/`

- Simplify factory method parameter precedence in `LayoutContext`

---

# Feature Additions

## Heading Style Support

**Files:** `generator.py`, `annotations/scene_adapter/executor.py`

**Do NOT add a new field to `ContentBlock`** — `ContentBlock.type` already has `HEADER`.
The bug is in `executor._build_text_styles()` which hardcodes `ParagraphStyle.PLAIN`
at offset 0, ignoring all per-paragraph type information.

**Correct fix:**

1. Add `ParagraphStyleSpan` to `intents.py`:
   ```python
   @dataclass(frozen=True)
   class ParagraphStyleSpan:
       char_offset: int          # offset in page_text where this style starts
       style: si.ParagraphStyle  # PLAIN | HEADING | BULLET | ...
   ```
   Add `paragraph_styles: list[ParagraphStyleSpan]` to `LayerPlan` (not `PageTransformPlan`
   — styles belong to the content layer).

2. In the generator's successor (`page_planner.py`), project `ContentBlock.type` →
   `ParagraphStyleSpan` list during plan construction. Walk `ContentBlock`s in order;
   accumulate char offsets; emit a span for each heading or style change.

3. In `executor._build_text_styles()`, consume the `paragraph_styles` list to build the
   rmscene styles dict with correct per-paragraph styles.

**Scope:** Low risk — additive change. Heading style is the only blocked case today; the
`ParagraphStyleSpan` list naturally extends to bullets, code blocks, etc. without further
schema changes.

## Paragraph Spacing Preservation

**Files:** `parser.py`, `generator.py`

**Do NOT implement the proposed `preceding_blank_lines: int` approach** — adding extra
`\n` characters to `RootTextBlock.text` shifts all annotation character offsets, causing
silent anchor confidence drops and orphans after any spacing change.

**Why it's unsafe:** The annotation anchoring system stores character offsets into
`page_text`. If spacing adds `\n\n` between paragraphs in the text, every anchor after
the first gap is off by `N` characters. The merge engine sees a "different document" and
degrades to fuzzy/diff fallback.

**Correct approaches (both require more work than the original proposal):**

A. **Separator block in the content stream:** Add a `SPACER` block type to `ContentBlock`.
   Pagination accounts for the height. The executor adjusts Y positions for the extra
   vertical space without adding characters to `RootTextBlock.text`. Anchor offsets are
   unaffected.

B. **Consistent whitespace normalisation:** If you must use newline characters for spacing,
   normalise them out everywhere in the anchor pipeline: `_normalize_text()`,
   `context_before`/`context_after` computation, `original_position` calculation, hash
   generation. This is a deep change touching many code paths; validate against all
   existing anchor tests before shipping.

**Defer until the anchor model impact is fully designed.**

---

# Testing

## Test Coverage Gaps

Priority modules needing improved coverage:

| Module | Coverage | Action |
|--------|----------|--------|
| `scene_adapter/translator.py` | 55.06% | **Highest priority.** Layer boundary with pure functions. Add unit tests for: `extract_unknown_blocks`, `build_stroke_bundles`, `get_anchor_offset_from_tree_node`, `is_sentinel_anchor`, `reanchor_bundle`, `prepare_bundle_for_injection`. |
| `cli.py` | 52.69% | Easy wins. Unit test each command function with mocked dependencies — argument parsing, exit codes, error formatting. |
| `highlight_handler.py` | 63.35% | Unit test the pure functions at top of file. Let record-replay cover `relocate()` / `apply_to_page()` integration paths. |
| `generator.py` | 53.35% | **Do not backfill.** Most uncovered paths are in `_apply_annotations_to_page`, `_build_transform_plan`, `_extract_text_blocks_from_rm` — all slated for deletion in M5.5. Adding tests now means deleting them again. |

## Record-Replay Test Infrastructure for Layers (pre-M5.5)

Build before M5.5 ships, not after:

- Framework for asserting layer count in generated `.rm` files
- Assert hidden layer not visible (check visibility flag in parsed blocks)
- Assert preservation layer contains expected block types
- Round-trip: upload multi-layer `.rm`, re-download, verify layer structure intact
