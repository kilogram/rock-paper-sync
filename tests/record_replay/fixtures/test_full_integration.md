# Full Integration Test

This document exercises all features together to test complex interactions.

## Part 1: Mixed Annotations

**Instructions**:
- Highlight the word "integration" below (yellow)
- Underline "testing" with the highlighter (green)
- Draw a small star or checkmark next to this section

This integration testing document validates complex scenarios.

## Part 2: Handwriting with Context

**Instructions**: Write "2025" in the gap below

The year is: _________________

Additional context before and after to test anchoring.

## Part 3: Overlapping Annotations

**Instructions**:
- Highlight the entire sentence below (yellow)
- Then add handwritten notes in the margin
- Circle or underline specific words with pen

The quick brown fox jumps over the lazy dog. This tests overlapping highlights and strokes.

## Part 4: Code Block with Annotations

**Instructions**:
- Highlight key parts of this code
- Add handwritten notes or corrections

```python
def process_annotations(doc_id, page_num):
    # Load annotations from remarkable
    annotations = load_rm_file(doc_id, page_num)

    # Process each annotation
    for anno in annotations:
        if anno.type == "highlight":
            extract_highlight(anno)
        elif anno.type == "stroke":
            run_ocr(anno)

    return annotations
```

## Part 5: List with Annotations

**Instructions**:
- Highlight different items (use different colors)
- Add checkmarks or notes with pen

1. First item - important
2. Second item - critical
3. Third item - review needed
4. Fourth item - completed

## Part 6: Long Content for Multi-Page Testing (Page 2-3)

**Instructions**:
- Highlight key phrases throughout this section
- Add margin notes with pen
- Create annotations that will span page breaks
- Test anchoring across page boundaries

### Historical Context Paragraph 1

The development of modern computing began in the mid-20th century with the invention of electronic computers. The ENIAC (Electronic Numerical Integrator and Computer), completed in 1945, was one of the earliest general-purpose electronic computers. It weighed more than 27 tons and occupied about 1800 square feet of floor space.

The transistor, invented in 1947 at Bell Labs, revolutionized electronics and paved the way for smaller, more reliable computers. By the 1960s, transistors had largely replaced vacuum tubes in computer designs, leading to the second generation of computers that were smaller, faster, and more energy-efficient.

### Historical Context Paragraph 2

The integrated circuit, developed independently by Jack Kilby and Robert Noyce in 1958-1959, marked the beginning of the third generation of computers. This innovation allowed multiple transistors to be placed on a single silicon chip, dramatically reducing the size and cost of computers while increasing their reliability and speed.

The invention of the microprocessor in 1971 by Intel (the Intel 4004) ushered in the fourth generation of computers. This single chip contained all the components of a computer's central processing unit (CPU), making it possible to create personal computers that individuals could afford and use at home.

### Technical Evolution Paragraph 3

The 1970s and 1980s saw the rise of personal computing, with companies like Apple, IBM, and Microsoft leading the charge. The Apple II, released in 1977, and the IBM PC, introduced in 1981, brought computing to the masses. The development of user-friendly operating systems and graphical user interfaces (GUIs) made computers accessible to non-technical users.

The internet, which had its origins in the ARPANET project of the 1960s, became publicly accessible in the 1990s. The World Wide Web, invented by Tim Berners-Lee in 1989, transformed the internet into a global information network. This led to the dot-com boom and fundamentally changed how people communicate, work, and access information.

### Modern Era Paragraph 4

The 21st century has brought mobile computing, cloud services, artificial intelligence, and the Internet of Things (IoT). Smartphones have become ubiquitous, putting powerful computers in billions of pockets worldwide. Cloud computing has enabled on-demand access to computing resources, while AI and machine learning are transforming industries from healthcare to transportation.

The exponential growth in computing power, described by Moore's Law (which predicted that the number of transistors on a chip would double approximately every two years), has continued for decades, though it is now approaching physical limits. New computing paradigms, such as quantum computing and neuromorphic computing, may define the next era of technological advancement.

### Impact on Society Paragraph 5

Computing technology has had profound effects on nearly every aspect of modern life. It has transformed education through e-learning platforms, revolutionized healthcare with electronic medical records and telemedicine, and changed the nature of work with remote collaboration tools and automation.

Social media platforms have reshaped human communication and social interaction, creating both opportunities for connection and challenges related to privacy, misinformation, and mental health. The digital economy has created entirely new industries and business models, from e-commerce to the sharing economy.

As we continue into the digital age, questions about data privacy, algorithmic bias, cybersecurity, and the societal impact of automation become increasingly important. The future of computing will likely be shaped not just by technological capabilities, but by how we choose to address these ethical and social challenges.

## Part 7: Dense Annotation Area

**Instructions**:
- Create many annotations in a small area
- Mix highlights and handwriting
- Test anchor disambiguation

Text A: annotate_this_word
Text B: highlight_me
Text C: stroke_here
Text D: ocr_this_gap: _________

---

End of full integration test document.
