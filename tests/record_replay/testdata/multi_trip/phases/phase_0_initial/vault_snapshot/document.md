# Multi-Trip Annotation Test

This document tests multi-trip sync scenarios: annotations, markdown modifications, and three-way merge.

## Section 1: Highlight Anchoring

**Instructions for Trip 1**: Highlight the word "anchoring" in the paragraph below.

The annotation anchoring system must handle content changes gracefully. When you highlight text and then the document is modified, the highlight should move to follow its target text. This is essential for a good user experience.

### Additional Context

This paragraph provides context around the highlighted section. It helps test that anchors can disambiguate between similar text in different locations.

## Section 2: Text That Will Shift

**Instructions for Trip 1**: Highlight the phrase "will shift down" below.

This content will shift down when new text is inserted above it. The anchoring system must track the highlight's target text, not its absolute position.

### Why Position-Independent Anchoring Matters

If anchors were based solely on page position (x, y coordinates), any document modification would break them. Instead, we use content-based anchoring that identifies text by its content hash and surrounding context.

## Section 3: Handwriting Area

**Instructions for Trip 1**: Write "test" in the area below.

Write here: _______________________

The handwriting strokes should remain anchored to this section even when the document structure changes above.

## Section 4: Dense Content for Page Spanning

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris.

Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.

### Subsection: More Padding

The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs. How quickly daft jumping zebras vex. Sphinx of black quartz, judge my vow.

## Section 5: Three-Way Merge Target

**Instructions for Trip 1**: Highlight "three-way merge" in this paragraph.

The three-way merge algorithm combines local markdown edits with device annotations. When you edit the markdown locally AND add annotations on the device, both changes must be preserved in the final result.

### Merge Conflict Scenarios

What happens when you edit the exact text that was highlighted? The system should:
1. Detect the modification
2. Attempt to re-anchor the highlight to similar text
3. Preserve annotation markers even if exact match fails

## Section 6: Final Section

This section exists to provide additional content for multi-page testing.

---

**End of multi-trip test document**
