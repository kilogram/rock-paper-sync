# Hidden Layer Orphan Preservation Test

<!-- ANNOTATED: 1 stroke -->
This document tests M5.5 hidden-layer preservation of orphaned annotations. When highlighted text is deleted, the annotation should be serialised into a hidden .rm layer ("Rock Paper Sync — Orphans") so it survives future pushes.
<!-- /ANNOTATED -->

## Section 4: Control — Stays Throughout

<!-- ANNOTATED: 1 highlight -->
**Instructions:** Highlight "control highlight" below.
<!-- /ANNOTATED -->

This section survives all modifications unchanged. The phrase control highlight should remain anchored through all trips. It verifies that non-orphaned annotations are unaffected.

<!-- ANNOTATED: 3 highlights -->
## Section 5: Recovery Zone
<!-- /ANNOTATED -->

This section starts empty. The phrase preserved forever has been restored here.
In Trip 4, the anchor phrase was re-introduced to verify that the orphaned annotation in the hidden layer can be re-anchored.

---

**End of hidden layer orphan preservation test**