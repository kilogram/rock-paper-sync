# T5 anchor probe (re-record with anchor capture)

Replaces `07_t5_probe`: that recording captured the descender stroke's native
bounds but NOT its TreeNodeBlock anchor, so no page baseline could be derived
and T5 (baseline offset 20 vs 25) stayed open. This time extract_profile.py
parses TreeNodeBlock anchors (anchor_id.part2, anchor_origin_x), so one
stroke resolves T5 numerically, and three extra strokes measure how
anchor_origin_x relates to glyph x (the uniform -2.6 px inset question).

Operator steps:

1. Highlight `ZEBRA41` below.
2. Pen tip ON the underscores of the ZEBRA41 line, drag straight DOWN ~1 cm.
3. On the MARGIN line: one short vertical stroke in the LEFT margin (before
   the text), one ON the word MIDDLE, one in the RIGHT margin (after "end").

ZEBRA41 _________________________________

MARGIN left probe MIDDLE word right end
