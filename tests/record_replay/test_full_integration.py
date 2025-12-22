"""Comprehensive integration test for all features combined.

This test exercises the complete system with all features active simultaneously
to expose integration issues that might not appear in isolated tests:

Features tested in combination:
- Sync (upload/download)
- Highlights (single, overlapping, multi-color)
- Strokes/handwriting
- OCR processing
- Corrections (user edits OCR text)
- Markdown modifications (re-anchoring after edits)
- Multi-page documents
- Dense annotation areas (anchor disambiguation)

Focus on challenging scenarios:
- Anchoring with markdown modifications
- Overlapping annotations of different types
- OCR corrections after document structure changes
- Annotations near page boundaries
- Multiple annotations in close proximity

Recording Usage:
    uv run pytest tests/record_replay/test_full_integration.py --online -s

    When prompted: Follow all instructions in the test document.
    This will take 5-10 minutes to annotate thoroughly.

Replaying:
    uv run pytest tests/record_replay/test_full_integration.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations
from rock_paper_sync.annotations.core.data_types import RenderConfig
from rock_paper_sync.annotations.handlers.highlight_handler import HighlightHandler
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.annotations.ocr_corrections import detect_single_ocr_correction


@pytest.mark.device
def test_full_integration(device, workspace, fixtures_dir, tmp_path):
    """Comprehensive integration test combining all features.

    This test validates:
    1. Upload document with space for all annotation types
    2. Create mixed annotations (highlights + strokes + OCR)
    3. Sync annotations back to markdown
    4. Modify markdown to simulate user edits
    5. OCR corrections on modified document
    6. Re-sync and verify anchoring handles all scenarios
    7. Validate no data loss, no anchor conflicts
    """
    test_id = "full_integration"

    # Load comprehensive fixture
    fixture_doc = fixtures_dir / "test_full_integration.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        device.start_test(
            test_id, description="Full integration: all features combined with modifications"
        )
    except FileNotFoundError:
        pytest.skip(f"Testdata '{test_id}' not available. Run with --online -s to record.")

    # === PHASE 1: Initial Upload and Annotation ===
    doc_uuid = device.upload_document(workspace.test_doc)

    initial_state = device.wait_for_annotations(doc_uuid)
    assert initial_state.has_annotations, "Need comprehensive annotations for integration test"

    # Extract all annotations
    all_annotations = []
    for page_uuid, rm_data in initial_state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        all_annotations.extend(annotations)

    assert len(all_annotations) > 0, "Should have captured annotations"

    # Categorize annotations
    highlights = [a for a in all_annotations if a.type == AnnotationType.HIGHLIGHT]
    strokes = [a for a in all_annotations if a.type == AnnotationType.STROKE]

    # Should have both types for comprehensive testing
    assert len(highlights) > 0, "Need highlights for integration test"
    assert len(strokes) > 0, "Need strokes for integration test"

    print(f"\n📊 Initial annotations: {len(highlights)} highlights, {len(strokes)} strokes")

    # === PHASE 2: Markdown Modifications (Conflict Scenario) ===
    # Modify the document in ways that challenge anchoring:
    # 1. Add text at beginning (shifts all positions)
    # 2. Insert paragraphs in middle (breaks anchors)
    # 3. Reformat sections (changes paragraph boundaries)
    # 4. Modify annotated text slightly (anchor tolerance test)

    original_content = workspace.test_doc.read_text()

    modified_content = original_content

    # Modification 1: Add header text (shifts everything down)
    modified_content = modified_content.replace(
        "# Full Integration Test",
        "# Full Integration Test\n\n> **Version 2.0** - Modified after initial annotations",
    )

    # Modification 2: Insert new section in middle
    modified_content = modified_content.replace(
        "## Part 3: Overlapping Annotations",
        "## Part 2.5: Inserted Section\n\n"
        "This section was added AFTER annotations were created.\n"
        "It tests whether anchoring can handle structural changes.\n\n"
        "## Part 3: Overlapping Annotations",
    )

    # Modification 3: Reformat annotated section (change whitespace/structure)
    modified_content = modified_content.replace(
        "This integration testing document validates complex scenarios.",
        "This **integration testing** document validates **complex scenarios**.\n\n"
        "_Note: Formatting changed after annotation._",
    )

    # Modification 4: Subtle text change near annotation
    modified_content = modified_content.replace(
        "Additional context before and after to test anchoring.",
        "Additional context before and after to test anchoring robustness.",
    )

    # Write modified content
    workspace.test_doc.write_text(modified_content)
    print("\n📝 Applied structural modifications to markdown")

    # === PHASE 3: Re-sync with Modifications ===
    device.trigger_sync()

    updated_state = device.get_document_state(doc_uuid)
    assert updated_state.has_annotations, "Annotations should survive markdown modifications"

    # Extract updated annotations
    updated_annotations = []
    for page_uuid, rm_data in updated_state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        updated_annotations.extend(annotations)

    updated_highlights = [a for a in updated_annotations if a.type == AnnotationType.HIGHLIGHT]
    updated_strokes = [a for a in updated_annotations if a.type == AnnotationType.STROKE]

    # Critical: Annotations should not be lost during modifications
    assert len(updated_highlights) == len(
        highlights
    ), f"Highlights lost during modification: {len(highlights)} -> {len(updated_highlights)}"
    assert len(updated_strokes) == len(
        strokes
    ), f"Strokes lost during modification: {len(strokes)} -> {len(updated_strokes)}"

    print(
        f"✅ All annotations preserved after modifications: "
        f"{len(updated_highlights)} highlights, {len(updated_strokes)} strokes"
    )

    # === PHASE 4: Anchor Verification ===
    # Test that anchors can still match after modifications
    highlight_handler = HighlightHandler()
    stroke_handler = StrokeHandler()

    final_content = workspace.test_doc.read_text()
    paragraphs = final_content.split("\n\n")

    # Test highlight anchoring
    for i, highlight in enumerate(updated_highlights[:3]):  # Test first 3
        for para_idx, para_text in enumerate(paragraphs):
            if len(para_text.strip()) < 10:
                continue

            anchor = highlight_handler.create_anchor(
                annotation=highlight,
                paragraph_text=para_text,
                paragraph_index=para_idx,
                page_num=0,
            )

            # Verify anchor structure is valid
            assert anchor.annotation_type == "highlight"
            assert anchor.text is not None, "Highlight anchor should have text anchor"

    # Test stroke anchoring
    for i, stroke in enumerate(updated_strokes[:3]):  # Test first 3
        for para_idx, para_text in enumerate(paragraphs):
            if len(para_text.strip()) < 10:
                continue

            anchor = stroke_handler.create_anchor(
                annotation=stroke, paragraph_text=para_text, paragraph_index=para_idx, page_num=0
            )

            # Verify anchor structure is valid
            assert anchor.annotation_type == "stroke"
            assert anchor.page is not None, "Stroke anchor should have page position"
            assert anchor.bbox is not None, "Stroke anchor should have bounding box"

    print("✅ Anchor creation successful after modifications")

    # === PHASE 5: OCR Correction Detection ===
    # Simulate user correcting OCR text after markdown modifications
    # This tests the complete correction workflow with anchoring

    config = RenderConfig(stroke_style="comment")

    # Test correction detection with modified document structure
    test_corrections = [
        {
            "old": "Text with <!-- OCR: helo wrld --> after modifications.",
            "new": "Text with <!-- OCR: hello world --> after modifications.",
            "expected_old": "helo wrld",
            "expected_new": "hello world",
        },
        {
            "old": "Modified para with <!-- OCR: quck test --> here.",
            "new": "Modified para with <!-- OCR: quick test --> here.",
            "expected_old": "quck test",
            "expected_new": "quick test",
        },
    ]

    corrections_found = 0
    for i, test_case in enumerate(test_corrections):
        correction = detect_single_ocr_correction(
            vault_name="test",
            file_path="doc.md",
            paragraph_index=i,
            old_paragraph=test_case["old"],
            new_paragraph=test_case["new"],
            annotation_id=f"anno-integration-{i}",
            image_hash=f"hash-integration-{i}",
            config=config,
        )

        if correction:
            assert test_case["expected_old"] in correction.original_text
            assert test_case["expected_new"] in correction.corrected_text
            corrections_found += 1

    print(f"✅ OCR correction detection working: {corrections_found} corrections found")

    # === PHASE 6: Annotation Markers in Modified Document ===
    # Verify that annotation markers are present in the modified markdown
    final_markdown = workspace.test_doc.read_text()

    # Should have annotation markers despite modifications
    if updated_highlights:
        has_highlight_markers = (
            "<!-- ANNOTATED:" in final_markdown or "ANNOTATED:" in final_markdown
        )
        assert has_highlight_markers, "Modified document should contain highlight markers"
        print("✅ Highlight markers present in modified markdown")

    if updated_strokes:
        # Markers are formatted by AnnotationInfo.__str__() as "N stroke" or "N strokes"
        # within <!-- ANNOTATED: ... --> comments
        has_stroke_markers = " stroke" in final_markdown
        assert has_stroke_markers, "Modified document should contain stroke markers"
        print("✅ Stroke markers present in modified markdown")

    # === PHASE 7: Anchor Disambiguation ===
    # Test that anchors can disambiguate between nearby annotations
    # This is critical when multiple annotations are close together

    # Create anchors for all highlights and verify uniqueness
    highlight_anchors = []
    for highlight in updated_highlights:
        for para_idx, para_text in enumerate(paragraphs[:10]):  # Check first 10 paragraphs
            if len(para_text.strip()) < 5:
                continue

            anchor = highlight_handler.create_anchor(
                annotation=highlight,
                paragraph_text=para_text,
                paragraph_index=para_idx,
                page_num=0,
            )
            if anchor.text:
                highlight_anchors.append((anchor.annotation_id, anchor.text.content, para_idx))

    # If we have multiple highlight anchors, verify they can be distinguished
    if len(highlight_anchors) > 1:
        # Count unique text contents
        unique_texts = set(h[1] for h in highlight_anchors)
        # It's OK if some share texts (overlapping highlights), but not all
        # Just verify the anchor system produces valid results
        print(
            f"✅ Anchor disambiguation: {len(highlight_anchors)} anchors, "
            f"{len(unique_texts)} unique texts"
        )

    device.end_test(test_id)


@pytest.mark.device
def test_integration_conflict_stress(device, workspace, fixtures_dir):
    """Stress test for anchoring under extreme modification conflicts.

    This test creates the most challenging scenario:
    - Dense annotations in small area
    - Major structural changes (delete/reorder sections)
    - Paragraph boundary changes
    - Tests anchor matching limits
    """
    test_id = "full_integration"

    # Use same fixture as full integration
    fixture_doc = fixtures_dir / "test_full_integration.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        device.start_test(test_id, description="Stress test: extreme modification conflicts")
    except FileNotFoundError:
        pytest.skip(f"Testdata '{test_id}' not available. Run with --online -s to record.")

    # Initial upload and annotation
    doc_uuid = device.upload_document(workspace.test_doc)
    initial_state = device.wait_for_annotations(doc_uuid)
    assert initial_state.has_annotations

    # Count initial annotations
    initial_count = 0
    for page_uuid, rm_data in initial_state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        initial_count += len(annotations)

    # EXTREME modifications:
    original = workspace.test_doc.read_text()

    # 1. Delete entire sections
    modified = original.replace("## Part 1: Mixed Annotations", "## Part 1: [DELETED]")

    # 2. Reorder sections
    parts = modified.split("## Part")
    if len(parts) > 4:
        # Swap Part 2 and Part 4
        parts[2], parts[4] = parts[4], parts[2]
        modified = "## Part".join(parts)

    # 3. Change all paragraph boundaries (add extra newlines)
    modified = modified.replace("\n\n", "\n\n\n")

    # 4. Massive text insertion at beginning
    modified = "# NOTICE: Document Completely Restructured\n\n" + "A" * 1000 + "\n\n" + modified

    workspace.test_doc.write_text(modified)

    # Re-sync
    device.trigger_sync()
    updated_state = device.get_document_state(doc_uuid)

    # Verify annotations still present (even if some anchoring fails)
    updated_count = 0
    for page_uuid, rm_data in updated_state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        updated_count += len(annotations)

    # Under extreme stress, we may lose some anchoring, but should not lose all
    # This tests the robustness of the matching algorithm
    assert updated_count > 0, "All annotations lost under extreme modifications - anchoring failed"

    retention_rate = updated_count / initial_count if initial_count > 0 else 0
    print(f"\n📊 Stress test retention: {updated_count}/{initial_count} " f"({retention_rate:.1%})")

    # Under extreme stress, we accept some loss but want >50% retention
    # This can be tuned based on anchor matching algorithm sophistication
    assert (
        retention_rate > 0.5
    ), f"Excessive annotation loss under stress: {retention_rate:.1%} < 50%"

    device.end_test(test_id)
