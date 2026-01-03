# Highlight Anchoring Test

> **INSERTED CONTENT**: This block was added after Trip 1 annotations.
> It shifts all content below, testing position-independent anchoring.


Test that highlights re-anchor correctly when markdown content is modified.

<!-- ANNOTATED: 4 strokes -->
## Section 1: Target Text
<!-- /ANNOTATED -->

<!-- ANNOTATED: 1 highlight, 1 stroke -->
**Trip 1 Instructions**: Highlight the word "anchoring" in the paragraph below.
<!-- /ANNOTATED -->

The annotation anchoring system must handle content changes gracefully. When you highlight text and then the document is modified, the highlight should move to follow its target text.

## Section 1.5: New Section (Inserted)

This entire section was inserted after annotations were created.
Content below should shift but annotations should follow their targets.

## Section 2: Shifting Content

<!-- ANNOTATED: 1 highlight -->
**Trip 1 Instructions**: Highlight the phrase "will shift down" below.
<!-- /ANNOTATED -->

This content will shift down when new text is inserted above it. The anchoring system must track the highlight's target text, not its absolute position.

## Section 3: Handwriting Area

**Trip 1 Instructions**: Write "test" with the pen tool below.

Write here: _______________________

<!-- ANNOTATED: 1 highlight -->
## Section 4: Three-Way Merge Target
<!-- /ANNOTATED -->

**Trip 1 Instructions**: Highlight "three-way merge" in this paragraph.

The three-way merge algorithm combines local markdown edits with device annotations. Both changes must be preserved in the final result.

---

**End of highlight anchoring test**