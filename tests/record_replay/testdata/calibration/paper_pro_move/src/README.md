# Layout calibration corpus — source

Ground-truth corpus for the layout test bench (see `docs/LAYOUT_TESTBENCH_PLAN.md`
Phase 2, `docs/LAYOUT_SPEC.md` "Corpus contract").

Each markdown file exercises a specific set of spec items. Unique **sentinel
words** `ZEBRA01`…`ZEBRA40` are distributed across the corpus. During the device
session the operator highlights every sentinel; each on-device highlight stores
an absolute glyph rectangle (`SceneGlyphItemBlock`), giving a device-signed
statement of exactly where those characters landed (principle P2).

`tools/calibration/extract_profile.py` maps `sentinel id → char range → device
rect(s)` and derives measured layout values into `profile.json`.

## Files and the spec items they address

| File | Spec items | Sentinels |
|------|-----------|-----------|
| `01_wrapped_paragraphs.md` | W1, W2, W4, T4, E5 | ZEBRA01–ZEBRA10 |
| `02_headings.md` | T3, B2, B5 | ZEBRA11–ZEBRA17 |
| `03_lists.md` | B3 | ZEBRA18–ZEBRA26 |
| `04_code_blocks.md` | B4 | ZEBRA27–ZEBRA30 |
| `05_spacing_ladder.md` | B1, T2 | ZEBRA31–ZEBRA35 |
| `06_multipage.md` | P1g, P2g, B5 | ZEBRA36–ZEBRA40 |
| `07_t5_probe.md` | T5 (baseline offset) | handwritten descender, no highlight |

## Sentinel rules

- Every sentinel is a unique, greppable token of the form `ZEBRAnn`.
- Sentinels are placed to probe distinct positions: line starts, line ends near
  the wrap boundary, mid-line, indented list levels, headings, and content that
  straddles a page break.
- A sentinel is always surrounded by whitespace so the highlight covers exactly
  the `ZEBRAnn` glyphs and nothing else.
- Do not renumber or reflow sentinels between recordings; the corpus is
  append-mostly (P7). If a sentinel must move, note it in `profile.json`.
