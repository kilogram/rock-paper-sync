# Comprehensive Highlight Test

Tests all highlight behaviors in one
recording session. Every line is under
the 750 px device text width so nothing
wraps (reMarkableSans @ 10.16 pt).

## Section 1: Basic Highlight

**Instructions:** Highlight "first
target" below (yellow).

The first target text appears here for
basic highlight capture testing. This
tests a simple highlight capture.

## Section 2: Reanchoring Target

**Instructions:** Highlight "will move"
below.

This paragraph contains the phrase
will move that you should highlight.
In Trip 2, a new paragraph is inserted
above this section. The highlight must
reanchor and follow this text.

## Section 3: Conflict Target

**Instructions:** Highlight "edit me"
below.

Please highlight the exact phrase
edit me in this paragraph. In Trip 3
this text becomes "EDITED TEXT". We
test conflicting modifications here.

## Section 4: Multi-line Highlight

**Instructions:** Highlight from "start
here" to "end here" (several lines).

This tests multi-line highlights: you
start here and continue selecting
across multiple lines until you reach
end here for the complete selection.

## Section 5: Duplicate Text

**Instructions:** Highlight the SECOND
occurrence of "duplicate" below.

The word duplicate appears in this
first sentence. The word duplicate
also appears in this second sentence.
Highlight only the second one.

## Section 6: Stability Control

**Instructions:** Highlight "stable
anchor" below.

This section with stable anchor text
should never change. It is a control:
unrelated modifications must not move
annotations that should stay stable.

## Section 7: Deletion Zone

**Instructions:** Highlight "delete
zone" below.

This section containing delete zone
will be deleted in Trip 4. Its
highlight should become an orphan.
This tests orphan creation.

---

**End of comprehensive highlight test**
