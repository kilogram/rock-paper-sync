---
title: Comprehensive Test Document
author: Test Suite
tags:
  - test
  - markdown
  - fixtures
date: 2024-11-15
---

# Main Document Title

This is an introductory paragraph with **bold text**, *italic text*, and ***bold italic*** combined. It also includes `inline code` snippets and ~~strikethrough~~ text.

## Headers at Different Levels

### Level 3 Header

Content under level 3.

#### Level 4 Header

Content under level 4.

##### Level 5 Header

Content under level 5.

###### Level 6 Header

Content under level 6.

## Text Formatting

This paragraph contains multiple formatting types:

- **Bold text** for emphasis
- *Italic text* for subtle emphasis  
- ***Bold and italic*** combined
- `Inline code` for technical terms
- ~~Strikethrough~~ for corrections

Nested formatting: **This is bold with *italic inside* it** and *this is italic with **bold inside** it*.

## Lists

### Unordered Lists

- First item
- Second item
  - Nested item 2.1
  - Nested item 2.2
    - Deeply nested 2.2.1
    - Deeply nested 2.2.2
  - Nested item 2.3
- Third item
- Fourth item with **bold** and *italic* formatting

### Ordered Lists

1. First numbered item
2. Second numbered item
   1. Sub-item 2.1
   2. Sub-item 2.2
3. Third numbered item
4. Fourth numbered item

### Mixed Lists

- Bullet item
  1. Nested number 1
  2. Nested number 2
- Another bullet
  - Sub-bullet
    1. Number under sub-bullet

## Links and Images

Here's a [link to Obsidian](https://obsidian.md) and another [link to reMarkable](https://remarkable.com/store/remarkable-paper-pro).

Images are converted to placeholders:

![Diagram of system architecture](./images/architecture.png)

![Photo of handwritten notes](../assets/notes.jpg)

## Code Blocks

### Fenced Code Block

```python
def hello_world():
    """A simple function"""
    print("Hello, World!")
    
if __name__ == "__main__":
    hello_world()
```

### Another Language

```javascript
const greet = (name) => {
    console.log(`Hello, ${name}!`);
};

greet("reMarkable");
```

### Code Without Language Specification

```
This is a plain code block
without syntax highlighting
```

## Blockquotes

> This is a blockquote. It can contain multiple lines
> and spans across them elegantly.
>
> It can also have multiple paragraphs within the quote.

Nested blockquote:

> Outer quote
> > Inner quote
> > > Deeply nested quote

## Horizontal Rules

Above the line.

---

Below the first line.

***

Below the second line.

___

Below the third line.

## Special Characters

Testing special characters: & < > " ' © ® ™ € £ ¥

Unicode: café, naïve, résumé, Zürich, 日本語, 한국어, العربية

Emoji: 📝 ✅ ⚠️ 🎉 (may not render on reMarkable)

## Long Paragraph for Pagination Testing

This is a deliberately long paragraph intended to test how the pagination system handles content that might wrap across multiple lines. The system should calculate approximately how many lines this text will consume based on the configured page width and character size estimates. When the cumulative line count approaches the configured lines per page threshold, the paginator should identify an appropriate break point. Ideally, this would occur at a paragraph boundary rather than mid-sentence. The goal is to create documents that are readable and logically organized when viewed on the reMarkable device. This paragraph continues with more content to ensure we're testing the line counting mechanism thoroughly. Additional sentences add to the overall length, ensuring that this single block of text represents a significant portion of a page. The rendering engine must account for margins, font size, and spacing when determining the actual visual layout on the reMarkable's 1404x1872 pixel display.

## Tables (May Not Be Supported)

| Column 1 | Column 2 | Column 3 |
|----------|----------|----------|
| Data A1  | Data A2  | Data A3  |
| Data B1  | Data B2  | Data B3  |
| Data C1  | Data C2  | Data C3  |

Tables might be converted to text representation.

## Task Lists

- [x] Completed task
- [ ] Incomplete task
- [x] Another done item
- [ ] Still to do

## Footnotes

Here's a sentence with a footnote[^1].

[^1]: This is the footnote content.

## Mathematical Notation (LaTeX)

Inline math: $E = mc^2$

Block math:
$$
\int_{0}^{\infty} e^{-x^2} dx = \frac{\sqrt{\pi}}{2}
$$

Note: Math rendering depends on reMarkable's capabilities.

## Conclusion

This test document covers a comprehensive range of Markdown syntax elements that the parser should handle. Each element type should be properly identified, parsed into structured blocks, and converted to reMarkable format with appropriate formatting preserved where possible.

The ultimate test is whether the generated reMarkable document is readable and functional on the actual device.
