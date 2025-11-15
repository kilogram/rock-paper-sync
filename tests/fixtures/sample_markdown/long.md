# Long Document for Pagination Testing

This document is designed to test pagination logic by containing enough content
to require multiple pages when rendered on a reMarkable device.

## Introduction

This is the introduction section. It contains several paragraphs to simulate
a real document that would need to be split across multiple pages.

The reMarkable Paper Pro has a resolution of 1404x1872 pixels. With typical
margins and font sizes, we estimate approximately 45-50 lines per page.

This document should span at least 3-4 pages to properly test pagination logic.

## Section 1: Overview

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor
incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis
nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.

Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore
eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt
in culpa qui officia deserunt mollit anim id est laborum.

### Subsection 1.1

More content here to fill up space. Each paragraph should be long enough to
take multiple lines when rendered on the device.

We want to ensure that page breaks occur at sensible boundaries, such as
between paragraphs or before headers, rather than mid-sentence.

### Subsection 1.2

Additional content in this subsection. Testing the pagination algorithm requires
having enough varied content to see how it handles different scenarios.

## Section 2: Detailed Content

This section contains more detailed content with various formatting elements
to test how they interact with pagination.

**Bold text** should be handled correctly across page boundaries. Similarly,
*italic text* and `inline code` need to work properly.

Here's a list to test list handling:

- First list item with some content
- Second list item with more content
- Third list item
  - Nested item one
  - Nested item two
- Fourth list item to continue the list

And a numbered list:

1. First numbered item
2. Second numbered item with longer content
3. Third numbered item
4. Fourth numbered item
5. Fifth numbered item

## Section 3: Code Blocks

Code blocks should also be handled properly during pagination.

```python
def example_function():
    """This is a sample function."""
    result = []
    for i in range(10):
        result.append(i * 2)
    return result

class ExampleClass:
    def __init__(self, value):
        self.value = value

    def process(self):
        return self.value * 2
```

The code block above should ideally not be split across pages if possible.

## Section 4: More Content

Continuing with more content to ensure we reach multiple pages. This section
discusses various aspects of document formatting and rendering.

The goal is to have at least 100-150 lines of content total, which should
translate to approximately 3-4 pages on the reMarkable device.

### Subsection 4.1: Technical Details

When implementing pagination, we need to consider:

- Line length and wrapping
- Font size and line height
- Margin sizes
- Special handling for different block types
- Preserving formatting across boundaries

Each of these factors affects how content is split across pages.

### Subsection 4.2: Implementation Considerations

The pagination algorithm should:

1. Estimate lines per block based on content length
2. Track running total of lines on current page
3. Start new page when approaching limit
4. Handle headers specially (start new page if < 10 lines remain)
5. Never split paragraphs mid-way
6. Keep code blocks together when possible

## Section 5: Edge Cases

This section covers edge cases in pagination:

### Very Long Paragraphs

Sometimes a single paragraph might be so long that it exceeds a full page. In this case, the paragraph must be split, but we should try to split at sentence boundaries or at least at word boundaries rather than mid-word. This paragraph is intentionally long to test this scenario. It keeps going with more and more content to ensure we're testing the edge case properly. Additional sentences are added here to make sure we exceed reasonable limits. The pagination algorithm needs to handle this gracefully without breaking the layout or losing content.

### Multiple Short Items

- Item 1
- Item 2
- Item 3
- Item 4
- Item 5
- Item 6
- Item 7
- Item 8
- Item 9
- Item 10
- Item 11
- Item 12
- Item 13
- Item 14
- Item 15

Lists with many short items should be handled efficiently.

## Section 6: Additional Content

More content to pad out the document and ensure we have enough material
for proper multi-page testing.

This section contains several paragraphs with varying lengths and formats
to provide a realistic test case.

### Subsection 6.1

Content here with **bold emphasis** and *italic styling* throughout.

### Subsection 6.2

More content with `inline code examples` and various formatting.

## Section 7: Near the End

We're approaching the end of the document now. This section helps ensure
we have enough content for at least 3-4 pages.

The pagination logic should handle the last page correctly, even if it's
not completely full.

### Final Subsection

Last bit of content before the conclusion.

## Conclusion

This concludes the long document test fixture. If you're reading this,
the parser successfully handled a multi-page document!

The total line count should be well over 150 lines, ensuring multiple
pages are required for rendering on the reMarkable device.

Thank you for testing pagination!
