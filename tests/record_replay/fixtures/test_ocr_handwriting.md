# OCR Handwriting Test

This document is designed for testing OCR processing of handwritten annotations.

## Instructions

Use the ballpoint pen to write text in the designated areas below. The OCR system should recognize your handwriting and generate text annotations.

## Test 1: Simple Words

**Write "hello" using strokes:**

<!-- OCR_EXPECT: text="hello" confidence_min=0.7 -->
> ─────────────────────────────
>
> [Write here]
>
> ─────────────────────────────
<!-- /OCR_EXPECT -->

## Test 2: Numbers

**Write "2025" using strokes:**

<!-- OCR_EXPECT: text="2025" confidence_min=0.7 -->
> ─────────────────────────────
>
> [Write here]
>
> ─────────────────────────────
<!-- /OCR_EXPECT -->

## Test 3: Short Phrase

**Write "quick test" using strokes:**

<!-- OCR_EXPECT: text="quick test" confidence_min=0.7 -->
> ─────────────────────────────
>
> [Write here]
>
> ─────────────────────────────
<!-- /OCR_EXPECT -->

## Test 4: Mixed Content

**Write "Code 42" using strokes:**

<!-- OCR_EXPECT: text="Code 42" confidence_min=0.7 -->
> ─────────────────────────────
>
> [Write here]
>
> ─────────────────────────────
<!-- /OCR_EXPECT -->

## Test 5: Longer Text

**Write "The quick brown fox" using strokes:**

<!-- OCR_EXPECT: text="The quick brown fox" confidence_min=0.7 -->
> ─────────────────────────────
>
> [Write here]
>
> ─────────────────────────────
<!-- /OCR_EXPECT -->

---

After writing, sync the document. The OCR system should recognize your handwriting.
