# Comprehensive Highlight Test

This document tests all highlight behaviors in a single recording session.

## Section 1: Basic Highlight

**Instructions:** Highlight "first target" below (yellow).

The first target text appears here for basic highlight capture testing.
This tests that we can capture a simple highlight correctly.

## Section 2: Reanchoring Target

**Instructions:** Highlight "will move" below.

This paragraph contains the phrase will move that you should highlight.
In Trip 2, a new paragraph will be inserted above this section.
The highlight should reanchor to follow this text to its new position.

## Section 3: Conflict Target

**Instructions:** Highlight "edit me" below.

Please highlight the exact phrase edit me in this paragraph.
In Trip 3, this exact text will be changed to "EDITED TEXT".
We want to test how the system handles conflicting modifications.

## Section 4: Multi-line Highlight

**Instructions:** Highlight from "start here" to "end here" (spanning multiple lines).

This tests multi-line highlight handling where you should
start here and continue selecting across
multiple lines until you reach
end here for the complete selection.

## Section 5: Duplicate Text

**Instructions:** Highlight the SECOND occurrence of "duplicate" below.

The word duplicate appears in this first sentence.
The word duplicate also appears in this second sentence.
Highlight only the second one to test disambiguation.

## Section 6: Stability Control

**Instructions:** Highlight "stable anchor" below.

This section with stable anchor text should never change.
It serves as a control to verify that unrelated modifications
don't affect annotations that should remain stable.

## Section 7: Deletion Zone

**Instructions:** Highlight "delete zone" below.

This entire section containing delete zone will be deleted in Trip 4.
When the section is deleted, the highlight should become an orphan.
This tests orphan creation through deletion.

---

**End of comprehensive highlight test document**
