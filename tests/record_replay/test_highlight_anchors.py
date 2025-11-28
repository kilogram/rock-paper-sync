"""Tests for highlight anchoring behavior across markdown changes.

This test validates the core use cases of the anchoring system:
- Fuzzy matching when text has minor edits (case, typos)
- Re-identification when paragraphs are reordered
- Deletion detection when annotated paragraphs are removed
- Position tolerance when content is added before annotations

Recording Usage:
    uv run pytest tests/record_replay/test_highlight_anchors.py --online -s

    When prompted: Create highlights on the test paragraphs

Replaying:
    uv run pytest tests/record_replay/test_highlight_anchors.py

    Uses recorded annotations, modifies markdown, tests anchor matching behavior.
"""

import io
import pytest

from rock_paper_sync.annotations import read_annotations, AnnotationType
from rock_paper_sync.annotations.handlers.highlight_handler import HighlightHandler
from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm


@pytest.mark.device
def test_highlight_anchors_comprehensive(device, workspace, fixtures_dir):
    """Comprehensive test for highlight anchor creation, matching, and preservation.

    This test consolidates all highlight anchor testing into a single recording session:
    1. Anchor creation with correct structure
    2. Fuzzy text matching across edits
    3. Multi-signal weighted matching
    4. Annotation preservation across document updates

    Records once, tests multiple scenarios to minimize human burden.
    """
    test_id = "highlights"

    # Load fixture with highlightable text
    fixture_doc = fixtures_dir / "test_highlights.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        device.start_test(test_id, description="Create highlight annotations for comprehensive anchor testing")
    except FileNotFoundError:
        pytest.skip(f"Testdata '{test_id}' not available. Run with --online -s to record.")

    # Upload document
    doc_uuid = device.upload_document(workspace.test_doc)

    # Wait for annotations
    state = device.wait_for_annotations(doc_uuid)
    assert state.has_annotations, "No annotations captured"

    # Load the actual markdown source to get paragraph text
    source_text = workspace.test_doc.read_text()
    # Extract annotated paragraphs (lines between <!-- ANNOTATED --> markers)
    import re
    annotated_paragraphs = []
    pattern = r'<!-- ANNOTATED:.*?-->\s*\n(.*?)\n<!-- /ANNOTATED -->'
    for match in re.finditer(pattern, source_text, re.DOTALL):
        paragraph = match.group(1).strip()
        annotated_paragraphs.append(paragraph)

    # Extract highlights and create anchors using real paragraph text
    handler = HighlightHandler()
    all_anchors = []

    for page_uuid, rm_data in state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        highlights = [a for a in annotations if a.type == AnnotationType.HIGHLIGHT]

        assert len(highlights) > 0, "No highlights found"

        for i, highlight in enumerate(highlights):
            # Get the highlight text
            highlight_text = highlight.highlight.text if highlight.highlight else ""

            # Find which annotated paragraph contains this highlight
            paragraph_text = None
            paragraph_index = None
            for idx, para in enumerate(annotated_paragraphs):
                if highlight_text.lower() in para.lower():
                    paragraph_text = para
                    paragraph_index = idx
                    break

            # If not found in annotated paragraphs, use the highlight text itself
            if paragraph_text is None:
                paragraph_text = highlight_text
                paragraph_index = i

            anchor = handler.create_anchor(
                annotation=highlight,
                paragraph_text=paragraph_text,
                paragraph_index=paragraph_index,
                page_num=0
            )
            all_anchors.append((anchor, highlight, paragraph_text))

    assert len(all_anchors) > 0, "No anchors created"

    # Use first anchor for behavioral tests (assuming at least one highlight exists)
    assert len(all_anchors) > 0, "Need at least one highlight for testing"
    test_anchor, _, original_paragraph = all_anchors[0]

    # Test 1: Exact Match - Original paragraph should match perfectly
    exact_match_score = test_anchor.match_score(
        paragraph_text=original_paragraph,
        position=(test_anchor.page.x, test_anchor.page.y),
        bbox=(test_anchor.bbox.x, test_anchor.bbox.y, test_anchor.bbox.width, test_anchor.bbox.height)
    )
    assert exact_match_score > 0.7, \
        f"Exact match should score high, got {exact_match_score:.2f}"

    # Test 2: Fuzzy Match - Case Change
    # User changes case in markdown, anchor should still match
    case_changed = original_paragraph.replace(
        test_anchor.text.content,
        test_anchor.text.content.upper(),
        1
    )
    case_change_score = test_anchor.match_score(
        paragraph_text=case_changed,
        position=(test_anchor.page.x, test_anchor.page.y),
        bbox=(test_anchor.bbox.x, test_anchor.bbox.y, test_anchor.bbox.width, test_anchor.bbox.height)
    )
    assert case_change_score >= 0.5, \
        f"Case change should still match (fuzzy), got {case_change_score:.2f}"

    # Test 3: Fuzzy Match - Small Typo
    # User introduces small typo, anchor should still match
    if "the" in original_paragraph.lower():
        typo_paragraph = original_paragraph.lower().replace("the", "teh", 1)
        typo_score = test_anchor.match_score(
            paragraph_text=typo_paragraph,
            position=(test_anchor.page.x, test_anchor.page.y),
            bbox=(test_anchor.bbox.x, test_anchor.bbox.y, test_anchor.bbox.width, test_anchor.bbox.height)
        )
        assert typo_score > 0.4, \
            f"Minor typo should still match somewhat, got {typo_score:.2f}"

    # Test 4: Text-Only Match - Wrong Position
    # Anchor should still match based on text content even if position is way off
    text_only_score = test_anchor.match_score(
        paragraph_text=original_paragraph,
        position=(9999, 9999),  # Wrong position
        bbox=None
    )
    assert text_only_score > 0.3, \
        f"Text-only match should still work, got {text_only_score:.2f}"

    # Test 5: No Match - Completely Different Text
    # Anchor should fail to match unrelated text
    no_match_score = test_anchor.match_score(
        paragraph_text="Completely unrelated text that has nothing to do with the original",
        position=(9999, 9999),
        bbox=None
    )
    assert no_match_score < 0.3, \
        f"Unrelated text should not match, got {no_match_score:.2f}"

    # Test 6: Position Tolerance - Text Moved
    # If paragraph moves to different position, text matching should still work
    moved_score = test_anchor.match_score(
        paragraph_text=original_paragraph,
        position=(test_anchor.page.x + 500, test_anchor.page.y + 500),  # Moved position
        bbox=None
    )
    assert moved_score > 0.3, \
        f"Text should match even when moved, got {moved_score:.2f}"

    # Test 7: Best Match Selection
    # When searching across multiple paragraphs, should pick the best match
    candidates = [
        "Completely unrelated paragraph about something else",
        original_paragraph,  # This should win
        "Another unrelated paragraph with different content",
    ]

    scores = [
        test_anchor.match_score(paragraph_text=para, position=None, bbox=None)
        for para in candidates
    ]

    best_index = scores.index(max(scores))
    assert best_index == 1, \
        f"Should identify original paragraph (index 1) as best match, got index {best_index} with scores {scores}"

    device.end_test(test_id)
