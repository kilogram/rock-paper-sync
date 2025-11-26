# Annotation Test Data

This directory contains test data for annotation feature testing.

## Directory Structure

```
testdata/
├── rmscene/                          # Individual .rm file samples
│   ├── Bold_Heading_Bullet_Normal.rm     # Document with bold/heading/bullet formatting
│   ├── Normal_A_stroke_2_layers.rm       # Single character "A" with 2 hand-drawn strokes
│   └── Wikipedia_highlighted_p1.rm       # Wikipedia excerpt with highlights
│
└── real_world_annotation_test/       # Multi-stage annotation preservation test
    ├── stage1_initial/               # Initial clean document (no annotations)
    │   ├── test1.md                      # Original markdown source
    │   ├── doc_uuid.txt                  # Document UUID (reMarkable identifier)
    │   ├── page_uuids.txt                # List of page UUIDs
    │   ├── 3a03a425-*.rm                 # Generated .rm files
    │   ├── 74035018-*.rm
    │   └── e605d362-*.rm
    │
    ├── stage2_annotated/             # After user annotates with pen/highlight
    │   ├── doc_uuid.txt
    │   ├── page_uuids.txt
    │   ├── 3a03a425-*.rm
    │   ├── 74035018-*.rm
    │   └── e605d362-*.rm
    │
    └── stage3_modified/              # After user modifies markdown + regenerates
        ├── test1_modified.md             # Modified markdown source
        ├── doc_uuid.txt
        ├── page_uuids.txt
        ├── 3a03a425-*.rm
        ├── 74035018-*.rm
        └── e605d362-*.rm
```

## Test Data Descriptions

### rmscene/ - Unit Test Files

Individual .rm files used for focused unit testing of annotation reading and parsing:

- **Bold_Heading_Bullet_Normal.rm**: Contains text with various formatting styles (bold, headings, bullet points). Useful for testing annotation extraction from formatted documents.

- **Normal_A_stroke_2_layers.rm**: Simple document containing the letter "A" with exactly 2 hand-drawn strokes. Used to test:
  - Stroke reading and parsing
  - Multiple stroke extraction
  - Stroke properties (color, tool, points)

- **Wikipedia_highlighted_p1.rm**: Wikipedia page excerpt with text highlights. Used to test:
  - Highlight reading from real-world documents
  - Multiple highlights
  - Highlight rectangles and text content

### real_world_annotation_test/ - Integration Test Suite

Multi-stage test data tracking annotation preservation through a complete workflow:

**Stage 1 (Initial)**: Clean document with no user annotations
- Tests baseline: no annotations should be found
- Data represents initial state after markdown → .rm conversion

**Stage 2 (Annotated)**: Same document after user has made annotations on device
- User manually adds pen strokes and highlights on reMarkable
- Tests annotation extraction from real documents
- Validates annotation types and counts

**Stage 3 (Modified)**: After markdown was edited and document regenerated
- Tests that annotations are preserved when document structure changes
- Validates annotation anchoring still works with modified content
- Ensures stroke/highlight details remain intact

## Usage in Tests

### For Unit Tests (rmscene/)

```python
from pathlib import Path
TESTDATA_DIR = Path(__file__).parent / "testdata" / "rmscene"
file_path = TESTDATA_DIR / "Normal_A_stroke_2_layers.rm"
annotations = read_annotations(file_path)
```

### For Integration Tests (real_world_annotation_test/)

```python
from pathlib import Path
TESTDATA_DIR = Path(__file__).parent / "testdata" / "real_world_annotation_test"

stage1_dir = TESTDATA_DIR / "stage1_initial"
stage2_dir = TESTDATA_DIR / "stage2_annotated"
stage3_dir = TESTDATA_DIR / "stage3_modified"
```

## Notes

- All `.rm` files are binary reMarkable document format (v6)
- `.md` files contain markdown source
- `doc_uuid.txt` contains a single-line UUID
- `page_uuids.txt` contains one UUID per line, one for each page
- Files are small (~10-20KB) to keep repository size reasonable

## Data Origin

The **rmscene/** test files are sourced from the [rmscene library repository](https://github.com/ricklupton/rmscene/tree/main/tests/data):
- Bold_Heading_Bullet_Normal.rm
- Normal_A_stroke_2_layers.rm
- Wikipedia_highlighted_p1.rm

These files are checked into this repository for:
- **Self-contained tests**: Tests don't require external dependencies to be cloned
- **Stability**: Test data is stable and unlikely to change
- **Simplicity**: Contributors just clone once and everything works

The **real_world_annotation_test/** files are specific to this project's annotation preservation testing workflow.
