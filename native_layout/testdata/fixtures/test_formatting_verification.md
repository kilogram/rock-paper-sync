# Formatting Verification Test

This document tests that all markdown formatting types render correctly on the reMarkable device.

## Text Styles

This paragraph has **bold text** that should appear heavier/darker.

This paragraph has *italic text* that should appear slanted.

This paragraph has `inline code` that should appear in monospace font.

Combined styles: ***bold italic*** text and **`bold code`** text.

## Unordered Lists

Simple list:
- First item
- Second item
- Third item

Nested list:
- Parent item 1
  - Child item A
  - Child item B
    - Grandchild item
- Parent item 2
  - Child item C

## Ordered Lists

1. First numbered item
2. Second numbered item
3. Third numbered item

Nested ordered:
1. Step one
   1. Sub-step A
   2. Sub-step B
2. Step two

## Code Block

```python
def hello_world():
    """A simple function."""
    message = "Hello, reMarkable!"
    print(message)
    return message
```

Another code block with different language:

```javascript
function greet(name) {
    console.log(`Hello, ${name}!`);
}
```

## Blockquotes

> This is a blockquote.
> It can span multiple lines.
> Each line should be visually distinct from regular text.

Nested blockquote:

> Outer quote level
> > Inner quote level
> > Should be further indented

## Headers

### H3 Header (Third Level)

#### H4 Header (Fourth Level)

##### H5 Header (Fifth Level)

###### H6 Header (Sixth Level)

## Horizontal Rules

Content above the rule.

---

Content below the rule.

## Links

This paragraph contains a [link to example](https://example.com) that should be visually distinct.

## Strikethrough

This paragraph has ~~strikethrough text~~ that should have a line through it.

## Mixed Content

Here's a complex paragraph with **bold**, *italic*, `code`, and a [link](https://example.com) all together. This tests that formatting can coexist without breaking.

A list with formatted items:
- **Bold item**
- *Italic item*
- `Code item`
- ~~Strikethrough item~~

## Long Paragraph

This is a longer paragraph to test text wrapping and line spacing. The reMarkable device should wrap this text appropriately and maintain consistent line height throughout. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.

---

**End of formatting verification document**
