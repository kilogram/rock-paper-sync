# Three-Way Merge Test

Test that local markdown edits AND device annotations are both preserved.

## Section 1: Annotate This

<!-- ANNOTATED: 1 highlight -->
**Trip 1 Instructions**: Highlight the word "preserved" in this section.
<!-- /ANNOTATED -->

Your annotations should be preserved even when the document is edited locally. The three-way merge algorithm handles this by tracking both content changes and annotation markers.

## Section 2: Will Be Edited Locally

LOCAL EDIT: Lorem ipsum dolor sit amet, consectetur adipiscing elit. This section will be edited by the LOCAL user (not you on the device).

LOCAL EDIT: The quick brown fox jumps over the lazy dog.

<!-- ANNOTATED: 1 highlight -->
## Section 3: Another Annotation Target
<!-- /ANNOTATED -->

**Trip 1 Instructions**: Highlight "annotation target" below.

This is an annotation target that should survive local edits to other sections. The merge system must detect non-conflicting changes.

## Section 4: Final Section

This section exists to provide additional content.

---

**LOCAL EDIT: Document modified locally after annotations**

This text was added by the local user.

---

**End of three-way merge test document**