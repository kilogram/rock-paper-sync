# T5 baseline probe

Spec item T5 is an unresolved contradiction: `DeviceGeometry.baseline_offset`
says 25.0, the renderer says 20. This probe settles it with a device-signed
measurement instead of a guess (principle P4).

The single short line below is the target. It does NOT wrap — keep it on one
visual line. `T5BASE` names the line; the underscores draw the baseline the
glyphs sit on.

What to do on the device (see the operator checklist in `record_corpus.py`):

1. Highlight the token `T5BASE`. Its glyph rectangle fixes this line's TOP edge
   and height.
2. With the pen, put the tip down right on the underscores (that visible
   underline IS the baseline) and drag straight DOWN about 1 cm, making one
   short vertical stroke. Anywhere along the underscores is fine — you only need
   one stroke, and only its starting (top) point matters.

`extract_profile.py` takes the stroke's top Y (the baseline) minus the
highlight rectangle's top Y (the line top); that difference is the true baseline
offset, resolving 20 vs 25.

T5BASE _________________________________
