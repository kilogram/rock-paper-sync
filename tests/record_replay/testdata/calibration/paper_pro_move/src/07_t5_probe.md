# T5 baseline probe

Spec item T5 is an unresolved contradiction: `DeviceGeometry.baseline_offset`
says 25.0, the renderer says 20. This probe settles it with a device-signed
measurement instead of a guess (principle P4).

Procedure (see the operator checklist in `record_corpus.py`):

1. The row of underscores below is a single body line. Its glyph rectangle
   (highlight it — sentinel `T5BASE`) fixes the line's top edge and height.
2. With the pen, draw a short vertical descender stroke starting exactly on the
   visible baseline of the underscores (the underscores sit *on* the baseline)
   and going downward, crossing the `T5BASE` marker so the stroke's anchor is
   unambiguous.
3. `extract_profile.py` compares the stroke's top Y (the baseline) against the
   highlight rectangle's top Y (the line top). The difference is the true
   baseline offset, resolving 20 vs 25.

T5BASE ________________________________________________________________________
