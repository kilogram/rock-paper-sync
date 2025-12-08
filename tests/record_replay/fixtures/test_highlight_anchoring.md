# Highlight Anchoring Test

Test that highlights re-anchor correctly when markdown content is modified.

## Section 1: Target Text

**Trip 1 Instructions**: Highlight the word "anchoring" in the paragraph below.

The annotation anchoring system must handle content changes gracefully. When you highlight text and then the document is modified, the highlight should move to follow its target text.

## Section 2: Shifting Content

**Trip 1 Instructions**: Highlight the phrase "will shift down" below.

This content will shift down when new text is inserted above it. The anchoring system must track the highlight's target text, not its absolute position.

## Section 3: Handwriting Area

**Trip 1 Instructions**: Write "test" with the pen tool below.

Write here: _______________________

## Section 4: Three-Way Merge Target

**Trip 1 Instructions**: Highlight "three-way merge" in this paragraph.

The three-way merge algorithm combines local markdown edits with device annotations. Both changes must be preserved in the final result.

---

**End of highlight anchoring test**
