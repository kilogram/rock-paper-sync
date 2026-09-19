"""Formatting verification test (Milestone 1).

This test verifies that all markdown formatting types render correctly
on the reMarkable device. It's a visual confirmation test - the user
observes the rendered document and confirms each formatting type appears
correctly.

Test flow (1 trip, visual confirmation):
1. Upload document with all formatting types
2. User visually confirms each type renders correctly
3. Record pass/fail observation

Recording Usage:
    uv run pytest tests/record_replay/test_formatting_verification.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_formatting_verification.py
"""

import pytest


@pytest.mark.device
def test_formatting_verification(device, workspace, fixtures_dir):
    """Visual verification of all formatting types.

    This test verifies:
    - Bold text (heavier/darker)
    - Italic text (slanted)
    - Inline code (monospace)
    - Combined styles (bold italic, bold code)
    - Unordered lists (with nesting)
    - Ordered lists (with nesting)
    - Code blocks (syntax highlighting optional)
    - Blockquotes (with nesting)
    - Headers (H3-H6 hierarchy)
    - Horizontal rules
    - Links (visually distinct)
    - Strikethrough
    - Mixed formatting in paragraphs
    - Text wrapping for long paragraphs
    """
    fixture_doc = fixtures_dir / "test_formatting_verification.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="Formatting verification (visual)"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # =========================================================================
    # TRIP 1: Visual verification
    # =========================================================================
    print("\n" + "=" * 60)
    print("FORMATTING VERIFICATION")
    print("=" * 60)
    print("\nPlease verify each formatting type on the device:")
    print()
    print("TEXT STYLES:")
    print("  [ ] Bold text appears heavier/darker")
    print("  [ ] Italic text appears slanted")
    print("  [ ] Inline code appears in monospace")
    print("  [ ] Bold italic combines both styles")
    print("  [ ] Bold code combines both styles")
    print()
    print("LISTS:")
    print("  [ ] Unordered list has bullet points")
    print("  [ ] Nested items are indented")
    print("  [ ] Ordered list has numbers")
    print("  [ ] Nested ordered items are properly numbered")
    print()
    print("CODE BLOCKS:")
    print("  [ ] Code blocks are visually distinct")
    print("  [ ] Monospace font is used")
    print("  [ ] Indentation is preserved")
    print()
    print("BLOCKQUOTES:")
    print("  [ ] Blockquotes are indented/styled differently")
    print("  [ ] Nested blockquotes show hierarchy")
    print()
    print("HEADERS:")
    print("  [ ] H3 is larger than H4")
    print("  [ ] H4 is larger than H5")
    print("  [ ] H5 is larger than H6")
    print("  [ ] Headers stand out from body text")
    print()
    print("OTHER:")
    print("  [ ] Horizontal rules are visible")
    print("  [ ] Links are visually distinct (underline/color)")
    print("  [ ] Strikethrough has line through text")
    print("  [ ] Mixed formatting works correctly")
    print("  [ ] Long paragraphs wrap properly")

    doc_uuid = device.upload_document(workspace.test_doc)

    # Wait for user to review the document
    # The device fixture will prompt user for confirmation
    device.observe_result(
        "VISUAL VERIFICATION CHECKLIST:\n\n"
        "1. TEXT STYLES\n"
        "   - Bold: heavier/darker weight\n"
        "   - Italic: slanted text\n"
        "   - Code: monospace font\n"
        "   - Combined: both styles visible\n\n"
        "2. LISTS\n"
        "   - Bullets for unordered\n"
        "   - Numbers for ordered\n"
        "   - Proper nesting indentation\n\n"
        "3. CODE BLOCKS\n"
        "   - Distinct background/border\n"
        "   - Monospace font\n"
        "   - Preserved indentation\n\n"
        "4. BLOCKQUOTES\n"
        "   - Visual distinction (indent/bar)\n"
        "   - Nested hierarchy visible\n\n"
        "5. HEADERS\n"
        "   - Size hierarchy (H3 > H4 > H5 > H6)\n"
        "   - Stand out from body text\n\n"
        "6. OTHER ELEMENTS\n"
        "   - Horizontal rules visible\n"
        "   - Links distinguishable\n"
        "   - Strikethrough line present\n"
        "   - Long text wraps correctly\n\n"
        "Does everything render correctly? (y/n)"
    )

    # Verify document was uploaded successfully
    doc_state = device.get_document_state(doc_uuid)
    assert doc_state is not None, "Document should be accessible"

    # =========================================================================
    # FINAL SUMMARY
    # =========================================================================
    print("\n" + "=" * 60)
    print("VERIFICATION COMPLETE")
    print("=" * 60)
    print("\nThe formatting verification test has completed.")
    print("Results are based on your visual observation.")
    print("\nIf any formatting appeared incorrect, please note:")
    print("  - Which formatting type had issues")
    print("  - How it appeared vs. expected")
    print("  - Device model and firmware version")

    print("\nFormatting verification test PASSED (visual confirmation)")

    device.end_test(test_id)
