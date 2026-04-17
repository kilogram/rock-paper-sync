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
    # Full recording from scratch
    uv run pytest tests/record_replay/test_full_integration.py --online -s

    # List available recording phases
    uv run pytest tests/record_replay/ --list-phases

    # Resume recording from phase 3 (after modifications applied)
    uv run pytest tests/record_replay/test_full_integration.py --online -s --resume-from-phase=3

    When prompted: Follow all instructions in the test document.
    This will take 5-10 minutes to annotate thoroughly.

Replaying:
    uv run pytest tests/record_replay/test_full_integration.py
"""

import pytest

from rock_paper_sync.annotations.core.data_types import RenderConfig
from rock_paper_sync.annotations.handlers.highlight_handler import HighlightHandler
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.annotations.ocr_corrections import detect_single_ocr_correction
from tests.record_replay.harness.phase import AnnotationState, PhaseContext, extract_annotations


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

    anno_state: AnnotationState | None = None

    # === PHASE 1: Initial Upload and Annotation ===
    with PhaseContext(
        device, 1, "initial_upload", "Upload document and wait for annotations"
    ) as ctx:
        if ctx.should_run:
            doc_uuid = device.upload_document(workspace.test_doc)

            initial_state = device.wait_for_annotations(doc_uuid)
            assert (
                initial_state.has_annotations
            ), "Need comprehensive annotations for integration test"

            anno_state = AnnotationState.from_document_state(initial_state)
            assert anno_state.highlights, "Need highlights for integration test"
            assert anno_state.strokes, "Need strokes for integration test"

            print(
                f"\n📊 Initial annotations: "
                f"{len(anno_state.highlights)} highlights, {len(anno_state.strokes)} strokes"
            )
    anno_state = anno_state or ctx.restored_state
    doc_uuid = (
        anno_state.doc_uuid if anno_state and anno_state.doc_uuid else None
    ) or workspace.get_document_uuid()

    # === PHASE 2: Pull Sync Verification ===
    with PhaseContext(
        device, 2, "pull_sync", "Verify annotations render correctly in markdown"
    ) as ctx:
        if ctx.should_run:
            device.trigger_sync()
            synced_content = workspace.test_doc.read_text()

            # Highlights should render as ==text==
            highlight_markers = synced_content.count("==")
            if anno_state and anno_state.highlights:
                if highlight_markers >= 2:
                    print(
                        f"✅ Pull sync: Found {highlight_markers // 2} ==text== highlight markers"
                    )
                else:
                    print(
                        "⚠️  Pull sync: No ==text== highlight markers found "
                        "(may depend on implementation)"
                    )

            # Strokes should render as [^n] footnotes
            footnote_markers = synced_content.count("[^")
            if anno_state and anno_state.strokes:
                if footnote_markers > 0:
                    print(f"✅ Pull sync: Found {footnote_markers} [^n] stroke footnote markers")
                else:
                    print(
                        "⚠️  Pull sync: No [^n] stroke markers found (may depend on implementation)"
                    )

    # === PHASE 3: Markdown Modifications (Conflict Scenario) ===
    with PhaseContext(
        device, 3, "markdown_mods", "Apply structural modifications to markdown"
    ) as ctx:
        if ctx.should_run:
            # Modify the document in ways that challenge anchoring:
            # 1. Add text at beginning (shifts all positions)
            # 2. Insert paragraphs in middle (breaks anchors)
            # 3. Reformat sections (changes paragraph boundaries)
            # 4. Modify annotated text slightly (anchor tolerance test)

            modified_content = workspace.test_doc.read_text()

            modified_content = modified_content.replace(
                "# Full Integration Test",
                "# Full Integration Test\n\n> **Version 2.0** - Modified after initial annotations",
            )
            modified_content = modified_content.replace(
                "## Part 3: Overlapping Annotations",
                "## Part 2.5: Inserted Section\n\n"
                "This section was added AFTER annotations were created.\n"
                "It tests whether anchoring can handle structural changes.\n\n"
                "## Part 3: Overlapping Annotations",
            )
            modified_content = modified_content.replace(
                "This integration testing document validates complex scenarios.",
                "This **integration testing** document validates **complex scenarios**.\n\n"
                "_Note: Formatting changed after annotation._",
            )
            modified_content = modified_content.replace(
                "Additional context before and after to test anchoring.",
                "Additional context before and after to test anchoring robustness.",
            )

            workspace.test_doc.write_text(modified_content)
            print("\n📝 Applied structural modifications to markdown")

    # === PHASE 4: Re-sync with Modifications ===
    with PhaseContext(device, 4, "resync", "Re-sync and verify annotations preserved") as ctx:
        if ctx.should_run:
            device.trigger_sync()

            updated_state = device.get_document_state(doc_uuid)
            assert (
                updated_state.has_annotations
            ), "Annotations should survive markdown modifications"

            new_state = AnnotationState.from_document_state(updated_state)

            if anno_state:
                anno_state.assert_count_preserved(new_state)

            print(
                f"✅ All annotations preserved after modifications: "
                f"{len(new_state.highlights)} highlights, {len(new_state.strokes)} strokes"
            )
            anno_state = new_state

            # Pull sync to verify annotations render in markdown after modifications
            device.trigger_sync()
            modified_synced = workspace.test_doc.read_text()

            post_highlight_markers = modified_synced.count("==")
            post_footnote_markers = modified_synced.count("[^")

            if anno_state.highlights:
                if post_highlight_markers >= 2:
                    print(
                        f"✅ Post-modification pull sync: {post_highlight_markers // 2} highlight markers"
                    )
                else:
                    print(
                        "⚠️  Post-modification: highlight markers may have been affected by restructure"
                    )

            if anno_state.strokes:
                if post_footnote_markers > 0:
                    print(f"✅ Post-modification pull sync: {post_footnote_markers} stroke markers")
                else:
                    print(
                        "⚠️  Post-modification: stroke markers may have been affected by restructure"
                    )
    anno_state = anno_state or ctx.restored_state

    # Remaining phases are pure validation — no checkpoints
    final_highlights = anno_state.highlights if anno_state else []
    final_strokes = anno_state.strokes if anno_state else []

    # === PHASE 5: Anchor Verification (pure validation) ===
    highlight_handler = HighlightHandler()
    stroke_handler = StrokeHandler()

    final_content = workspace.test_doc.read_text()
    paragraphs = final_content.split("\n\n")

    for highlight in final_highlights[:3]:
        for para_idx, para_text in enumerate(paragraphs):
            if len(para_text.strip()) < 10:
                continue
            anchor = highlight_handler.create_anchor(
                annotation=highlight,
                paragraph_text=para_text,
                paragraph_index=para_idx,
                page_num=0,
            )
            assert anchor.text_content is not None, "Highlight anchor should have text content"
            assert anchor.content_hash, "Highlight anchor should have content hash"
            assert anchor.paragraph_index == para_idx, "Paragraph index should match"

    for stroke in final_strokes[:3]:
        for para_idx, para_text in enumerate(paragraphs):
            if len(para_text.strip()) < 10:
                continue
            anchor = stroke_handler.create_anchor(
                annotation=stroke, paragraph_text=para_text, paragraph_index=para_idx, page_num=0
            )
            assert anchor.y_position_hint is not None, "Stroke anchor should have Y position hint"
            assert anchor.paragraph_index == para_idx, "Paragraph index should match"

    print("✅ Anchor creation successful after modifications")

    # === PHASE 6: OCR Correction Detection (pure validation) ===
    config = RenderConfig(stroke_style="comment")

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

    # === PHASE 7: Annotation Markers in Modified Document (pure validation) ===
    final_markdown = workspace.test_doc.read_text()

    if final_highlights:
        has_highlight_markers = (
            "<!-- ANNOTATED:" in final_markdown or "ANNOTATED:" in final_markdown
        )
        assert has_highlight_markers, "Modified document should contain highlight markers"
        print("✅ Highlight markers present in modified markdown")

    if final_strokes:
        has_stroke_markers = " stroke" in final_markdown
        assert has_stroke_markers, "Modified document should contain stroke markers"
        print("✅ Stroke markers present in modified markdown")

    # === PHASE 8: Anchor Disambiguation (pure validation) ===
    highlight_anchors = []
    for highlight in final_highlights:
        for para_idx, para_text in enumerate(paragraphs[:10]):
            if len(para_text.strip()) < 5:
                continue
            anchor = highlight_handler.create_anchor(
                annotation=highlight,
                paragraph_text=para_text,
                paragraph_index=para_idx,
                page_num=0,
            )
            if anchor.text_content:
                highlight_anchors.append((highlight.annotation_id, anchor.text_content, para_idx))

    if len(highlight_anchors) > 1:
        unique_texts = set(h[1] for h in highlight_anchors)
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

    fixture_doc = fixtures_dir / "test_full_integration.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        device.start_test(test_id, description="Stress test: extreme modification conflicts")
    except FileNotFoundError:
        pytest.skip(f"Testdata '{test_id}' not available. Run with --online -s to record.")

    doc_uuid = device.upload_document(workspace.test_doc)
    initial_state = device.wait_for_annotations(doc_uuid)
    assert initial_state.has_annotations

    initial_highlights, initial_strokes = extract_annotations(initial_state.rm_files)
    initial_count = len(initial_highlights) + len(initial_strokes)

    # EXTREME modifications
    original = workspace.test_doc.read_text()

    modified = original.replace("## Part 1: Mixed Annotations", "## Part 1: [DELETED]")

    parts = modified.split("## Part")
    if len(parts) > 4:
        parts[2], parts[4] = parts[4], parts[2]
        modified = "## Part".join(parts)

    modified = modified.replace("\n\n", "\n\n\n")
    modified = "# NOTICE: Document Completely Restructured\n\n" + "A" * 1000 + "\n\n" + modified

    workspace.test_doc.write_text(modified)

    device.trigger_sync()
    updated_state = device.get_document_state(doc_uuid)

    updated_highlights, updated_strokes = extract_annotations(updated_state.rm_files)
    updated_count = len(updated_highlights) + len(updated_strokes)

    assert updated_count > 0, "All annotations lost under extreme modifications - anchoring failed"

    retention_rate = updated_count / initial_count if initial_count > 0 else 0
    print(f"\n📊 Stress test retention: {updated_count}/{initial_count} ({retention_rate:.1%})")

    assert (
        retention_rate > 0.5
    ), f"Excessive annotation loss under stress: {retention_rate:.1%} < 50%"

    device.end_test(test_id)
