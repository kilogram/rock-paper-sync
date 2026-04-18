# Hidden Layer Orphan Preservation Test

This document tests M5.5 hidden-layer preservation of orphaned annotations.
When highlighted text is deleted, the annotation should be serialised into
a hidden .rm layer ("Rock Paper Sync — Orphans") so it survives future pushes.

## Section 1: Single Highlight Target

**Instructions:** Highlight "preserved forever" below.

This paragraph contains preserved forever text that will be annotated.
The entire section will be deleted in Trip 2, making the annotation orphaned.

## Section 2: Multiple Highlight Targets

**Instructions:** Highlight "first preserved" AND "second preserved" below.

Here is first preserved as one target phrase.
And here is second preserved as the other target phrase.
Both will be deleted together to produce two orphans in the hidden layer.

## Section 3: Stroke Target

**Instructions:** Draw a short stroke (any pen) over the word "stroked" below.

This line has the word stroked in it.
Draw a stroke over just that word.
The section will be deleted in Trip 3, orphaning the stroke into the hidden layer.

## Section 4: Control — Stays Throughout

**Instructions:** Highlight "control highlight" below.

This section survives all modifications unchanged.
The phrase control highlight should remain anchored through all trips.
It verifies that non-orphaned annotations are unaffected.

## Section 5: Recovery Zone

This section starts empty.
In Trip 4, the text "preserved forever" will be re-added here to verify
that the orphaned annotation in the hidden layer can be re-anchored.

---

**End of hidden layer orphan preservation test**
