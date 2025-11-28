# Test Markdown Modifications

This document tests annotation anchoring across markdown modifications and multi-page scenarios.

## Section 1: Text to Highlight (Page 1)

**Instructions**: Highlight the word "persistent" below:

The annotations should be persistent even when the document is modified. This is critical for ensuring that user annotations survive markdown edits, reformatting, and structural changes.

### Background on Annotation Persistence

When working with documents that evolve over time, it's essential that annotations remain anchored to their original context. This requires robust anchor matching algorithms that can handle various types of modifications:

1. **Text insertion** - Adding new paragraphs before, after, or within annotated sections
2. **Text deletion** - Removing content while preserving nearby annotations
3. **Reformatting** - Changing markdown syntax, adding emphasis, restructuring headings
4. **Reordering** - Moving sections around while maintaining annotation integrity

The anchor matching system uses multiple signals to re-locate annotations:
- Text content hashing for highlights
- Spatial position (x, y coordinates) for strokes
- Bounding box overlap (IoU) for handwriting
- Paragraph-level context for disambiguation

### Why This Matters

Consider a real-world scenario: A user highlights important passages in a research paper, adds handwritten notes in the margins, and uses OCR to capture handwritten formulas. Later, they reorganize the document, add new sections, and fix typos. Without robust anchoring, all those annotations would be lost or misplaced.

This test validates that the system handles these challenges correctly.

## Section 2: Handwriting Area (Page 1-2)

**Instructions**: Write "hello" in the gap below:

Write here: _________________

After writing, the system will:
1. Detect the handwritten strokes
2. Create spatial anchors (x, y, bbox)
3. Optionally run OCR if enabled
4. Embed markers in the markdown

When the document is later modified, the anchor matching algorithm will:
1. Re-scan the document structure
2. Find paragraphs near the original position
3. Match based on spatial proximity and context
4. Re-insert annotation markers in the correct location

### More Context for Page Spanning

The quick brown fox jumps over the lazy dog. This pangram contains every letter of the alphabet and is commonly used for font testing.

Pack my box with five dozen liquor jugs. How quickly daft jumping zebras vex. Quick zephyrs blow, vexing daft Jim. Sphinx of black quartz, judge my vow. Two driven jocks help fax my big quiz.

These additional pangrams ensure we have enough content to span multiple pages, which is important for testing:
- Page boundary handling
- Cross-page annotation scenarios
- Pagination stability across modifications

## Section 3: Dense Content Area (Page 2)

This section contains dense text to push content across pages and test anchoring when document flow changes.

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.

Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.

### Subsection: Technical Details

The annotation anchoring system must handle several edge cases:

**Edge Case 1: Annotation Near Page Break**
When an annotation is positioned near a page boundary, modifications to earlier content can shift the page break, moving the annotation to a different page. The anchor matching must account for this by using content-based signals, not just page numbers.

**Edge Case 2: Paragraph Boundary Changes**
Adding or removing line breaks changes paragraph boundaries. Since anchors are often paragraph-relative, the system must gracefully handle paragraph merging and splitting.

**Edge Case 3: Multi-Line Highlights**
A highlight that spans multiple lines or paragraphs requires special handling. The anchor must capture the start and end positions, and re-matching must handle cases where intervening text is inserted or deleted.

## Section 4: Additional Padding (Page 2-3)

More content to ensure multi-page layout and test various modification scenarios.

### Paragraph 1
The Industrial Revolution, which began in Britain in the late 18th century, marked a major turning point in history. Almost every aspect of daily life was influenced in some way. Most notably, average income and population began to exhibit unprecedented sustained growth.

### Paragraph 2
In the two centuries following 1800, the world's average per capita income increased over tenfold, while the world's population increased over sixfold. In the words of Nobel Prize winner Robert E. Lucas Jr., "For the first time in history, the living standards of the masses of ordinary people have begun to undergo sustained growth."

### Paragraph 3
The Industrial Revolution began in Great Britain, and many of the technological innovations were of British origin. The development of trade and the rise of business were among the major causes of the Industrial Revolution. The effects spread throughout Western Europe and North America during the 19th century.

### Paragraph 4
The term Industrial Revolution was introduced by Friedrich Engels and Louis-Auguste Blanqui in the mid-19th century, but became widely used by historians after Arnold Toynbee in his 1884 lectures on the Industrial Revolution in England.

## Section 5: Final Content (Page 3)

This final section ensures we have solid multi-page coverage.

**Instructions**: If you've scrolled through all the content above, you can add additional annotations here to test cross-document scenarios.

### Summary Points

- ✅ Multi-page document structure
- ✅ Various annotation types (highlights, strokes, OCR)
- ✅ Content modifications (insert, delete, reformat)
- ✅ Anchor re-matching across changes
- ✅ Pagination stability testing

The combination of all these elements provides comprehensive testing of the annotation system under realistic usage conditions.

---

**End of modification test document** - Total expected pages: 3-4
